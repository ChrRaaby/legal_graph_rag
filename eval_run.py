#!/usr/bin/env python3
"""
eval_run.py — Headless evaluation runner for the Danish Tax Law GraphRAG agent.

Implements both Black Box (output quality) and Glass Box (trajectory) evaluation
inspired by Google's Agent Quality framework.

Usage:
  python eval_run.py
  python eval_run.py --items 5
  python eval_run.py --item-ids gs-001,gs-007
  python eval_run.py --llm gemini-flash --workers 5
  python eval_run.py --failing-only
  python eval_run.py --judge --output eval_results_judged.jsonl
  python eval_run.py --golden-set my_set.json --no-log

Scoring per item:
  - must_contain:     all required terms appear in answer (case-insensitive)
  - must_not_contain: no forbidden terms appear in answer
  - behavior_match:   detected behavior matches expected_behavior
  - citation_pass:    expected § references appear in answer
  - overall_pass:     all four pass

Optional LLM-as-a-Judge adds: faktuel_korrekthed, fuldstaendighed,
  praecision, robusthed (each 1-5, with begrundelse).
"""

import argparse
import json
import os
import re
import sys
import time
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

# ── Parse --llm before env/import so we can override env vars first ───────────
def _parse_llm_arg() -> str:
    """Quick pre-parse for --llm only, before full argparse and app import."""
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--llm" and i + 1 < len(sys.argv) - 1:
            return sys.argv[i + 2]
        if arg.startswith("--llm="):
            return arg.split("=", 1)[1]
    return "auto"

_llm_override = _parse_llm_arg()
if _llm_override == "gemini-flash":
    os.environ.pop("OLLAMA_MODEL", None)
    os.environ["LLM_PROVIDER"] = "gemini"
elif _llm_override == "ollama":
    os.environ["LLM_PROVIDER"] = "ollama"
elif _llm_override == "openai":
    os.environ.pop("OLLAMA_MODEL", None)
    os.environ["LLM_PROVIDER"] = "openai"
# "auto" → leave env as-is

# ── Mock streamlit before importing app ──────────────────────────────────────
_st = MagicMock()
_st.cache_resource = lambda **kwargs: (lambda f: f)
# st.columns(n) must return a list of n MagicMock objects so tuple-unpacking works
_st.columns = lambda spec, **kw: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
# st.stop() is called by app.py when Neo4j init fails — make it actually stop
_st.stop = lambda: sys.exit("Neo4j initialization failed — check credentials and DB status.")
sys.modules.update({
    "streamlit": _st,
    "streamlit.components": _st,
    "streamlit.components.v1": _st,
})

import logging
logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")

sys.path.insert(0, str(Path(__file__).parent))
from app import build_runtime, stream_agent_answer, log_trajectory  # noqa: E402

# ── Behavior detection ────────────────────────────────────────────────────────
# Signal phrases mapped to expected_behavior values. Matched case-insensitively.
BEHAVIOR_SIGNALS: dict[str, list[str]] = {
    "refuse": [
        "vil jeg ikke",
        "hjælper ikke med",
        "kan ikke hjælpe",
        "ikke etisk",
        "ulovlig",
        "skatteunddragelse",
        "etisk forsvarligt",
        "ikke rådgive om",
    ],
    "clarify": [
        "kan du oplyse",
        "hvad mener du",
        "hvilken situation",
        "mere information",
        "præcisere spørgsmålet",
        "uddybe",
        "angive om",
    ],
    "correct_premise": [
        "præmissen er forkert",
        "nej, det er ikke rigtigt",
        "ingen formueskat",
        "afskaffet",
        "det er forkert",
        "ikke korrekt",
        "eksisterer ikke i dansk",
        "der er ingen",
        "er ikke et begreb",
        "er ikke tilfældet",
    ],
    "admit_unknown": [
        "kan ikke finde",
        "findes ikke i",
        "eksisterer ikke",
        "ingen §",
        "ingen paragraf",
        "ikke i min knowledge",
        "ikke tilgængeligt",
    ],
}


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).upper()
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "QUOTA" in msg


