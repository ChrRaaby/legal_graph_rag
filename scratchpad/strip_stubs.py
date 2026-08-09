"""Remove the now-pointless Streamlit stubs from the remaining CLI tools.

app.py no longer imports streamlit, so these MagicMock blocks stub a module
nobody loads. Leaving them would imply a coupling that no longer exists.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
changed = []


def strip(fname: str, start_anchor: str, end_anchor: str, note: str) -> None:
    p = REPO / fname
    src = p.read_text(encoding="utf-8")
    i = src.find(start_anchor)
    if i == -1:
        print(f"  {fname}: anchor not found, skipped")
        return
    j = src.index(end_anchor, i) + len(end_anchor)
    block = src[i:j]
    assert "streamlit" in block, f"{fname}: block does not look like the stub"
    src = src[:i] + note + src[j:]
    # drop the now-unused import if nothing else needs it
    if "MagicMock" not in src.replace("from unittest.mock import MagicMock", ""):
        src = re.sub(r"^from unittest\.mock import MagicMock\n", "", src, count=1, flags=re.M)
    p.write_text(src, encoding="utf-8")
    changed.append(fname)
    print(f"  {fname}: stub removed ({block.count(chr(10))} lines)")


NOTE = ("# app.py is pure runtime since 2026-08-08 — no Streamlit stub needed.\n")

strip("eval_compare.py",
      "# ── Mock streamlit before any app import ",
      '"streamlit.components.v1": _st,\n})\n', NOTE)
strip("eval_scope_fixtures.py",
      "# Stub streamlit before importing app (same pattern as eval_run.py).",
      '"streamlit": _st, "streamlit.components": _st, "streamlit.components.v1": _st,\n})\n', NOTE)

print("\nchanged:", changed or "(none)")
