"""Remove the retired Streamlit UI from app.py (deferred Phase-E refactor).

app.py stays THE single runtime source (ground rule 5) — this only removes the
presentation layer that Maskinrummet replaced. Verified beforehand: no symbol in
the UI half is referenced outside app.py, and pandas / altair / neo4j_viz /
sqlite3 / streamlit are used ONLY by that half.

Every edit is anchored on exact text and asserted, so a drifted file fails loudly
instead of being silently half-cut.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
p = REPO / "app.py"
src = p.read_text(encoding="utf-8")
before_lines = src.count("\n")


def cut(pattern: str, label: str, flags=0, count=1) -> None:
    global src
    new, n = re.subn(pattern, "", src, count=count, flags=flags)
    assert n == count, f"anchor missed: {label} (matched {n}, wanted {count})"
    src = new
    print(f"  cut  {label}")


def swap(old: str, new: str, label: str) -> None:
    global src
    assert old in src, f"anchor missed: {label}"
    src = src.replace(old, new, 1)
    print(f"  swap {label}")


# ── 1. UI-only imports ───────────────────────────────────────────────────────
for imp, label in [
    (r"^import sqlite3\n", "import sqlite3"),
    (r"^import pandas as pd\n", "import pandas"),
    (r"^import altair as alt\n", "import altair"),
    (r"^import streamlit as st\n", "import streamlit"),
    (r"^import streamlit\.components\.v1 as components\n", "streamlit.components"),
    (r"^from neo4j_viz\.neo4j import ColorSpace, from_neo4j\n", "neo4j_viz.neo4j"),
    (r"^from neo4j_viz import Layout\n", "neo4j_viz Layout"),
]:
    cut(imp, label, flags=re.M)

# ── 2. page config ───────────────────────────────────────────────────────────
cut(r'^st\.set_page_config\([^\n]*\)\n\n?', "st.set_page_config", flags=re.M)

# ── 3. the SQLite layer that only the Streamlit app used ─────────────────────
# server.py keeps its own observability.db tables (mr_runs, feedback); this block
# backed the Streamlit traces/eval panels exclusively.
db_start = src.index("# ---------------------------------------------------------------------------\n"
                     "# Persistence — SQLite-backed traces and eval results")
db_end = src.index("st.markdown(", db_start)
assert db_start < db_end, "db block anchors out of order"
removed_db = src[db_start:db_end]
assert "_db_load_eval_results" in removed_db, "db block does not span the helpers"
assert "def build_runtime" not in removed_db, "db block would swallow runtime code"
src = src[:db_start] + src[db_end:]
print(f"  cut  SQLite trace/eval layer ({removed_db.count(chr(10))} lines)")

# ── 4. the sidebar CSS injection ─────────────────────────────────────────────
css_start = src.index("st.markdown(")
css_end = src.index("unsafe_allow_html=True,\n)", css_start) + len("unsafe_allow_html=True,\n)")
src = src[:css_start] + src[css_end:].lstrip("\n")
print("  cut  sidebar CSS block")

# ── 5. build_runtime caching: st.cache_resource → lru_cache ──────────────────
# Preserves the memoisation the Streamlit decorator provided, so importers that
# call build_runtime() more than once still get one initialised runtime.
swap("@st.cache_resource(show_spinner=False)\ndef build_runtime(",
     "@lru_cache(maxsize=None)\ndef build_runtime(",
     "build_runtime caching → lru_cache")
swap("from difflib import SequenceMatcher\n",
     "from difflib import SequenceMatcher\nfrom functools import lru_cache\n",
     "import lru_cache")

# ── 6. the UI itself: renderers + module-level page code ─────────────────────
ui_start = src.index("def _eval_run_case(")
removed_ui = src[ui_start:]
assert "_render_architecture" in removed_ui and "st.title(" in removed_ui, "UI block anchors wrong"
assert "def stream_agent_answer" not in removed_ui, "UI block would swallow the runtime"
assert "def score_item" not in removed_ui, "UI block would swallow the scorer"
src = src[:ui_start].rstrip("\n") + "\n"
print(f"  cut  Streamlit UI: renderers + page code ({removed_ui.count(chr(10))} lines)")

# ── guards ───────────────────────────────────────────────────────────────────
leftover = [l for l in src.splitlines() if re.search(r"(^|[^A-Za-z_.])st\.", l)
            or "streamlit" in l]
assert not leftover, "streamlit references survive:\n" + "\n".join(leftover[:10])
for must in ("def build_runtime", "def stream_agent_answer", "def score_item",
             "def classify_request", "def resolve_llm_provider", "def log_trajectory",
             "def token_usage", "def redact_if_pii", "def detect_behavior"):
    assert must in src, f"runtime symbol lost: {must}"

p.write_text(src, encoding="utf-8")
print(f"\napp.py: {before_lines} → {src.count(chr(10))} lines "
      f"(−{before_lines - src.count(chr(10))})")
print("no streamlit references remain; all runtime symbols intact")
