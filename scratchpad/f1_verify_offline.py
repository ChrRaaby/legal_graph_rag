"""F1 offline verification — no LLM calls, no Neo4j.

Covers backlog Phase F verify steps 2 (hatch), 3 (signal collision) and the
spec §6.4 redaction guarantee. Run:  .venv/bin/python3 scratchpad/f1_verify_offline.py
"""
import os
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

# Stub streamlit exactly like eval_run.py does (same MagicMock pattern), so
# importing app is cheap and does not need a Streamlit runtime.
from unittest.mock import MagicMock  # noqa: E402

_st = MagicMock()
_st.cache_resource = lambda **kwargs: (lambda f: f)
_st.columns = lambda spec, **kw: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
_st.stop = lambda: sys.exit("Neo4j initialization failed")
sys.modules.update({
    "streamlit": _st,
    "streamlit.components": _st,
    "streamlit.components.v1": _st,
})

import app  # noqa: E402
import eval_run  # noqa: E402

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


print("\n=== verify 3: signal collision (spec §3) ===")
SIG = eval_run.BEHAVIOR_SIGNALS
OTHER = ["refuse", "admit_unknown", "clarify", "correct_premise"]

non_tax = app.SCOPE_TEMPLATES["non_tax"].lower()
pii = app.SCOPE_TEMPLATES["pii"].lower()
illegal = app.SCOPE_TEMPLATES["illegal"].lower()

for beh in OTHER:
    hits = [s for s in SIG[beh] if s in non_tax]
    check(f"non_tax template trips no `{beh}` signal", not hits, f"hits={hits}")
for beh in OTHER:
    hits = [s for s in SIG[beh] if s in pii]
    check(f"pii template trips no `{beh}` signal", not hits, f"hits={hits}")

check("illegal template DOES trip `refuse` (keeps gs-026/034/035/036 green)",
      any(s in illegal for s in SIG["refuse"]),
      "illegal reply must still classify as refuse")

check("non_tax template detects as out_of_scope",
      eval_run.detect_behavior(app.SCOPE_TEMPLATES["non_tax"]) == "out_of_scope",
      eval_run.detect_behavior(app.SCOPE_TEMPLATES["non_tax"]))
check("pii template detects as pii_block",
      eval_run.detect_behavior(app.SCOPE_TEMPLATES["pii"]) == "pii_block",
      eval_run.detect_behavior(app.SCOPE_TEMPLATES["pii"]))
check("illegal template detects as refuse",
      eval_run.detect_behavior(app.SCOPE_TEMPLATES["illegal"]) == "refuse",
      eval_run.detect_behavior(app.SCOPE_TEMPLATES["illegal"]))

print("\n=== new signals must not reclassify existing golden expectations ===")
import json  # noqa: E402
gs = json.loads((REPO / "eval_golden_set.json").read_text(encoding="utf-8"))
items = gs["items"] if isinstance(gs, dict) and "items" in gs else gs
new_sigs = SIG["out_of_scope"] + SIG["pii_block"]
bad = [(i["id"], s) for i in items for s in new_sigs
       if s in (i.get("expected_answer", "") or "").lower()]
check("no golden expected_answer contains a new gate signal", not bad, f"{bad}")

print("\n=== verify 2: hatch F_SCOPE_GUARD=off removes the classifier from the path ===")
check("F_SCOPE_GUARD default is on", app.F_SCOPE_GUARD is True)

src = (REPO / "app.py").read_text(encoding="utf-8")
gate_guarded = "if F_SCOPE_GUARD and _question:" in src
check("gate body is guarded by the hatch flag", gate_guarded)

# Hatch parsing, checked in-process. NOTE: an earlier version of this test
# spawned a subprocess that re-imported app — which starts a SECOND torch-
# importing interpreter alongside this one, something the backlog's traps index
# explicitly warns against ("never start two torch processes concurrently").
# It passed once and then failed spuriously. The behavioural half of verify 2 —
# that a would-be-blocked prompt actually reaches the agent with the hatch off —
# lives in its own process in scratchpad/f1_hatch_off_check.py, which is both
# stronger and trap-free.
_parse = lambda v: v.strip().lower() not in ("0", "off", "false", "no")
for _v, _want in (("off", False), ("0", False), ("false", False), ("no", False),
                  ("on", True), ("1", True), ("", True)):
    check(f"hatch parse {_v!r} → {_want}", _parse(_v) is _want)
check("app parses the hatch with the project's standard idiom",
      "F_SCOPE_GUARD = os.getenv(\"F_SCOPE_GUARD\", \"on\").strip().lower() not in "
      "(\"0\", \"off\", \"false\", \"no\")" in src)

print("\n=== spec §6.4: PII redaction ===")
gate_pii = [{"type": "scope_gate", "flag": "pii", "reason": "indeholder CPR-nummer"}]
gate_non_tax = [{"type": "scope_gate", "flag": "non_tax", "reason": "handler om madlavning"}]
secret = "Mit CPR er 010190-1234"
check("pii-gated question is redacted",
      app.redact_if_pii(secret, gate_pii) == app.REDACTED_PII)
check("non-pii gate leaves the question intact",
      app.redact_if_pii(secret, gate_non_tax) == secret)
check("ungated question is untouched", app.redact_if_pii(secret, []) == secret)

print("\n=== fail-open contract ===")
_saved = app._get_scope_classifier
app._get_scope_classifier = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
v = app.classify_request("Kan jeg få kørselsfradrag?")
app._get_scope_classifier = _saved
check("classifier failure returns all-false flags", app.scope_flag(v) is None)
check("classifier failure records an error", bool(v.get("error")))

print("\n=== ground rule 4: contract preserved ===")
import inspect  # noqa: E402
gsrc = inspect.getsource(app.stream_agent_answer)
check("gate returns the (answer, tool_events) 2-tuple",
      "return SCOPE_TEMPLATES[_flag], tool_events" in gsrc)

print("\n" + "=" * 60)
if failures:
    print(f"FAILED ({len(failures)}): " + "; ".join(failures))
    sys.exit(1)
print("ALL OFFLINE CHECKS PASSED")
