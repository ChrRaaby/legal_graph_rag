#!/usr/bin/env python3
"""Classify-only fixture for the F1 scope gate — the L0-equivalent rung.

Runs `classify_request` over golden-set questions with NO agent, NO tools and no
Neo4j retrieval: ~1 cheap classifier call per item. Its job is the false-positive
non-regression — **every existing golden item must raise zero flags** — so it is
the cheap gate to re-run after any edit to the classifier prompt.

  .venv/bin/python3 eval_scope_fixtures.py                     # all items
  .venv/bin/python3 eval_scope_fixtures.py --item-ids gs-001,gs-026
  .venv/bin/python3 eval_scope_fixtures.py --expect-file scratchpad/f1_scope_cases.json

--expect-file takes [{"question": ..., "expect": "pii|illegal|non_tax|null"}, ...]
for the guardrail cases that are not (yet) golden items.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

# Stub streamlit before importing app (same pattern as eval_run.py).
_st = MagicMock()
_st.cache_resource = lambda **kwargs: (lambda f: f)
_st.columns = lambda spec, **kw: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
_st.stop = lambda: sys.exit("Neo4j initialization failed")
sys.modules.update({
    "streamlit": _st, "streamlit.components": _st, "streamlit.components.v1": _st,
})

import logging  # noqa: E402
logging.getLogger("neo4j").setLevel(logging.ERROR)

from app import classify_request, scope_flag, SCOPE_CLASSIFIER_MODEL  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden-set", default="eval_golden_set.json")
    ap.add_argument("--item-ids", help="comma-separated subset")
    ap.add_argument("--expect-file", help="extra cases with explicit expectations")
    ap.add_argument("--output", help="write per-case jsonl")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cases: list[dict] = []
    if not args.expect_file:
        gs = json.loads((REPO / args.golden_set).read_text(encoding="utf-8"))
        items = gs["items"] if isinstance(gs, dict) and "items" in gs else gs
        if args.item_ids:
            wanted = {s.strip() for s in args.item_ids.split(",")}
            items = [i for i in items if i["id"] in wanted]
        # Expected flag per item: the 4 `refuse` items ask for help with unlawful
        # acts, so the gate SHOULD flag them illegal — spec §2.2 migrates that
        # class from the agent's AFVIS prompt section to the gate. Every other
        # golden item is in-scope tax content and must raise NO flag; that subset
        # is the false-positive non-regression this fixture exists to protect.
        cases = [{"id": i["id"], "question": i["question"],
                  "expect": "illegal" if i.get("expected_behavior") == "refuse" else None}
                 for i in items]
    else:
        raw = json.loads(Path(args.expect_file).read_text(encoding="utf-8"))
        cases = [{"id": c.get("id", f"case-{n:02d}"), "question": c["question"],
                  "expect": c.get("expect")} for n, c in enumerate(raw, 1)]

    print(f"Scope-classifier fixture — {len(cases)} case(s), model={SCOPE_CLASSIFIER_MODEL}")
    print("(no agent, no tools, no retrieval)\n")

    def run(case: dict) -> dict:
        verdict = classify_request(case["question"])
        got = scope_flag(verdict)
        return {**case, "got": got, "reason": verdict.get("reason", ""),
                "error": verdict.get("error"), "pass": got == case["expect"]}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        results = list(ex.map(run, cases))
    results.sort(key=lambda r: r["id"])

    failures = [r for r in results if not r["pass"]]
    errors = [r for r in results if r.get("error")]
    for r in results:
        if not r["pass"]:
            print(f"  FLAG  {r['id']}  expect={r['expect']} got={r['got']}"
                  f"  {r['question'][:52]}\n        reason: {r['reason'][:96]}")
    if errors:
        print(f"\n  {len(errors)} classifier error(s) — these fail OPEN in production:")
        for r in errors[:5]:
            print(f"    {r['id']}: {r['error'][:110]}")

    print(f"\n{'='*62}")
    print(f"  {len(results) - len(failures)}/{len(results)} as expected"
          f"   ({len(failures)} unexpected, {len(errors)} errors)")
    if not args.expect_file:
        inscope = [r for r in results if r["expect"] is None]
        fp = [r for r in inscope if r["got"]]
        print(f"  FALSE-POSITIVE NON-REGRESSION: {len(inscope) - len(fp)}/{len(inscope)} "
              f"in-scope items raised no flag" + ("  ← REGRESSION" if fp else "  ✓"))
        refuse = [r for r in results if r["expect"] == "illegal"]
        if refuse:
            hit = sum(1 for r in refuse if r["got"] == "illegal")
            print(f"  refuse-class migration: {hit}/{len(refuse)} flagged illegal by the gate")
    print("=" * 62)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  written to {args.output}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