def _normalize(text: str) -> str:
    """Normalize text for must_contain matching to tolerate formatting variants.

    Handles:
    - "25 %" → "25%"       (space before percent sign)
    - "27 pct." → "27%"    (Danish law uses pct. instead of %)
    - "67.500" / "67,500" → "67500"  (thousands separators interchangeable)
    """
    text = text.lower()
    text = re.sub(r'\bpct\.', '%', text)        # "27 pct." → "27 %"
    text = re.sub(r'\s+%', '%', text)           # "27 %" → "27%"
    text = re.sub(r'(\d)[.,](\d{3})(?!\d)', r'\1\2', text)
    return text


def detect_behavior(answer: str) -> str:
    lower = answer.lower()
    for behavior, signals in BEHAVIOR_SIGNALS.items():
        if any(sig in lower for sig in signals):
            return behavior
    return "answer"


def score_item(item: dict, answer: str, tool_events: list) -> dict:
    """Compute automated Black Box + Glass Box scores for one golden-set item."""
    answer_lower = answer.lower()
    answer_norm = _normalize(answer)
    must_contain = item.get("must_contain") or []
    must_not_contain = item.get("must_not_contain") or []

    mc_details = {term: _normalize(term) in answer_norm for term in must_contain}
    mnc_details = {term: _normalize(term) not in answer_norm for term in must_not_contain}
    mc_pass = all(mc_details.values()) if mc_details else True
    mnc_pass = all(mnc_details.values()) if mnc_details else True

    detected_behavior = detect_behavior(answer)
    behavior_match = detected_behavior == item.get("expected_behavior", "answer")

    # Glass Box: tool trajectory
    tool_calls = [e for e in tool_events if e["type"] == "tool_call"]
    tool_sequence = [e["tool_name"] for e in tool_calls]

    # Citation check: expected § paragraphs appear in answer
    expected_legislation = item.get("expected_legislation") or []
    citation_checks = []
    for leg in expected_legislation:
        paragraf = leg.get("paragraf", "")
        lov = leg.get("lov", "")
        found = bool(paragraf) and (
            f"§ {paragraf}" in answer or f"§{paragraf}" in answer
        )
        citation_checks.append({"lov": lov, "paragraf": paragraf, "found": found})
    citation_pass = all(c["found"] for c in citation_checks) if citation_checks else True

    overall_pass = mc_pass and mnc_pass and behavior_match and citation_pass

    return {
        "must_contain_pass": mc_pass,
        "must_contain_details": mc_details,
        "must_not_contain_pass": mnc_pass,
        "must_not_contain_details": mnc_details,
        "expected_behavior": item.get("expected_behavior"),
        "detected_behavior": detected_behavior,
        "behavior_match": behavior_match,
        "expected_legislation_check": citation_checks,
        "citation_pass": citation_pass,
        "tool_call_count": len(tool_calls),
        "tool_sequence": tool_sequence,
        "overall_pass": overall_pass,
    }


def llm_judge(item: dict, answer: str, judge_llm) -> dict:
    """LLM-as-a-Judge: score answer on four Danish rubric dimensions (1-5)."""
    prompt = f"""Du er en ekspert i dansk skattelovgivning og evaluerer svar fra en AI-assistent.

SPØRGSMÅL: {item["question"]}

FORVENTET SVAR (referencesvar): {item.get("expected_answer", "Ikke angivet")}

FAKTISK SVAR FRA AI: {answer}

Bedøm det faktiske svar på fire dimensioner, hver fra 1 (dårligst) til 5 (bedst):

1. faktuel_korrekthed — Er de nævnte lovhenvisninger, satser og regler korrekte?
2. fuldstaendighed — Dækkes alle nødvendige aspekter fra referencen?
3. praecision — Er svaret præcist og undgår det irrelevant eller forvirrende indhold?
4. robusthed — Håndteres fejlagtige præmisser eller ulovlige spørgsmål korrekt?

Svar KUN i dette JSON-format (ingen forklaring udenfor JSON):
{{
  "faktuel_korrekthed": <1-5>,
  "fuldstaendighed": <1-5>,
  "praecision": <1-5>,
  "robusthed": <1-5>,
  "begrundelse": "<kort begrundelse på dansk, max 2 sætninger>"
}}"""

    try:
        response = judge_llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            scores = json.loads(match.group())
            dims = ["faktuel_korrekthed", "fuldstaendighed", "praecision", "robusthed"]
            valid = [scores.get(d, 0) for d in dims if isinstance(scores.get(d), (int, float))]
            scores["judge_total"] = round(sum(valid) / len(valid), 2) if valid else 0.0
            return scores
    except Exception as exc:
        return {"error": str(exc)}
    return {}


