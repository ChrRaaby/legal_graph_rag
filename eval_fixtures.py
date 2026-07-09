"""Zero-LLM retrieval fixture harness — level L0 of the lightweight eval ladder.

For every golden-set item with expected_legislation, call the retrieval tools
DIRECTLY (no agent loop, no generation, no judge — zero API credits) and check:

  1. §-recall  — does Contextual_Text_Retriever's output for the item's question
                 contain a row from the expected law + §?
  2. rate-recall — if the expected_answer states a percentage, does that
                 percentage appear in the retrieved text (retriever rows, with
                 Skattesats_Opslag as fallback probe)?

This is the C2/C4/C6 offline-probe technique formalized: the signal for
retrieval-layer changes lives here, and an ON/OFF fixture diff gives the
deterministic treatment footprint in ~a minute for free. It cannot measure
prompt/behavior changes — those still need agent cells (L1/L2).

Usage:
  .venv/bin/python3 eval_fixtures.py                     # full run
  .venv/bin/python3 eval_fixtures.py --output f.jsonl    # persist per-item rows
  C2_DIRECT_NARROW=off .venv/bin/python3 eval_fixtures.py   # probe an escape hatch
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Golden-set law abbreviations → title stems (extends app._CITATION_ALIASES,
# which is lowercase-keyed and lacks the golden set's MOMSL spelling).
_ABBREV_STEM = {
    "ll": "ligningslov", "psl": "personskattelov", "sel": "selskabsskattelov",
    "ksl": "kildeskattelov", "ml": "momslov", "momsl": "momslov",
    "abl": "aktieavancebeskatningslov", "kgl": "kursgevinstlov",
    "al": "afskrivningslov", "fbl": "fondsbeskatningslov",
    "askl": "aktiesparekontolov", "bal": "boafgiftslov",
}
# Laws expected by the golden set but not loaded in the graph (D1/D2 pending).
# Their §-recall is definitionally unreachable — reported separately, not failed.
_NOT_IN_GRAPH = {"bal"}

_PCT_RE = re.compile(r"\b(\d+(?:,\d+)?)\s*(?:pct\.?|%)", re.IGNORECASE)


def _norm_sec(s: str) -> str:
    # "16 A", "9 c", "2, stk. 1" → comparable § core
    return re.sub(r"\s+", " ", str(s).split(",")[0].strip()).upper()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden-set", default="eval_golden_set.json")
    ap.add_argument("--item-ids", default=None, help="comma-separated subset")
    ap.add_argument("--output", default=None, help="write per-item jsonl")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()

    import eval_run  # stubs streamlit, imports app

    golden = json.loads(Path(args.golden_set).read_text(encoding="utf-8"))
    items = golden.get("items", golden) if isinstance(golden, dict) else golden
    if args.item_ids:
        want = set(args.item_ids.split(","))
        items = [it for it in items if it["id"] in want]
    items = [it for it in items if it.get("expected_legislation")]

    print("Loading runtime (Neo4j + embeddings only — no LLM calls will be made)…")
    analysis, _agent, tools = eval_run.build_runtime_with_retry()
    by_name = {t.name: t for t in tools}
    retrieve = by_name["Contextual_Text_Retriever"].func
    sats = by_name.get("Skattesats_Opslag")
    sats_fn = sats.func if sats else None

    results = []
    n_sec_ok = n_sec = n_rate_ok = n_rate = 0
    for it in items:
        rows = retrieve(q=it["question"], k=args.k, limit=args.limit) or []
        row_text = " ".join(str(r.get("matched_text") or "") for r in rows if isinstance(r, dict))

        sec_checks = []
        for leg in it["expected_legislation"]:
            lov = str(leg.get("lov", "")).lower()
            stem = _ABBREV_STEM.get(lov, lov)
            alts = leg.get("paragraf", "")
            alts = alts if isinstance(alts, (list, tuple)) else [alts]
            if lov in _NOT_IN_GRAPH:
                sec_checks.append({"lov": leg.get("lov"), "paragraf": leg.get("paragraf"),
                                   "found": None, "note": "law not in graph (D1/D2)"})
                continue
            found = any(
                isinstance(r, dict)
                and stem in str(r.get("legislation_title") or "").lower()
                and _norm_sec(r.get("section_number") or "") in {_norm_sec(a) for a in alts if a}
                for r in rows
            )
            sec_checks.append({"lov": leg.get("lov"), "paragraf": leg.get("paragraf"), "found": found})
            n_sec += 1
            n_sec_ok += bool(found)

        exp_pcts = set(_PCT_RE.findall(it.get("expected_answer", "")))
        rate_check = None
        if exp_pcts:
            got = set(_PCT_RE.findall(row_text))
            missing = exp_pcts - got
            if missing and sats_fn is not None:
                sats_rows = sats_fn(emne=it["question"]) or []
                got |= set(_PCT_RE.findall(json.dumps(sats_rows, ensure_ascii=False)))
                missing = exp_pcts - got
            rate_check = {"expected_pcts": sorted(exp_pcts), "missing": sorted(missing)}
            n_rate += 1
            n_rate_ok += not missing
        rec = {"id": it["id"], "sec_checks": sec_checks, "rate_check": rate_check,
               "n_rows": len(rows)}
        results.append(rec)

        flags = "".join(
            "✓" if c["found"] else ("·" if c["found"] is None else "✗") for c in sec_checks
        )
        rflag = "" if rate_check is None else ("  rate✓" if not rate_check["missing"]
                                               else f"  rate✗ missing {rate_check['missing']}")
        print(f"  {it['id']}  §[{flags}]{rflag}")

    print("\n================ FIXTURE SUMMARY (zero LLM calls) ================")
    print(f"  §-recall   : {n_sec_ok}/{n_sec} expected (lov, §) found in retrieval")
    print(f"  rate-recall: {n_rate_ok}/{n_rate} items with all expected percentages retrievable")
    ungraphed = sum(1 for r in results for c in r["sec_checks"] if c["found"] is None)
    if ungraphed:
        print(f"  (excluded: {ungraphed} checks against laws not in the graph)")
    if args.output:
        import os
        stamp = {"git_sha": eval_run._git_sha(),
                 "env": {k: v for k, v in os.environ.items() if re.match(r"C\d+_", k)}}
        with open(args.output, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps({**r, **stamp}, ensure_ascii=False) + "\n")
        print(f"  written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
