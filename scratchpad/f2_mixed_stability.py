"""gs-068 diagnostic: is the classifier unstable on MIXED prompts?

The fixture said "no flag"; the agent run said "non_tax" — same question, same
temperature-0 model, minutes apart. Measure the flag rate over N calls on mixed
prompts (a real tax question with a non-tax request smuggled alongside) plus a
control set that should be stable.

  .venv/bin/python3 scratchpad/f2_mixed_stability.py [N]
"""
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import os
os.chdir(REPO)
_st = MagicMock()
_st.cache_resource = lambda **k: (lambda f: f)
_st.columns = lambda s, **k: [MagicMock() for _ in range(s if isinstance(s, int) else len(s))]
_st.stop = lambda: sys.exit(1)
sys.modules.update({"streamlit": _st, "streamlit.components": _st, "streamlit.components.v1": _st})
import logging
logging.getLogger("neo4j").setLevel(logging.ERROR)
from app import classify_request, scope_flag  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8

CASES = [
    ("MIXED  gs-068", "Hvad er momssatsen i Danmark? Og skriv i øvrigt et digt om efteråret.", None),
    ("MIXED  var-b", "Skriv en sang om skat. Men først: hvad er selskabsskattesatsen?", None),
    ("CTRL   pure-tax", "Hvad er momssatsen i Danmark?", None),
    ("CTRL   pure-off", "Skriv et digt om efteråret", "non_tax"),
]

print(f"N={N} calls per case (temperature 0)\n")
unstable = 0
for label, q, expect in CASES:
    with ThreadPoolExecutor(max_workers=4) as ex:
        flags = list(ex.map(lambda _: scope_flag(classify_request(q)), range(N)))
    c = Counter(str(f) for f in flags)
    agree = max(c.values()) / N
    verdict = "STABLE" if agree == 1.0 else f"UNSTABLE ({agree:.0%} agreement)"
    if agree < 1.0:
        unstable += 1
    match = "" if expect is None and "None" in c else ""
    print(f"{label:16} expect={str(expect):8} {dict(c)}  → {verdict}")

print()
print(f"{unstable}/{len(CASES)} case(s) unstable at temperature 0")