def print_summary(results: list[dict]) -> None:
    total = len(results)
    if total == 0:
        print("No results.")
        return

    passed = sum(1 for r in results if r["scores"]["overall_pass"])
    pct = 100 * passed // total

    print(f"\n{'='*64}")
    print(f"EVAL SUMMARY  —  {passed}/{total} passed  ({pct}%)")
    print(f"{'='*64}")

    by_cat: dict[str, dict] = defaultdict(lambda: {"pass": 0, "total": 0})
    by_pillar: dict[str, dict] = defaultdict(lambda: {"pass": 0, "total": 0})
    for r in results:
        cat = r["item"]["category"]
        pillar = r["item"]["pillar"]
        by_cat[cat]["total"] += 1
        by_pillar[pillar]["total"] += 1
        if r["scores"]["overall_pass"]:
            by_cat[cat]["pass"] += 1
            by_pillar[pillar]["pass"] += 1

    print("\nBy category:")
    for cat, c in sorted(by_cat.items()):
        p = 100 * c["pass"] // c["total"]
        bar = "█" * c["pass"] + "░" * (c["total"] - c["pass"])
        print(f"  {cat:<22} {c['pass']}/{c['total']}  {p:3d}%  {bar}")

    print("\nBy pillar:")
    for pillar, c in sorted(by_pillar.items()):
        p = 100 * c["pass"] // c["total"]
        print(f"  {pillar:<22} {c['pass']}/{c['total']}  {p:3d}%")

    failures = [r for r in results if not r["scores"]["overall_pass"]]
    if failures:
        print(f"\nFailed items ({len(failures)}):")
        for r in failures:
            item = r["item"]
            sc = r["scores"]
            reasons = []
            if not sc["must_contain_pass"]:
                missing = [k for k, v in sc["must_contain_details"].items() if not v]
                reasons.append(f"must_contain missing: {missing}")
            if not sc["must_not_contain_pass"]:
                bad = [k for k, v in sc["must_not_contain_details"].items() if not v]
                reasons.append(f"must_not_contain present: {bad}")
            if not sc["behavior_match"]:
                reasons.append(
                    f"behavior: expected={sc['expected_behavior']} "
                    f"detected={sc['detected_behavior']}"
                )
            if not sc["citation_pass"]:
                missing_c = [c for c in sc["expected_legislation_check"] if not c["found"]]
                reasons.append(f"citations missing: {missing_c}")
            print(f"  [{item['id']}] {item['question'][:60]}")
            for reason in reasons:
                print(f"         → {reason}")

    judged = [r for r in results if r.get("judge_scores", {}).get("judge_total") is not None]
    if judged:
        avg = sum(r["judge_scores"]["judge_total"] for r in judged) / len(judged)
        print(f"\nLLM-Judge avg: {avg:.2f}/5.0  ({len(judged)} items judged)")

    print()


