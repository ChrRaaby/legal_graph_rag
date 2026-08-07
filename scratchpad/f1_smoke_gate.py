"""F1 verify step 5 — end-to-end gate smokes through the real runtime.

Covers the two flags eval_run cannot reach (no golden items yet): pii and
non_tax, plus the persistence-redaction path and the hatch's behavioural effect.
  .venv/bin/python3 scratchpad/f1_smoke_gate.py
"""
import json
import os
import sys
import sqlite3
import tempfile
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

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


print("Building runtime…")
analysis, agent_executor, tools = app.build_runtime()
print(f"runtime ready ({len(tools)} tools)\n")


def ask(q, history=None):
    msgs = list(history or []) + [{"role": "user", "content": q}]
    return app.stream_agent_answer(agent_executor, msgs)


print("=== non_tax gate ===")
ans, evs = ask("Fortæl mig en joke")
gate = [e for e in evs if e.get("type") == "scope_gate"]
check("blocked with exactly one scope_gate event", len(gate) == 1, f"events={[e.get('type') for e in evs]}")
check("flag is non_tax", bool(gate) and gate[0].get("flag") == "non_tax")
check("no tools were called", not [e for e in evs if e.get("type") == "tool_call"])
check("answer is the non_tax template", ans == app.SCOPE_TEMPLATES["non_tax"])
print(f"        reason: {gate[0].get('reason','')[:88] if gate else '—'}")

print("\n=== pii gate + redaction ===")
SECRET = "Mit CPR-nummer er 010190-1234 og jeg bor på Nørregade 5. Hvor meget skal jeg betale i skat?"
ans_p, evs_p = ask(SECRET)
gate_p = [e for e in evs_p if e.get("type") == "scope_gate"]
check("blocked with a scope_gate event", len(gate_p) == 1)
check("flag is pii", bool(gate_p) and gate_p[0].get("flag") == "pii")
check("answer is the pii template", ans_p == app.SCOPE_TEMPLATES["pii"])
reason = gate_p[0].get("reason", "") if gate_p else ""
check("reason does not echo the CPR number", "010190" not in reason, f"reason={reason!r}")
check("redact_if_pii replaces the prompt", app.redact_if_pii(SECRET, evs_p) == app.REDACTED_PII)
print(f"        reason: {reason[:88]}")

# Real persistence path: server._persist_run must scrub both the column and the
# run_start event that carries its own copy of the question.
print("\n=== mr_runs persistence (server._persist_run) ===")
import server  # noqa: E402
_tmp = Path(tempfile.mkdtemp()) / "obs.db"
_orig_db = server._db
server._db = lambda: sqlite3.connect(_tmp)
con = sqlite3.connect(_tmp)
con.execute("CREATE TABLE IF NOT EXISTS mr_runs (run_id TEXT PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP,"
            " question TEXT, answer TEXT, provider TEXT, git_sha TEXT, latency_s REAL, events TEXT, citations TEXT)")
con.commit()
con.close()
events_with_start = [{"type": "run_start", "run_id": "t1", "question": SECRET}] + evs_p
server._persist_run("t1", SECRET, ans_p, 0.5, events_with_start, [])
con = sqlite3.connect(_tmp)
row = con.execute("SELECT question, events FROM mr_runs WHERE run_id='t1'").fetchone()
con.close()
server._db = _orig_db
check("mr_runs.question is redacted", row[0] == app.REDACTED_PII, repr(row[0])[:80])
check("raw CPR absent from the whole stored row", "010190-1234" not in (row[0] + row[1]),
      "PII survived in the events blob")

print("\n=== pass-through is unaffected ===")
ans_ok, evs_ok = ask("Kan jeg få kørselsfradrag når jeg cykler på arbejde?")
check("no scope_gate event", not [e for e in evs_ok if str(e.get("type", "")).startswith("scope_gate")])
check("tools were called", bool([e for e in evs_ok if e.get("type") == "tool_call"]))
check("answer is substantive", len(ans_ok) > 120, f"len={len(ans_ok)}")

print("\n=== follow-up keeps tax context (spec §1) ===")
hist = [{"role": "user", "content": "Hvad er topskattegrænsen i 2025?"},
        {"role": "assistant", "content": "Topskattegrænsen fremgår af personskatteloven § 7."}]
ans_f, evs_f = ask("Hvad med i 2026?", hist)
check("short follow-up is not flagged non_tax",
      not [e for e in evs_f if e.get("type") == "scope_gate"],
      "context-free classification would misfire here")

print("\n" + "=" * 60)
if failures:
    print(f"FAILED ({len(failures)}): " + "; ".join(failures))
    sys.exit(1)
print("ALL GATE SMOKES PASSED")
