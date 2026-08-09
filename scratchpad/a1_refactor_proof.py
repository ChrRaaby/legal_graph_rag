"""A1 the decisive proof: the PRE-refactor scorer and the POST-refactor scorer
produce identical output on identical inputs.

The replay-against-stored-verdicts check is confounded by history: some saved
files were scored by older scorer generations, and the F3 judge cells had their
embedded gs-064 item deliberately patched after scoring. Neither is an A1
regression, but neither can be waved away by assertion.

So compare the functions themselves: load eval_run.py as it was at git HEAD
(before this refactor) and diff its score_item against app.score_item over every
(item, answer) pair in every saved eval file. Pure refactor <=> zero differences.

  .venv/bin/python3 scratchpad/a1_refactor_proof.py
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)
_st = MagicMock()
_st.cache_resource = lambda **k: (lambda f: f)
_st.columns = lambda s, **k: [MagicMock() for _ in range(s if isinstance(s, int) else len(s))]
_st.stop = lambda: sys.exit(1)
sys.modules.update({"streamlit": _st, "streamlit.components": _st, "streamlit.components.v1": _st})
import logging
logging.getLogger("neo4j").setLevel(logging.ERROR)

import app  # post-refactor scorer

# ── load the pre-refactor eval_run.py from git ───────────────────────────────
old_src = subprocess.run(["git", "show", "HEAD:eval_run.py"], capture_output=True,
                         text=True, check=True, cwd=REPO).stdout
old_path = Path("/tmp/eval_run_prerefactor.py")
old_path.write_text(old_src, encoding="utf-8")
spec = importlib.util.spec_from_file_location("eval_run_prerefactor", old_path)
old = importlib.util.module_from_spec(spec)
sys.modules["eval_run_prerefactor"] = old
spec.loader.exec_module(old)

print("loaded pre-refactor scorer from git HEAD")
print(f"  old.score_item defined in module: {old.score_item.__module__}")
print(f"  app.score_item defined in module: {app.score_item.__module__}")
assert old.score_item is not app.score_item, "did not actually load the old copy"

pairs = 0
diffs = 0
for p in sorted((REPO / "eval_history").glob("eval_results_*.jsonl")):
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if "item" not in r or "answer" not in r:
            continue
        pairs += 1
        a = old.score_item(r["item"], r["answer"], [])
        b = app.score_item(r["item"], r["answer"], [])
        if a != b:
            diffs += 1
            if diffs <= 3:
                keys = set(a) | set(b)
                d = {k: (a.get(k), b.get(k)) for k in keys if a.get(k) != b.get(k)}
                print(f"  DIFF {p.name} {r['item']['id']}: {d}")

# also exercise behaviour detection over every distinct answer
answers = set()
for p in (REPO / "eval_history").glob("eval_results_*.jsonl"):
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                answers.add(json.loads(line).get("answer", ""))
            except Exception:
                pass
beh_diffs = sum(1 for a in answers if old.detect_behavior(a) != app.detect_behavior(a))

print(f"\nscore_item     : {pairs - diffs}/{pairs} identical  ({diffs} differences)")
print(f"detect_behavior: {len(answers) - beh_diffs}/{len(answers)} identical over distinct answers")

if diffs == 0 and beh_diffs == 0:
    print("\nA1 IS A PURE REFACTOR — old and new scorers are functionally identical")
    sys.exit(0)
print("\nNOT a pure refactor — investigate the diffs above")
sys.exit(1)
