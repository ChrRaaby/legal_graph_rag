"""G4 — tool usage census over saved eval runs.

Re-runs the method C3 used (backlog §194-196: a census over 2,301 saved
item-runs that cut 15 tools to 12), against the current tool set.

Two rules this script exists to respect, both learned the hard way:

  1. Count PER SUBSTRATE, never pooled. C3's sharpest finding was that the
     substrates disagree — flash never touched the pruned tools while gemma made
     most of the low-quality Semantic_Search calls. A pooled count hides that.

  2. A zero-call tool is not automatically a useless tool. "The tool earns
     nothing" and "the model never picks a tool that would help" look identical
     in the counts, and C1b is a live case of the second (the citation tool is
     correct; flash simply never invokes it). This script reports the counts and
     refuses to recommend removal — that decision needs the split above plus a
     matched pair, because tool schemas change every LLM request.

Read-only. Costs nothing. Usage:  .venv/bin/python3 scratchpad/g4_tool_census.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HISTORY = ROOT / "eval_history"


def substrate_of(rec: dict, filename: str) -> str:
    """Same precedence the server uses: explicit model, then provider (which is
    only self-describing for gemini), then the filename."""
    if rec.get("model"):
        return rec["model"]
    provider = rec.get("provider") or ""
    if ":" in provider:
        return provider.split(":", 1)[1]
    low = filename.lower()
    for tok, model in (("26b", "gemma4:26b"), ("31b", "gemma4:31b"), ("12b", "gemma4:12b")):
        if tok in low:
            return model
    if "gemma" in low:
        return "gemma4:26b"
    if "flash" in low:
        return "gemini-2.5-flash"
    return f"{provider or 'ukendt'} (umærket)"


def main() -> int:
    files = sorted(HISTORY.glob("eval_results_*.jsonl"))
    if not files:
        print("no eval_results_*.jsonl under eval_history/")
        return 1

    # substrate -> tool -> [calls, items_calling, items_calling_that_passed]
    per_sub: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    sub_items: dict[str, int] = defaultdict(int)
    sub_passes: dict[str, int] = defaultdict(int)
    sub_files: dict[str, set[str]] = defaultdict(set)
    records = 0

    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            scores = rec.get("scores") or {}
            if not isinstance(scores, dict):
                continue
            sub = substrate_of(rec, path.name)
            seq = scores.get("tool_sequence") or []
            passed = bool(scores.get("overall_pass"))
            records += 1
            sub_items[sub] += 1
            sub_passes[sub] += int(passed)
            sub_files[sub].add(path.name)
            for tool in set(seq):                 # per-item participation
                per_sub[sub][tool][1] += 1
                per_sub[sub][tool][2] += int(passed)
            for tool in seq:                      # raw call volume
                per_sub[sub][tool][0] += 1

    print(f"{records} item-records across {len(files)} files\n")

    # Runtime tool list, so "never called" is measured against what the agent
    # actually has rather than a number typed into this script.
    try:
        from app import build_tools  # type: ignore
        active = sorted(t.name for t in build_tools())
    except Exception:
        active = []
    if not active:
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        active = sorted(set(re.findall(r'name\s*=\s*"([A-Z][A-Za-z0-9_]+)"', src)))
        print(f"(tool list read from app.py source: {len(active)} names)\n")

    order = sorted(sub_items, key=lambda s: -sub_items[s])
    for sub in order:
        n = sub_items[sub]
        if n < 20:
            continue
        base = 100 * sub_passes[sub] / n
        tools = per_sub[sub]
        total_calls = sum(v[0] for v in tools.values())
        print("=" * 78)
        print(f"{sub}   {n} item-records · baseline pass {base:.0f}% · {total_calls} tool calls")
        print(f"{'tool':<34}{'calls':>7}{'share':>7}{'items':>7}{'pass|called':>13}{'vs base':>9}")
        for tool, (calls, items, passes) in sorted(tools.items(), key=lambda kv: -kv[1][0]):
            share = 100 * calls / total_calls if total_calls else 0
            pwc = 100 * passes / items if items else 0
            delta = pwc - base
            flag = "  <-- thin" if items < 10 else ""
            print(f"{tool:<34}{calls:>7}{share:>6.0f}%{items:>7}{pwc:>12.0f}%{delta:>+8.0f}{flag}")
        never = [t for t in active if t not in tools]
        if never:
            print(f"\n  never called on this substrate ({len(never)}/{len(active)}):")
            for t in never:
                print(f"    - {t}")
        print()

    print("=" * 78)
    print("NOT a removal list. For each zero-call tool the question is still open:")
    print("force the path and measure, or drop it (C1b was the former). Removal is")
    print("agent-visible — matched pair on both substrates before anything goes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