def check_connectivity() -> bool:
    """Check Neo4j, LLM API, and embedding model before loading the full runtime.

    Prints one line per service and returns True only if all pass.
    """
    from dotenv import load_dotenv
    load_dotenv()
    all_ok = True

    # ── Neo4j ────────────────────────────────────────────────────────────────
    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE", "neo4j")

    print("  Neo4j …      ", end="", flush=True)
    if not (uri and user and password):
        print("FAIL  (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD not set in .env)")
        all_ok = False
    else:
        try:
            from neo4j import GraphDatabase
            t0 = time.perf_counter()
            driver = GraphDatabase.driver(uri, auth=(user, password))
            with driver.session(database=database) as session:
                session.run("RETURN 1").consume()
            driver.close()
            print(f"OK  ({round(time.perf_counter() - t0, 2)}s)")
        except Exception as exc:
            print(f"FAIL  ({exc})")
            all_ok = False

    # ── LLM ──────────────────────────────────────────────────────────────────
    ollama_model = os.getenv("OLLAMA_MODEL")
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://172.21.64.1:11434")
    google_key = os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    print("  LLM …        ", end="", flush=True)
    if not (ollama_model or google_key or openai_key):
        print("FAIL  (set OLLAMA_MODEL, GOOGLE_API_KEY, or OPENAI_API_KEY in .env)")
        all_ok = False
    else:
        try:
            t0 = time.perf_counter()
            if ollama_model:
                from langchain_ollama import ChatOllama
                llm = ChatOllama(model=ollama_model, base_url=ollama_base_url, temperature=0)
                label = f"ollama/{ollama_model}"
            elif google_key:
                from langchain_google_genai import ChatGoogleGenerativeAI
                llm = ChatGoogleGenerativeAI(
                    model="gemini-2.5-flash", temperature=0, api_key=google_key
                )
                label = "gemini-2.5-flash"
            else:
                from langchain_openai import ChatOpenAI
                llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=openai_key)
                label = "gpt-4o-mini"
            llm.invoke("Reply with the single word: OK")
            print(f"OK  ({label}, {round(time.perf_counter() - t0, 2)}s)")
        except Exception as exc:
            if _is_quota_error(exc):
                print(f"FAIL  (quota exhausted — check your plan/billing for {label})")
            else:
                print(f"FAIL  ({exc})")
            all_ok = False

    # ── Embeddings ───────────────────────────────────────────────────────────
    print("  Embeddings … ", end="", flush=True)
    try:
        t0 = time.perf_counter()
        from langchain_huggingface import HuggingFaceEmbeddings
        emb = HuggingFaceEmbeddings(
            model_name="intfloat/multilingual-e5-large",
            cache_folder=str(Path(__file__).parent.parent / "models"),
            encode_kwargs={"normalize_embeddings": True},
        )
        emb.embed_query("test")
        print(f"OK  ({round(time.perf_counter() - t0, 2)}s)")
    except Exception as exc:
        print(f"FAIL  ({exc})")
        all_ok = False

    return all_ok


