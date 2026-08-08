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
import subprocess
import sys
import time
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


def _git_sha() -> str:
    """Short SHA, never fatal — same contract as eval_run's E0 helper."""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, cwd=REPO, timeout=5)
        sha = r.stdout.strip()
        if sha:
            dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                   text=True, cwd=REPO, timeout=5).stdout.strip()
            return sha + ("-dirty" if dirty else "")
    except Exception:
        pass
    return "unknown"


def _set_version(golden_set: str) -> str:
    try:
        gs = json.loads((REPO / golden_set).read_text(encoding="utf-8"))
        return gs.get("metadata", {}).get("version", "—")
    except Exception:
        return "—"


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
        # Expected flag is derived from expected_behavior, so the fixture stays
        # correct as the set grows. `refuse` items ask for help with unlawful
        # acts and SHOULD flag illegal — spec §2.2 migrates that class from the
        # agent's AFVIS prompt section to the gate. Everything not in this map is
        # in-scope content that must raise NO flag; that subset is the
        # false-positive non-regression this fixture exists to protect.
        BEHAVIOR_TO_FLAG = {
            "refuse": "illegal",
            "out_of_scope": "non_tax",
            "pii_block": "pii",
        }
        cases = [{"id": i["id"], "question": i["question"],
                  "expect": BEHAVIOR_TO_FLAG.get(i.get("expected_behavior"))}
                 for i in items]
    else:
        raw = json.loads(Path(args.expect_file).read_text(encoding="utf-8"))
        cases = [{"id": c.get("id", f"case-{n:02d}"), "question": c["question"],
                  "expect": c.get("expect")} for n, c in enumerate(raw, 1)]

    print(f"Scope-classifier fixture — {len(cases)} case(s), model={SCOPE_CLASSIFIER_MODEL}")
    print("(no agent, no tools, no retrieval)\n")

    # E0 lesson (run-metadata stamping): a fixture record must say WHICH
    # classifier produced it. The model is swappable (gemini-3.5-flash-lite vs
    # ollama:gemma4:26b), so an unstamped baseline file cannot be compared to a
    # later one — the same trap E0 fixed for eval_run records.
    stamp = {
        "classifier_model": SCOPE_CLASSIFIER_MODEL,
        "git_sha": _git_sha(),
        "set_version": str(_set_version(args.golden_set)) if not args.expect_file else "—",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(f"stamp: {stamp['classifier_model']} · {stamp['git_sha']} · set {stamp['set_version']}\n")

    def run(case: dict) -> dict:
        verdict = classify_request(case["question"])
        got = scope_flag(verdict)
        return {**case, **stamp, "got": got, "reason": verdict.get("reason", ""),
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
        for flag in ("illegal", "non_tax", "pii"):
            grp = [r for r in results if r["expect"] == flag]
            if grp:
                hit = sum(1 for r in grp if r["got"] == flag)
                print(f"  {flag:<8} items blocked as expected: {hit}/{len(grp)}")
    print("=" * 62)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  written to {args.output}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
