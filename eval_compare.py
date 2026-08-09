#!/usr/bin/env python3
"""
eval_compare.py — Side-by-side model comparison for the Danish Tax Law GraphRAG agent.

Usage:
  python eval_compare.py
  python eval_compare.py --models gemma4:26b,gemma4:31b --items 5
  python eval_compare.py --item-ids gs-001,gs-007,gs-012
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# app.py is pure runtime since 2026-08-08 — no Streamlit stub needed.

import logging
logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")

sys.path.insert(0, str(Path(__file__).parent))

# Import shared scoring helpers (single-sourced in app.py since A1).
from eval_run import score_item, _is_quota_error  # noqa: E402
from app import build_runtime, stream_agent_answer  # noqa: E402


# ── Connectivity check ───────────────────────────────────────────────────────

def check_connectivity(models: list[str]) -> bool:
    from dotenv import load_dotenv
    load_dotenv()
    all_ok = True
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://172.21.64.1:11434")

    # Neo4j
    uri, user, pw, db = (
        os.getenv("NEO4J_URI"), os.getenv("NEO4J_USER"),
        os.getenv("NEO4J_PASSWORD"), os.getenv("NEO4J_DATABASE", "neo4j"),
    )
    print("  Neo4j …        ", end="", flush=True)
    if not (uri and user and pw):
        print("FAIL  (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD not set)")
        all_ok = False
    else:
        try:
            from neo4j import GraphDatabase
            t0 = time.perf_counter()
            drv = GraphDatabase.driver(uri, auth=(user, pw))
            with drv.session(database=db) as s:
                s.run("RETURN 1").consume()
            drv.close()
            print(f"OK  ({round(time.perf_counter()-t0, 2)}s)")
        except Exception as exc:
            print(f"FAIL  ({exc})")
            all_ok = False

    # Each model
    from langchain_ollama import ChatOllama
    for model in models:
        print(f"  ollama/{model:<16} ", end="", flush=True)
        try:
            t0 = time.perf_counter()
            llm = ChatOllama(model=model, base_url=ollama_base, temperature=0)
            llm.invoke("Reply with the single word: OK")
            print(f"OK  ({round(time.perf_counter()-t0, 2)}s)")
        except Exception as exc:
            print(f"FAIL  ({exc})")
            all_ok = False

    # Embeddings
    print("  Embeddings …   ", end="", flush=True)
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        t0 = time.perf_counter()
        emb = HuggingFaceEmbeddings(
            model_name="intfloat/multilingual-e5-large",
            cache_folder=str(Path(__file__).parent.parent / "models"),
            encode_kwargs={"normalize_embeddings": True},
        )
        emb.embed_query("test")
        print(f"OK  ({round(time.perf_counter()-t0, 2)}s)")
    except Exception as exc:
        print(f"FAIL  ({exc})")
        all_ok = False

    return all_ok


# ── Run one model over items ─────────────────────────────────────────────────

def run_model(model: str, items: list[dict]) -> list[dict]:
    """Set OLLAMA_MODEL, build runtime, run items, return per-item result dicts."""
    os.environ["OLLAMA_MODEL"] = model
    print(f"\nLoading runtime for {model}…")
    _, agent_executor = build_runtime()

    results = []
    for i, item in enumerate(items, 1):
        question = item["question"]
        print(f"  [{i:2d}/{len(items)}] {item['id']}  {question[:60]}", end="", flush=True)
        t0 = time.perf_counter()
        try:
            answer, tool_events = stream_agent_answer(agent_executor, [{"role": "user", "content": question}])
        except Exception as exc:
            if _is_quota_error(exc):
                print(f"\nAbort: quota error on {model}")
                sys.exit(1)
            answer = f"[ERROR: {exc}]"
            tool_events = []
        latency = round(time.perf_counter() - t0, 3)
        scores = score_item(item, answer, tool_events)
        status = "✓" if scores["overall_pass"] else "✗"
        print(f"  {status}  {latency}s  tools={scores['tool_call_count']}")
        results.append({"item": item, "answer": answer, "latency_s": latency, "scores": scores})
    return results


# ── Report ───────────────────────────────────────────────────────────────────

def _fail_reasons(scores: dict) -> list[str]:
    reasons = []
    if not scores["must_contain_pass"]:
        missing = [k for k, v in scores["must_contain_details"].items() if not v]
        reasons.append(f"must_contain: {missing}")
    if not scores["must_not_contain_pass"]:
        bad = [k for k, v in scores["must_not_contain_details"].items() if not v]
        reasons.append(f"must_not_contain: {bad}")
    if not scores["behavior_match"]:
        reasons.append(f"behavior exp={scores['expected_behavior']} got={scores['detected_behavior']}")
    if not scores["citation_pass"]:
        mc = [c for c in scores["expected_legislation_check"] if not c["found"]]
        reasons.append(f"citations: {mc}")
    return reasons


def print_report(models: list[str], all_results: dict[str, list[dict]]) -> None:
    items = all_results[models[0]]
    n = len(items)
    col = 18  # model name column width

    print("\n" + "=" * 72)
    print(f"COMPARISON REPORT  —  {n} item(s)")
    print("  " + "  vs  ".join(models))
    print("=" * 72)

    # Per-item rows
    for idx in range(n):
        item = items[idx]["item"]
        print(f"\n[{item['id']}] {item['question'][:65]}")
        for model in models:
            r = all_results[model][idx]
            sc = r["scores"]
            status = "✓" if sc["overall_pass"] else "✗"
            line = (
                f"  {model:<{col}}  {status}  {r['latency_s']:6.1f}s  "
                f"tools={sc['tool_call_count']}"
            )
            print(line)
            if not sc["overall_pass"]:
                for reason in _fail_reasons(sc):
                    print(f"  {'':{col}}       → {reason}")

    # Summary table
    print("\n" + "-" * 72)
    header = f"  {'metric':<20}" + "".join(f"  {m:<{col}}" for m in models)
    print(header)
    print("-" * 72)

    passes = {m: sum(1 for r in all_results[m] if r["scores"]["overall_pass"]) for m in models}
    latencies = {m: [r["latency_s"] for r in all_results[m]] for m in models}
    tools = {m: [r["scores"]["tool_call_count"] for r in all_results[m]] for m in models}

    row_pass = f"  {'pass rate':<20}" + "".join(
        f"  {passes[m]}/{n} ({100*passes[m]//n:3d}%){'':<{col-11}}" for m in models
    )
    row_lat = f"  {'avg latency':<20}" + "".join(
        f"  {sum(latencies[m])/n:6.1f}s{'':<{col-8}}" for m in models
    )
    row_tools = f"  {'avg tools':<20}" + "".join(
        f"  {sum(tools[m])/n:4.1f}{'':<{col-4}}" for m in models
    )

    winner_pass = max(models, key=lambda m: passes[m])
    winner_lat = min(models, key=lambda m: sum(latencies[m]))

    print(row_pass)
    print(row_lat)
    print(row_tools)
    print("-" * 72)
    print(f"\n  Best pass rate:  {winner_pass}")
    print(f"  Fastest (avg):   {winner_lat}")
    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two Ollama models on the golden eval set")
    parser.add_argument("--models", default="gemma4:26b,gemma4:31b",
                        help="Comma-separated Ollama model names (default: gemma4:26b,gemma4:31b)")
    parser.add_argument("--items", type=int, default=5, metavar="N",
                        help="Number of golden-set items to run (default: 5)")
    parser.add_argument("--item-ids", default=None, metavar="IDS",
                        help="Comma-separated item IDs, e.g. gs-001,gs-007")
    parser.add_argument("--golden-set", default="eval_golden_set.json")
    parser.add_argument("--output", default="eval_compare_results.json",
                        help="JSON file to write full results to")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",")]

    from dotenv import load_dotenv
    load_dotenv()

    golden_path = Path(args.golden_set)
    if not golden_path.is_absolute():
        golden_path = Path(__file__).parent / args.golden_set
    with open(golden_path, encoding="utf-8") as f:
        golden = json.load(f)
    items = golden["items"]

    if args.item_ids:
        ids = set(args.item_ids.split(","))
        items = [it for it in items if it["id"] in ids]
    items = items[: args.items]

    if not items:
        print("No items selected.")
        return

    print(f"Connectivity checks ({', '.join(models)})…")
    if not check_connectivity(models):
        print("\nAbort: fix failing services above, then re-run.")
        sys.exit(1)

    all_results: dict[str, list[dict]] = {}
    for model in models:
        all_results[model] = run_model(model, items)

    print_report(models, all_results)

    out = Path(args.output)
    if not out.is_absolute():
        out = Path(__file__).parent / out
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"models": models, "items": len(items), "results": all_results}, f,
                  ensure_ascii=False, indent=2)
    print(f"Full results written to: {out}")


if __name__ == "__main__":
    main()