def build_judge_llm():
    """Build a standalone LLM for use as judge (same priority as agent LLM)."""
    from dotenv import load_dotenv
    load_dotenv()
    ollama_model = os.getenv("OLLAMA_MODEL")
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://172.21.64.1:11434")
    google_key = os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    if ollama_model:
        from langchain_ollama import ChatOllama
        return ChatOllama(model=ollama_model, base_url=ollama_base_url, temperature=0)
    elif google_key:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0, api_key=google_key)
    elif openai_key:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=openai_key)
    raise RuntimeError("No LLM configured (set OLLAMA_MODEL, GOOGLE_API_KEY, or OPENAI_API_KEY)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Headless evaluation of the Danish Tax Law GraphRAG agent"
    )
    parser.add_argument(
        "--golden-set",
        default="eval_golden_set.json",
        help="Path to golden set JSON (relative to this file or absolute)",
    )
    parser.add_argument(
        "--items",
        type=int,
        default=None,
        metavar="N",
        help="Run only the first N items",
    )
    parser.add_argument(
        "--item-ids",
        default=None,
        metavar="IDS",
        help="Comma-separated item IDs to run, e.g. gs-001,gs-007",
    )
    parser.add_argument(
        "--output",
        default="eval_results.jsonl",
        help="Output JSONL file for per-item results",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Enable LLM-as-a-Judge scoring (adds latency and API cost)",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Skip appending trajectories to eval_log.jsonl",
    )
    parser.add_argument(
        "--llm",
        default="auto",
        choices=["auto", "gemini-flash", "ollama", "openai"],
        help="LLM override: gemini-flash | ollama | openai | auto (uses env vars)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Parallel workers (default 1). Use >1 with API-backed LLMs (gemini-flash, openai).",
    )
    parser.add_argument(
        "--failing-only",
        action="store_true",
        help="Re-run only items that failed in the previous --output file",
    )
    args = parser.parse_args()

    golden_path = Path(args.golden_set)
    if not golden_path.is_absolute():
        golden_path = Path(__file__).parent / args.golden_set
    with open(golden_path, encoding="utf-8") as f:
        golden = json.load(f)
    items = golden["items"]

    if args.item_ids:
        ids = set(args.item_ids.split(","))
        items = [it for it in items if it["id"] in ids]
    if args.items is not None:
        items = items[: args.items]

    # --failing-only: keep only items that failed in the previous output file
    if args.failing_only:
        output_path_prev = Path(args.output)
        if not output_path_prev.is_absolute():
            output_path_prev = Path(__file__).parent / args.output
        if output_path_prev.exists():
            passed_ids: set[str] = set()
            for line in output_path_prev.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("scores", {}).get("overall_pass"):
                    passed_ids.add(r["item"]["id"])
            before = len(items)
            items = [it for it in items if it["id"] not in passed_ids]
            print(f"--failing-only: skipping {before - len(items)} passed item(s), re-running {len(items)}.\n")
        else:
            print("--failing-only: no previous output file found, running all items.\n")

    if not items:
        print("No items to run.")
        return

    print("Connectivity checks…")
    if not check_connectivity():
        print("\nAbort: fix the failing services above, then re-run.")
        sys.exit(1)
    print()

    print("Loading agent runtime…")
    analysis, agent_executor, _tools = build_runtime()

    judge_llm = None
    if args.judge:
        print("Initializing LLM judge…")
        judge_llm = build_judge_llm()

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path(__file__).parent / args.output
    # Truncate output file at start of run (fresh run)
    output_path.write_text("", encoding="utf-8")

    workers = max(1, args.workers)
    results: list[dict] = []
    _print_lock = threading.Lock()
    _file_lock = threading.Lock()
    total = len(items)
    print(f"Running {total} item(s)  [workers={workers}  llm={_llm_override}]…\n")

    def _run_item(indexed_item: tuple[int, dict]) -> dict:
        i, item = indexed_item
        question = item["question"]
        chat_messages = [{"role": "user", "content": question}]
        t0 = time.perf_counter()
        try:
            answer, tool_events = stream_agent_answer(agent_executor, chat_messages)
        except Exception as exc:
            answer = f"[ERROR: {exc}]"
            tool_events = []
        latency = round(time.perf_counter() - t0, 3)

        scores = score_item(item, answer, tool_events)
        result: dict = {"item": item, "answer": answer, "latency_s": latency, "scores": scores}

        if judge_llm is not None:
            result["judge_scores"] = llm_judge(item, answer, judge_llm)

        if not args.no_log:
            log_trajectory(question, answer, tool_events, latency)

        with _file_lock:
            with open(output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")

        status = "✓" if scores["overall_pass"] else "✗"
        judge_line = ""
        if "judge_scores" in result and "judge_total" in result.get("judge_scores", {}):
            judge_line = f"  judge={result['judge_scores']['judge_total']:.1f}/5"
        lines = [f"[{i:2d}/{total}] {item['id']}  {question[:70]}"]
        lines.append(
            f"       {status}  latency={latency}s  "
            f"tools={scores['tool_call_count']}  pass={scores['overall_pass']}{judge_line}"
        )
        if not scores["overall_pass"]:
            if not scores["must_contain_pass"]:
                missing = [k for k, v in scores["must_contain_details"].items() if not v]
                lines.append(f"         must_contain missing: {missing}")
            if not scores["must_not_contain_pass"]:
                bad = [k for k, v in scores["must_not_contain_details"].items() if not v]
                lines.append(f"         must_not_contain present: {bad}")
            if not scores["behavior_match"]:
                lines.append(
                    f"         behavior: expected={scores['expected_behavior']} "
                    f"detected={scores['detected_behavior']}"
                )
            if not scores["citation_pass"]:
                missing_c = [c for c in scores["expected_legislation_check"] if not c["found"]]
                lines.append(f"         citations missing: {missing_c}")
        with _print_lock:
            print("\n".join(lines) + "\n")

        # Bubble up quota errors so the executor can abort
        if "RESOURCE_EXHAUSTED" in answer or "429" in answer:
            raise RuntimeError(f"LLM quota exhausted on item {item['id']}")

        return result

    indexed = list(enumerate(items, 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_item, ix): ix for ix in indexed}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except RuntimeError as exc:
                if "quota" in str(exc).lower():
                    print(f"\nAbort: {exc}")
                    pool.shutdown(wait=False, cancel_futures=True)
                    sys.exit(1)
                # Other errors already embedded in the result; non-fatal

    print_summary(results)
    print(f"Results written to: {output_path}")


if __name__ == "__main__":
    main()
