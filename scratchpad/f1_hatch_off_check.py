"""F1 verify step 2 (behavioural half): with F_SCOPE_GUARD=off, a prompt the
gate WOULD block must reach the agent exactly as before F1.

Run:  F_SCOPE_GUARD=off .venv/bin/python3 scratchpad/f1_hatch_off_check.py
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

_st = MagicMock()
_st.cache_resource = lambda **k: (lambda f: f)
_st.columns = lambda spec, **k: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
_st.stop = lambda: sys.exit("neo4j fail")
sys.modules.update({"streamlit": _st, "streamlit.components": _st, "streamlit.components.v1": _st})

import logging
logging.getLogger("neo4j").setLevel(logging.ERROR)
import app

if app.F_SCOPE_GUARD:
    sys.exit("Run me with F_SCOPE_GUARD=off — the flag is currently on.")

# Poison the classifier: with the hatch off it must never be reached.
def _boom(*a, **k):
    raise AssertionError("classify_request was called with F_SCOPE_GUARD=off")

app.classify_request = _boom

analysis, agent_executor, tools = app.build_runtime()
failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


print("\n=== hatch OFF: a would-be-blocked prompt reaches the agent ===")
ans, evs = app.stream_agent_answer(
    agent_executor, [{"role": "user", "content": "Fortæl mig en joke"}]
)
check("classifier was never invoked", True)  # reaching here means _boom never fired
check("no scope_gate event", not [e for e in evs if str(e.get("type", "")).startswith("scope_gate")])
check("answer is NOT the gate template", ans != app.SCOPE_TEMPLATES["non_tax"])
check("agent actually ran (llm events present)", bool([e for e in evs if e.get("type") == "llm_call"]))
print(f"        answer[:90]: {ans[:90]!r}")

print("\n=== hatch OFF: a normal tax question is unaffected ===")
ans2, evs2 = app.stream_agent_answer(
    agent_executor, [{"role": "user", "content": "Kan jeg få kørselsfradrag når jeg cykler på arbejde?"}]
)
check("tools were called", bool([e for e in evs2 if e.get("type") == "tool_call"]))
check("substantive answer", len(ans2) > 120)

print("\n" + "=" * 60)
if failures:
    print(f"FAILED ({len(failures)}): " + "; ".join(failures))
    sys.exit(1)
print("HATCH-OFF BEHAVIOUR VERIFIED (pre-F1 path intact)")
