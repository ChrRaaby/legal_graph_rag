#!/usr/bin/env python3
"""
server.py — FastAPI backend for Maskinrummet, the event-driven frontend
(backlog Phase E). It is a thin adapter over the existing Streamlit agent:
`app.py` remains the single source of the agent runtime. Same proven pattern as
eval_run.py — stub `streamlit` in `sys.modules`, then import build_runtime /
stream_agent_answer from `app`. No agent logic lives here.

Endpoints:
  GET  /api/architecture   runtime truth: provider, tool list, graph stats, app_mode
  POST /api/ask            Server-Sent Events stream of a live agent run
  GET  /api/health         liveness
  GET  /  (+ static)       the built frontend (frontend/dist), SPA fallback

Run (dev):   .venv/bin/uvicorn server:app --reload --port 8000
             (frontend dev server on :5173 proxies /api → :8000)
Run (prod):  build the frontend (npm run build), then
             APP_MODE=user .venv/bin/uvicorn server:app --port 8000
"""
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

# ── Stub Streamlit before importing app (identical rationale to eval_run.py) ──
# app.py runs Streamlit UI code at import time, including a build_runtime() call
# at module bottom. Under the stub that top-level build still runs once — we
# reuse its result below. st.stop() is a no-op here (NOT sys.exit) so a transient
# Aura hiccup at import doesn't abort the server; we validate + retry ourselves.
_st = MagicMock()
_st.cache_resource = lambda **kwargs: (lambda f: f)
_st.columns = lambda spec, **kw: [MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
_st.stop = lambda: None
_st.error = lambda *a, **k: None
sys.modules.update({
    "streamlit": _st,
    "streamlit.components": _st,
    "streamlit.components.v1": _st,
})

import logging  # noqa: E402
logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")

sys.path.insert(0, str(Path(__file__).parent))
import app as agent_app  # noqa: E402  (triggers one import-time build_runtime)
from app import build_runtime, stream_agent_answer, resolve_llm_provider, redact_if_pii  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402


APP_MODE = "user" if os.getenv("APP_MODE", "dev").lower() == "user" else "dev"
_DIST = Path(__file__).parent / "frontend" / "dist"


# ── Runtime acquisition ───────────────────────────────────────────────────────

def _build_with_retry(attempts: int = 4, timeout_s: int = 120):
    """Build the agent runtime, retrying if it stalls or drops — the Aura free
    instance intermittently hangs build_runtime() on startup (a silent hang, not
    an exception). Mirrors eval_run.build_runtime_with_retry so the server has the
    same resilience without importing eval_run's CLI module."""
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        box: dict = {}

        def _load():
            try:
                box["val"] = build_runtime()
            except Exception as exc:  # noqa: BLE001
                box["err"] = exc

        t = threading.Thread(target=_load, daemon=True)
        t.start()
        t.join(timeout_s)
        if "val" in box:
            return box["val"]
        last_err = (TimeoutError(f"build_runtime stalled >{timeout_s}s")
                    if t.is_alive() else box.get("err", RuntimeError("unknown build_runtime failure")))
        print(f"  runtime load failed ({last_err}) — attempt {attempt}/{attempts}, retrying…", flush=True)
        time.sleep(3)
    raise RuntimeError(f"build_runtime failed after {attempts} attempts: {last_err}")


def _acquire_runtime():
    """Prefer the runtime app.py already built at import time (avoids a second
    ~20s build); fall back to an explicit retried build if that one is missing or
    its Neo4j connection is dead."""
    analysis = getattr(agent_app, "analysis", None)
    executor = getattr(agent_app, "agent_executor", None)
    tools = getattr(agent_app, "agent_tools", None)
    ok = analysis is not None and executor is not None and tools is not None
    if ok:
        try:
            ok = bool(analysis.verify_connection())
        except Exception:
            ok = False
    if not ok:
        print("Import-time runtime unavailable — building explicitly…", flush=True)
        analysis, executor, tools = _build_with_retry()
    return analysis, executor, tools


print("Maskinrummet server — acquiring agent runtime…", flush=True)
ANALYSIS, AGENT_EXECUTOR, AGENT_TOOLS = _acquire_runtime()
PROVIDER = resolve_llm_provider()
print(f"Runtime ready · provider={PROVIDER} · {len(AGENT_TOOLS)} tools · app_mode={APP_MODE}", flush=True)


# ── Cached runtime-truth facts ────────────────────────────────────────────────

_graph_stats_cache: dict | None = None


def _graph_stats() -> dict:
    global _graph_stats_cache
    if _graph_stats_cache is not None:
        return _graph_stats_cache
    try:
        rows = ANALYSIS.run_query(
            "RETURN count{ (l:Legislation) } AS legislation, "
            "count{ (s:Section) } AS sections"
        )
        _graph_stats_cache = {
            "legislation": rows[0]["legislation"],
            "sections": rows[0]["sections"],
        } if rows else {"legislation": 0, "sections": 0}
    except Exception as exc:  # noqa: BLE001
        _graph_stats_cache = {"legislation": 0, "sections": 0, "error": str(exc)}
    return _graph_stats_cache


def _architecture() -> dict:
    """The circuit's source of truth: the ACTUAL tools and provider, so the
    diagram can never show stale wiring (the reason Kredsløbet exists)."""
    return {
        "app_mode": APP_MODE,
        "provider": PROVIDER,
        "graph_stats": _graph_stats(),
        "tools": [{"name": t.name, "description": (t.description or "")} for t in AGENT_TOOLS],
    }


# ── Law naming + citation resolution (law-aware, for kilder + graph) ──────────

# Full title/description stem → short code, for compact labels.
_LAW_SHORT = [
    ("ligningslov", "LL"), ("personskattelov", "PSL"), ("selskabsskattelov", "SEL"),
    ("kildeskattelov", "KSL"), ("merværdiafgiftslov", "ML"), ("momslov", "ML"),
    ("aktieavancebeskatningslov", "ABL"), ("kursgevinstlov", "KGL"),
    ("afskrivningslov", "AL"), ("fondsbeskatningslov", "FBL"), ("aktiesparekontolov", "ASKL"),
    ("boafgiftslov", "BAL"), ("statsskattelov", "SL"),
]
# Abbreviation / genitive-stem → title stem, for parsing law context out of text.
_ALIAS_STEM = {
    "ll": "ligningslov", "psl": "personskattelov", "sel": "selskabsskattelov",
    "ksl": "kildeskattelov", "ml": "momslov", "abl": "aktieavancebeskatningslov",
    "kgl": "kursgevinstlov", "al": "afskrivningslov", "fbl": "fondsbeskatningslov",
    "askl": "aktiesparekontolov", "bal": "boafgiftslov",
    "merværdiafgiftslov": "momslov",
}


def _law_short(title: str) -> str:
    tl = (title or "").lower()
    for stem, short in _LAW_SHORT:
        if stem in tl:
            return short
    return (title or "?")[:6]


_SEC_IN_TEXT = re.compile(r"§+\s*(\d+(?:\s*[a-zæøåA-ZÆØÅ])?)\b")
_LAW_IN_TEXT = re.compile(r"\b([a-zæøå]{4,}lov)(?:en|ens|s)?\b|\b(LL|PSL|SEL|KSL|ML|ABL|KGL|AL|FBL|ASKL|BAL)\b")


def _law_stem(token: str) -> str | None:
    """Map a matched law token (genitive full name or abbreviation) to a title stem."""
    t = token.lower()
    if t in _ALIAS_STEM:
        return _ALIAS_STEM[t]
    if t.endswith("lov"):
        return t
    return None


def resolve_citations(answer: str) -> list[dict]:
    """Law-aware: pull each §-reference out of the answer with the nearest
    preceding law context, resolve it to a current Section, and report
    verification + ELI uri + node id (for the kilder chips and graph highlight).
    A § with no identifiable law is checked against any current law."""
    if not answer:
        return []
    # Ordered list of (position, law_stem) law mentions.
    laws: list[tuple[int, str]] = []
    for lm in _LAW_IN_TEXT.finditer(answer):
        stem = _law_stem(lm.group(1) or lm.group(2) or "")
        if stem:
            laws.append((lm.start(), stem))

    seen: set[tuple[str | None, str]] = set()
    out: list[dict] = []
    for sm in _SEC_IN_TEXT.finditer(answer):
        num = re.sub(r"\s+", " ", sm.group(1)).strip().upper()
        stem = None
        for pos, s in laws:
            if pos < sm.start():
                stem = s
        key = (stem, num)
        if key in seen:
            continue
        seen.add(key)
        try:
            if stem:
                rows = ANALYSIS.run_query(
                    """MATCH (l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION*1..6]->(s:Section)
                       WHERE toLower(coalesce(l.description,l.title,'')) CONTAINS $stem
                         AND coalesce(l.is_current,true) AND toUpper(trim(s.number)) = $num
                       RETURN elementId(s) AS id, coalesce(l.description,l.title) AS lov, l.uri AS uri LIMIT 1""",
                    {"stem": stem, "num": num},
                )
            else:
                rows = ANALYSIS.run_query(
                    """MATCH (l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION*1..6]->(s:Section)
                       WHERE coalesce(l.is_current,true) AND toUpper(trim(s.number)) = $num
                       RETURN elementId(s) AS id, coalesce(l.description,l.title) AS lov, l.uri AS uri LIMIT 1""",
                    {"num": num},
                )
        except Exception:
            rows = []
        found = bool(rows)
        lov_title = rows[0]["lov"] if found else None
        short = _law_short(lov_title) if lov_title else (_law_short(stem) if stem else "")
        label = f"{short} § {num}".replace(" § ", " § ") if short else f"§ {num}"
        out.append({
            "label": label,
            "lov": short,
            "section_number": num,
            "verified": found,
            "uri": rows[0]["uri"] if found else None,
            "element_id": rows[0]["id"] if found else None,
        })
    return out


# ── Graph-ref parsing (tool output → section refs → subgraph) ─────────────────

def _refs_from_tool_output(content_full: str) -> list[dict]:
    """Best-effort parse of retrieval-tool JSON output into section references
    {uri, title, num}. Only rows that name a section are kept."""
    refs: list[dict] = []
    try:
        rows = json.loads(content_full)
    except Exception:
        return refs
    if not isinstance(rows, list):
        return refs
    for row in rows:
        if not isinstance(row, dict):
            continue
        num = row.get("section_number") or row.get("paragraf")
        if not num:
            continue
        refs.append({
            "uri": (row.get("legislation_uri") or "").strip(),
            "title": (row.get("legislation_title") or row.get("lov") or "").strip(),
            "num": str(num).strip(),
        })
    return refs


def build_subgraph(refs: list[dict], answer: str = "") -> dict:
    """Resolve retrieved section refs to a structured subgraph: law → section →
    stk hierarchy, plus CITES edges from the retrieved sections (cited neighbours
    included as their own nodes). Sections whose § is cited in the answer are
    flagged `used`."""
    clean = [{"uri": r.get("uri", "") or "", "title": r.get("title", "") or "", "num": (r.get("num") or "").upper()}
             for r in refs if (r.get("num"))]
    if not clean:
        return {"laws": [], "sections": [], "paragraphs": [], "cites": []}

    rowsA = ANALYSIS.run_query(
        """
        UNWIND $refs AS ref
        MATCH (l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION*1..6]->(s:Section)
        WHERE ( (ref.uri <> '' AND l.uri = ref.uri)
                OR (ref.uri = '' AND ref.title <> '' AND toLower(coalesce(l.description,l.title,'')) CONTAINS toLower(ref.title)) )
          AND coalesce(l.is_current, true)
          AND toUpper(trim(s.number)) = toUpper(ref.num)
        WITH DISTINCT l, s
        OPTIONAL MATCH (s)-[:HAS_PARAGRAPH]->(p:Paragraph)
        RETURN elementId(l) AS law_id, coalesce(l.description,l.title) AS law_title, l.uri AS law_uri,
               coalesce(l.is_current,true) AS law_current,
               elementId(s) AS sec_id, s.number AS sec_num, s.title AS sec_title,
               [x IN collect(DISTINCT {id: elementId(p), number: p.number}) WHERE x.number IS NOT NULL] AS paras
        """,
        {"refs": clean},
    )

    laws: dict[str, dict] = {}
    sections: dict[str, dict] = {}
    paragraphs: list[dict] = []
    cited = resolve_citations(answer)
    used_ids = {c["element_id"] for c in cited if c.get("element_id")}

    for r in rowsA:
        short = _law_short(r["law_title"])
        laws.setdefault(r["law_id"], {
            "id": r["law_id"], "short": short, "title": r["law_title"],
            "uri": r["law_uri"], "is_current": r["law_current"],
        })
        key = f"{short}|{r['sec_num'].upper()}"
        sections.setdefault(r["sec_id"], {
            "id": r["sec_id"], "key": key, "law_id": r["law_id"],
            "section_number": r["sec_num"], "title": r["sec_title"],
            "retrieved": True, "used": r["sec_id"] in used_ids,
        })
        for p in r["paras"]:
            paragraphs.append({"id": p["id"], "number": p["number"], "section_id": r["sec_id"]})

    ids = list(sections.keys())
    cites: list[dict] = []
    if ids:
        rowsB = ANALYSIS.run_query(
            """
            MATCH (a:Section)-[c:CITES]->(b:Section)
            WHERE elementId(a) IN $ids
            OPTIONAL MATCH (bl:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION*1..6]->(b)
            WITH a, b, c, bl WHERE bl IS NULL OR coalesce(bl.is_current,true)
            RETURN DISTINCT elementId(a) AS from_id, elementId(b) AS to_id,
                   b.number AS to_num, coalesce(bl.description,bl.title) AS to_law,
                   bl.uri AS to_law_uri, elementId(bl) AS to_law_id, c.via AS via
            """,
            {"ids": ids},
        )
        for r in rowsB:
            # Add the cited neighbour as a node if it isn't already retrieved.
            if r["to_id"] not in sections and r["to_law"]:
                short = _law_short(r["to_law"])
                laws.setdefault(r["to_law_id"], {
                    "id": r["to_law_id"], "short": short, "title": r["to_law"],
                    "uri": r["to_law_uri"], "is_current": True,
                })
                sections[r["to_id"]] = {
                    "id": r["to_id"], "key": f"{short}|{(r['to_num'] or '').upper()}",
                    "law_id": r["to_law_id"], "section_number": r["to_num"], "title": None,
                    "retrieved": False, "used": r["to_id"] in used_ids,
                }
            cites.append({"from": r["from_id"], "to": r["to_id"], "via": r["via"]})

    return {
        "laws": list(laws.values()),
        "sections": list(sections.values()),
        "paragraphs": paragraphs,
        "cites": cites,
    }


def node_detail(element_id: str) -> dict:
    rows = ANALYSIS.run_query(
        """
        MATCH (s:Section) WHERE elementId(s) = $id
        OPTIONAL MATCH (l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION*1..6]->(s)
        WITH l, s WHERE l IS NULL OR coalesce(l.is_current,true)
        OPTIONAL MATCH (s)-[:HAS_PARAGRAPH]->(p:Paragraph)
        WITH l, s, p ORDER BY p.number
        RETURN coalesce(l.description,l.title) AS lov, l.uri AS uri, coalesce(l.is_current,true) AS current,
               s.number AS num, s.title AS title,
               [x IN collect({n: p.number, t: p.text}) WHERE x.t IS NOT NULL] AS paras
        LIMIT 1
        """,
        {"id": element_id},
    )
    if not rows:
        return {"found": False}
    r = rows[0]
    return {
        "found": True,
        "label": f"{_law_short(r['lov'])} § {r['num']}" if r["lov"] else f"§ {r['num']}",
        "lov": r["lov"], "short": _law_short(r["lov"]) if r["lov"] else "",
        "section_number": r["num"], "section_title": r["title"],
        "is_current": r["current"], "uri": r["uri"],
        "paragraphs": [{"number": p["n"], "text": p["t"]} for p in r["paras"]],
    }


# ── Run + feedback persistence (own sqlite tables in observability.db) ─────────

_DB_PATH = str(Path(__file__).parent / "observability.db")
_db_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS mr_runs (
        run_id TEXT PRIMARY KEY, ts TEXT NOT NULL DEFAULT (datetime('now')),
        question TEXT, answer TEXT, provider TEXT, git_sha TEXT,
        latency_s REAL, events TEXT, citations TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS mr_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')),
        run_id TEXT, verdict TEXT, comment TEXT)""")
    return con


def _persist_run(run_id: str, question: str, answer: str, latency_s: float,
                 events: list[dict], citations: list[dict]) -> None:
    # F1: a pii-gated prompt is never stored verbatim (spec §6.4). Applied here,
    # at the single write choke point, so no caller can bypass it. The run_start
    # event carries its own copy of the question, so the event log must be
    # scrubbed too — redacting only the column would still persist the PII.
    redacted = redact_if_pii(question, events)
    if redacted != question:
        events = [
            {**ev, "question": redacted} if "question" in ev else ev
            for ev in events
        ]
    question = redacted
    with _db_lock:
        con = _db()
        con.execute(
            "INSERT OR REPLACE INTO mr_runs (run_id, question, answer, provider, git_sha, latency_s, events, citations) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, question, answer, PROVIDER, _git_sha(), latency_s,
             json.dumps(events, ensure_ascii=False, default=str),
             json.dumps(citations, ensure_ascii=False)),
        )
        con.commit()
        con.close()


def _git_sha() -> str:
    """Short HEAD hash, '-dirty' when tracked files are modified (matches
    eval_run._git_sha so live-run and eval provenance read the same way)."""
    import subprocess
    cwd = str(Path(__file__).parent)
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=cwd, capture_output=True, text=True, timeout=5)
        sha = r.stdout.strip()
        if not sha:
            return "unknown"
        d = subprocess.run(["git", "status", "--porcelain", "-uno"],
                           cwd=cwd, capture_output=True, text=True, timeout=5)
        if d.returncode == 0 and d.stdout.strip():
            sha += "-dirty"
        return sha
    except Exception:
        return "unknown"


# ── SSE run ───────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    """One SSE message. json.dumps escapes embedded newlines (e.g. in the model's
    thinking text), so the payload is always a single `data:` line."""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _client_event(ev: dict) -> dict:
    """Transform an internal tool_event into its SSE shape: rename llm_call→llm
    and, for tool_results, attach graph_refs (retrieved section refs, for the
    Graflinse). content_full rides along for the I/O drill-down."""
    out = dict(ev)
    if out.get("type") == "llm_call":
        out["type"] = "llm"
    elif out.get("type") == "tool_result":
        out["graph_refs"] = _refs_from_tool_output(out.get("content_full") or "")
    return out


def _run_events(messages: list[dict]):
    """Generator of SSE strings for one agent run. stream_agent_answer runs in a
    worker thread; its on_tool_event callback (called with the full, growing
    tool_events list) is diffed into per-event SSE messages; then citations +
    answer + done are emitted and the whole run is persisted for later replay.
    Every event is a pure record — the frontend rebuilds all layers as
    f(event_log, t)."""
    q: queue.Queue = queue.Queue()
    t0 = time.perf_counter()
    run_id = uuid.uuid4().hex[:12]
    question = messages[-1]["content"] if messages else ""
    client_events: list[dict] = []  # everything we send, for persistence

    def _worker():
        try:
            sent = 0

            def _on_event(tool_events: list[dict]):
                nonlocal sent
                for ev in tool_events[sent:]:
                    q.put(("event", ev))
                sent = len(tool_events)

            answer, tool_events = stream_agent_answer(
                AGENT_EXECUTOR, messages, on_tool_event=_on_event
            )
            for ev in tool_events[sent:]:
                q.put(("event", ev))
            totals = {"input_tokens": 0, "output_tokens": 0}
            for ev in tool_events:
                if ev.get("type") == "llm_call":
                    totals["input_tokens"] += int(ev.get("input_tokens") or 0)
                    totals["output_tokens"] += int(ev.get("output_tokens") or 0)
            q.put(("citations", resolve_citations(answer)))
            q.put(("answer", answer))
            q.put(("done", {"latency_s": round(time.perf_counter() - t0, 3), **totals}))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", str(exc)))
            q.put(("done", {"latency_s": round(time.perf_counter() - t0, 3)}))

    threading.Thread(target=_worker, daemon=True).start()

    start_ev = {
        "type": "run_start", "run_id": run_id, "provider": PROVIDER,
        "question": question, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    client_events.append(start_ev)
    yield _sse(start_ev)

    answer_text = ""
    citations: list[dict] = []
    while True:
        kind, payload = q.get()
        if kind == "event":
            ev = _client_event(payload)
            client_events.append(ev)
            yield _sse(ev)
        elif kind == "citations":
            citations = payload
            ev = {"type": "citations", "items": payload}
            client_events.append(ev)
            yield _sse(ev)
        elif kind == "answer":
            answer_text = payload
            ev = {"type": "answer", "text": payload}
            client_events.append(ev)
            yield _sse(ev)
        elif kind == "error":
            yield _sse({"type": "error", "message": payload})
        elif kind == "done":
            done_ev = {"type": "done", "run_id": run_id, **(payload or {})}
            client_events.append(done_ev)
            yield _sse(done_ev)
            try:
                _persist_run(run_id, question, answer_text,
                             float((payload or {}).get("latency_s") or 0.0),
                             client_events, citations)
            except Exception as exc:  # noqa: BLE001
                print(f"  run persist failed: {exc}", flush=True)
            break


# ── App + routes ──────────────────────────────────────────────────────────────

app = FastAPI(title="Maskinrummet", docs_url=None, redoc_url=None)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/architecture")
def architecture():
    return JSONResponse(_architecture())


@app.post("/api/ask")
async def ask(request: Request):
    body = await request.json()
    # Accept either {messages:[{role,content}...]} (multi-turn) or {question:"..."}.
    messages = body.get("messages")
    if not messages:
        question = (body.get("question") or "").strip()
        if not question:
            return JSONResponse({"error": "Provide 'question' or 'messages'."}, status_code=400)
        messages = [{"role": "user", "content": question}]
    return StreamingResponse(
        _run_events(messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── E2: graph lens, drill-down, citations, feedback, history ──────────────────

@app.post("/api/graph/subgraph")
async def graph_subgraph(request: Request):
    """Resolve retrieved section refs → structured subgraph (Graflinsen)."""
    body = await request.json()
    refs = body.get("refs") or []
    answer = body.get("answer") or ""
    try:
        return JSONResponse(build_subgraph(refs, answer))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "laws": [], "sections": [], "paragraphs": [], "cites": []})


@app.get("/api/graph/node/{element_id:path}")
def graph_node(element_id: str):
    """Full provision text + validity + ELI uri for the node inspector."""
    try:
        return JSONResponse(node_detail(element_id))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"found": False, "error": str(exc)})


@app.post("/api/run/{run_id}/analyze")
async def run_analyze(run_id: str, request: Request):
    """Cheap AI analysis of one LLM call's context (Feedback-round-1 #3). The
    deterministic search runs client-side; this endpoint answers semantic
    questions, always citing which context block supports the answer."""
    body = await request.json()
    question = (body.get("question") or "").strip()
    context = body.get("context") or ""
    if not question or not context:
        return JSONResponse({"error": "Provide 'question' and 'context'."}, status_code=400)
    prompt = (
        "Du analyserer den KONTEKST, en sprogmodel fik. Svar KUN ud fra konteksten nedenfor. "
        "Er svaret ikke i konteksten, så sig det klart. Citér den relevante sætning ordret.\n\n"
        f"SPØRGSMÅL: {question}\n\n=== KONTEKST ===\n{context[:12000]}"
    )
    try:
        from eval_run import build_judge_llm  # reuse the cheap hosted judge model
    except Exception:
        build_judge_llm = None
    try:
        llm = build_judge_llm() if build_judge_llm else None
        if llm is None:
            return JSONResponse({"error": "Ingen analysemodel tilgængelig."}, status_code=503)
        resp = llm.invoke(prompt)
        text = getattr(resp, "content", None) or str(resp)
        if isinstance(text, list):
            text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
        return JSONResponse({"answer": text})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/traces")
def traces():
    """Recent runs for the history list (newest first)."""
    with _db_lock:
        con = _db()
        rows = con.execute(
            "SELECT run_id, ts, question, provider, latency_s FROM mr_runs ORDER BY ts DESC LIMIT 30"
        ).fetchall()
        con.close()
    return JSONResponse([dict(r) for r in rows])


@app.get("/api/traces/{run_id}")
def trace(run_id: str):
    """A saved run's full event log + citations, for identical replay."""
    with _db_lock:
        con = _db()
        row = con.execute("SELECT * FROM mr_runs WHERE run_id = ?", (run_id,)).fetchone()
        con.close()
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "run_id": row["run_id"], "ts": row["ts"], "question": row["question"],
        "answer": row["answer"], "provider": row["provider"], "latency_s": row["latency_s"],
        "events": json.loads(row["events"] or "[]"),
        "citations": json.loads(row["citations"] or "[]"),
    })


# ── E3: eval lens + tool health ───────────────────────────────────────────────

def _infer_model(name: str, sample: dict) -> str:
    if sample.get("provider"):
        return sample["provider"]
    n = name.lower()
    if "flash" in n:
        return "gemini-2.5-flash"
    for tok, model in (("26b", "gemma4:26b"), ("31b", "gemma4:31b"), ("12b", "gemma4:12b")):
        if tok in n:
            return model
    if "gemma" in n:
        return "gemma4:26b"
    return "ukendt"


def _infer_set(name: str, sample: dict) -> str:
    if sample.get("set_version"):
        return "v" + str(sample["set_version"])
    m = re.search(r"v(\d+(?:\.\d+)?)", name.lower())
    return "v" + m.group(1) if m else "—"


# E4: pillar and tags joined category/difficulty/behavior. `tags` is list-valued,
# so a record contributes to every tag it carries (totals per tag row, not per
# record) — that is what makes "how do the f1_gate items do?" answerable.
_DIMS = [
    ("category", "category"),
    ("difficulty", "difficulty"),
    ("expected_behavior", "behavior"),
    ("pillar", "pillar"),
]
_LIST_DIMS = [("tags", "tags")]

GOLDEN_PATH = Path(__file__).parent / "eval_golden_set.json"


def _load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _gate_templates() -> dict[str, str]:
    """{template text -> flag}. A gated run's answer IS one of these verbatim, so
    template equality is how eval views detect gating — eval records do NOT carry
    tool_events (E3 gap, traps index), so counting tool_calls would report zero
    for every row."""
    return {v.strip(): k for k, v in agent_app.SCOPE_TEMPLATES.items()}


def _gate_flag(answer: str) -> str | None:
    return _gate_templates().get((answer or "").strip())


def _scan_eval_file(path: Path) -> dict | None:
    """Summarise one eval_results_*.jsonl: mean pass, repeat count, and a
    pass-% breakdown per dimension. Returns None for empty/non-standard files."""
    records: list[dict] = []
    first: dict | None = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if first is None:
                first = r
            if "item" in r and "scores" in r:
                records.append(r)
    except Exception:
        return None
    if not records or first is None:
        return None

    # Count DISTINCT run_idx values, not the max: eval_run --repeat N writes
    # run_idx 1..N in one file (N repeats), but the resumable gemma driver wrote
    # one run per file stamped run_idx=r, so max() would wrongly report N repeats.
    runs = len({r.get("run_idx", 1) for r in records}) or 1
    item_ids = {r["item"]["id"] for r in records}
    passes = sum(1 for r in records if r["scores"].get("overall_pass"))
    dims: dict[str, list[dict]] = {}
    for field, label in _DIMS:
        agg: dict[str, list[int]] = {}
        for r in records:
            v = r["item"].get(field) or "—"
            a = agg.setdefault(v, [0, 0])
            a[1] += 1
            if r["scores"].get("overall_pass"):
                a[0] += 1
        dims[label] = [{"value": v, "pass": a[0], "total": a[1]} for v, a in sorted(agg.items())]
    for field, label in _LIST_DIMS:
        agg = {}
        for r in records:
            for v in (r["item"].get(field) or []):
                a = agg.setdefault(str(v), [0, 0])
                a[1] += 1
                if r["scores"].get("overall_pass"):
                    a[0] += 1
        # tags are long-tailed; surface the ones with enough mass to read
        dims[label] = [{"value": v, "pass": a[0], "total": a[1]}
                       for v, a in sorted(agg.items(), key=lambda kv: (-kv[1][1], kv[0]))
                       if a[1] >= 2]

    gated = sum(1 for r in records if _gate_flag(r.get("answer", "")))

    ts = first.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
    return {
        "name": path.name,
        "model": _infer_model(path.name, first),
        "set_version": _infer_set(path.name, first),
        "git_sha": first.get("git_sha") or "—",
        "ts": ts,
        "repeat": runs,
        "n_items": len(item_ids),
        "n_records": len(records),
        "mean_pass": round(passes / runs, 1) if runs else 0,
        "pass_pct": round(100 * passes / len(records)) if records else 0,
        "dims": dims,
        "gated": gated,          # records answered by the F1 scope gate
    }


@app.get("/api/eval/runs")
def eval_runs():
    base = Path(__file__).parent
    out = []
    for p in base.glob("eval_results_*.jsonl"):
        if p.stat().st_size == 0:
            continue
        s = _scan_eval_file(p)
        if s:
            out.append(s)
    out.sort(key=lambda r: r["ts"], reverse=True)
    return JSONResponse(out)


@app.get("/api/eval/runs/{name}")
def eval_run_items(name: str):
    """Per-item detail for one eval file: pass-frequency across repeats + the
    last answer, for the drill-down."""
    if "/" in name or ".." in name or not name.startswith("eval_results_"):
        return JSONResponse({"error": "bad name"}, status_code=400)
    path = Path(__file__).parent / name
    if not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    by_item: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if "item" not in r or "scores" not in r:
            continue
        it = r["item"]
        e = by_item.setdefault(it["id"], {
            "id": it["id"], "category": it.get("category"), "difficulty": it.get("difficulty"),
            "expected_behavior": it.get("expected_behavior"), "question": it.get("question", ""),
            "runs": 0, "passes": 0, "answer": "", "scores": {},
        })
        e["runs"] += 1
        if r["scores"].get("overall_pass"):
            e["passes"] += 1
        e["answer"] = r.get("answer", "")
        e["gate_flag"] = _gate_flag(r.get("answer", ""))   # E4: mark gated rows
        sc = r["scores"]
        e["scores"] = {
            "must_contain": sc.get("must_contain_pass"), "must_not_contain": sc.get("must_not_contain_pass"),
            "behavior": sc.get("behavior_match"), "citation": sc.get("citation_pass"),
            "detected_behavior": sc.get("detected_behavior"),
        }
    items = sorted(by_item.values(), key=lambda x: x["id"])
    return JSONResponse({"name": name, "items": items})


@app.get("/api/eval/fixtures")
def eval_fixtures():
    """Scope-classifier fixture baselines (eval_fixtures_scope_*.jsonl).

    A third results shape: no agent, no tools — one classifier verdict per item.
    Kept separate from /api/eval/runs on purpose; folding it into the run scanner
    would mix a zero-LLM L0 rung into agent-run statistics."""
    base = Path(__file__).parent
    out = []
    for p in sorted(base.glob("eval_fixtures_scope_*.jsonl")):
        rows = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        if not rows:
            continue
        first = rows[0]
        by_flag: dict[str, list[int]] = {}
        for r in rows:
            key = r.get("expect") or "ingen flag"
            a = by_flag.setdefault(key, [0, 0])
            a[1] += 1
            if r.get("pass"):
                a[0] += 1
        in_scope = [r for r in rows if not r.get("expect")]
        out.append({
            "name": p.name,
            # unstamped pre-2026-08-08 baselines fall back to "—" rather than lie
            "classifier_model": first.get("classifier_model") or "—",
            "git_sha": first.get("git_sha") or "—",
            "set_version": first.get("set_version") or "—",
            "ts": first.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime(p.stat().st_mtime)),
            "n": len(rows),
            "passed": sum(1 for r in rows if r.get("pass")),
            "errors": sum(1 for r in rows if r.get("error")),
            # the number this fixture exists to protect
            "false_positives": sum(1 for r in in_scope if r.get("got")),
            "in_scope": len(in_scope),
            "by_flag": [{"value": k, "pass": v[0], "total": v[1]}
                        for k, v in sorted(by_flag.items())],
        })
    out.sort(key=lambda r: r["ts"], reverse=True)
    return JSONResponse(out)


# ── E4: golden-set browser ────────────────────────────────────────────────────

@app.get("/api/eval/golden")
def eval_golden(q: str = "", dim: str = "", value: str = ""):
    """Serve the golden-set item definitions themselves (read-only).

    The Eval lens previously only ever saw items echoed from *result* files, so
    an item that had never been run was invisible. Optional filters: `q` is a
    free-text search over id/question/expected_answer/notes; `dim`+`value` filter
    on any scalar field or on `tags` (list-valued)."""
    try:
        gs = _load_golden()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"cannot read golden set: {exc}"}, status_code=500)
    items = gs.get("items", [])

    if dim and value:
        if dim == "tags":
            items = [i for i in items if value in (i.get("tags") or [])]
        else:
            items = [i for i in items if str(i.get(dim) or "") == value]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in json.dumps(
            {k: i.get(k) for k in ("id", "question", "expected_answer", "notes", "tags")},
            ensure_ascii=False).lower()]

    def facet(field: str) -> list[dict]:
        agg: dict[str, int] = {}
        for i in gs.get("items", []):
            agg[str(i.get(field) or "—")] = agg.get(str(i.get(field) or "—"), 0) + 1
        return [{"value": v, "count": c} for v, c in sorted(agg.items())]

    tag_agg: dict[str, int] = {}
    for i in gs.get("items", []):
        for t in (i.get("tags") or []):
            tag_agg[str(t)] = tag_agg.get(str(t), 0) + 1

    return JSONResponse({
        "metadata": gs.get("metadata", {}),
        "total": len(gs.get("items", [])),
        "shown": len(items),
        "facets": {
            "category": facet("category"),
            "difficulty": facet("difficulty"),
            "expected_behavior": facet("expected_behavior"),
            "pillar": facet("pillar"),
            "tags": [{"value": v, "count": c}
                     for v, c in sorted(tag_agg.items(), key=lambda kv: (-kv[1], kv[0]))],
        },
        "items": items,
    })


# ── E4: smoke-tier runner ─────────────────────────────────────────────────────
# Deliberately NOT a replacement for the §2 measurement protocol. Full matched
# pairs stay on the CLI (ab_driver.py); this exists so a developer can run one
# item and watch it, and it is capped so a casual full-set run is never one
# click away. Every run costs real API money or GPU time.

EVAL_RUN_MAX_ITEMS = int(os.getenv("EVAL_RUN_MAX_ITEMS", "5"))


@app.post("/api/eval/run")
async def eval_run_smoke(request: Request):
    """Run 1..EVAL_RUN_MAX_ITEMS golden items through the real agent, streaming
    the same SSE event shape as /api/ask so the run is scrubbable in Kredsløbet,
    then score each with the single-sourced scorer (A1) and persist the event log
    to mr_runs — which is what makes an eval item's trace replayable (E3's
    deferred gap, now closed for UI-triggered runs)."""
    body = await request.json()
    ids = [str(i).strip() for i in (body.get("item_ids") or []) if str(i).strip()]
    if not ids:
        return JSONResponse({"error": "Provide item_ids: [...]"}, status_code=400)
    if len(ids) > EVAL_RUN_MAX_ITEMS:
        return JSONResponse(
            {"error": f"Smoke tier is capped at {EVAL_RUN_MAX_ITEMS} items "
                      f"({len(ids)} requested). Full runs belong on the CLI "
                      f"(eval_run.py / ab_driver.py) — see backlog §2."},
            status_code=400)
    try:
        gs = _load_golden()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"cannot read golden set: {exc}"}, status_code=500)
    by_id = {i["id"]: i for i in gs.get("items", [])}
    missing = [i for i in ids if i not in by_id]
    if missing:
        return JSONResponse({"error": f"unknown item ids: {missing}"}, status_code=400)

    items = [by_id[i] for i in ids]
    return StreamingResponse(_eval_run_events(items), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _eval_run_events(items: list[dict]):
    """SSE generator for a smoke run: per item, the normal agent event stream
    (so the UI can light Kredsløbet), then an `eval_item` verdict event."""
    for n, item in enumerate(items, 1):
        yield _sse({"type": "eval_item_start", "index": n, "total": len(items),
                    "id": item["id"], "question": item["question"],
                    "expected_behavior": item.get("expected_behavior")})
        q: queue.Queue = queue.Queue()
        t0 = time.perf_counter()
        run_id = uuid.uuid4().hex[:12]
        client_events: list[dict] = []
        state: dict = {}

        def _worker(_item=item, _q=q, _state=state):
            try:
                sent = 0

                def _on_event(tool_events: list[dict]):
                    nonlocal sent
                    for ev in tool_events[sent:]:
                        _q.put(("event", ev))
                    sent = len(tool_events)

                answer, tool_events = stream_agent_answer(
                    AGENT_EXECUTOR, [{"role": "user", "content": _item["question"]}],
                    on_tool_event=_on_event)
                for ev in tool_events[sent:]:
                    _q.put(("event", ev))
                _state["answer"] = answer
                _state["tool_events"] = tool_events
                _q.put(("done", None))
            except Exception as exc:  # noqa: BLE001
                _state["answer"] = f"[ERROR: {exc}]"
                _state["tool_events"] = []
                _q.put(("error", str(exc)))
                _q.put(("done", None))

        threading.Thread(target=_worker, daemon=True).start()
        start_ev = {"type": "run_start", "run_id": run_id, "provider": PROVIDER,
                    "question": item["question"],
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        client_events.append(start_ev)
        yield _sse(start_ev)

        while True:
            kind, payload = q.get()
            if kind == "event":
                ev = _client_event(payload)
                client_events.append(ev)
                yield _sse(ev)
            elif kind == "error":
                yield _sse({"type": "error", "message": payload})
            elif kind == "done":
                break

        answer = state.get("answer", "")
        tool_events = state.get("tool_events", [])
        latency = round(time.perf_counter() - t0, 3)
        # A1: the same scorer eval_run.py uses — no second implementation.
        scores = agent_app.score_item(item, answer, tool_events)

        ans_ev = {"type": "answer", "text": answer}
        client_events.append(ans_ev)
        yield _sse(ans_ev)
        done_ev = {"type": "done", "run_id": run_id, "latency_s": latency}
        client_events.append(done_ev)
        yield _sse(done_ev)

        # Persist so the item's trace is replayable like any chat turn (E3 gap).
        try:
            _persist_run(run_id, item["question"], answer, latency, client_events,
                         resolve_citations(answer))
        except Exception as exc:  # noqa: BLE001
            print(f"  eval run persist failed: {exc}", flush=True)

        yield _sse({"type": "eval_item", "index": n, "total": len(items),
                    "id": item["id"], "run_id": run_id, "latency_s": latency,
                    "answer": answer, "scores": scores,
                    "gate_flag": _gate_flag(answer)})

    yield _sse({"type": "eval_done", "total": len(items)})


@app.get("/api/tools/health")
def tools_health():
    """Per-tool usage health from persisted live runs (mr_runs): call count,
    empty-result rate, mean duration. Empty output was the tell for the dead
    tools this project has hit before."""
    agg: dict[str, dict] = {}
    with _db_lock:
        con = _db()
        rows = con.execute("SELECT events FROM mr_runs ORDER BY ts DESC LIMIT 200").fetchall()
        con.close()
    for row in rows:
        try:
            events = json.loads(row["events"] or "[]")
        except Exception:
            continue
        for ev in events:
            if ev.get("type") != "tool_result":
                continue
            name = ev.get("tool_name", "?")
            a = agg.setdefault(name, {"calls": 0, "empty": 0, "dur": 0.0, "dur_n": 0})
            a["calls"] += 1
            full = (ev.get("content_full") or "").strip()
            if full in ("", "[]") or full.startswith("[]"):
                a["empty"] += 1
            d = ev.get("duration_s")
            if isinstance(d, (int, float)):
                a["dur"] += d
                a["dur_n"] += 1
    out = [{
        "tool": name, "calls": a["calls"],
        "empty_rate": round(100 * a["empty"] / a["calls"]) if a["calls"] else 0,
        "mean_duration_s": round(a["dur"] / a["dur_n"], 2) if a["dur_n"] else None,
    } for name, a in agg.items()]
    out.sort(key=lambda r: r["calls"], reverse=True)
    return JSONResponse({"tools": out, "n_runs": len(rows)})


@app.post("/api/feedback")
async def feedback(request: Request):
    body = await request.json()
    verdict = body.get("verdict")
    if verdict not in ("up", "down"):
        return JSONResponse({"error": "verdict must be 'up' or 'down'."}, status_code=400)
    with _db_lock:
        con = _db()
        con.execute("INSERT INTO mr_feedback (run_id, verdict, comment) VALUES (?,?,?)",
                    (body.get("run_id"), verdict, (body.get("comment") or "")[:2000]))
        con.commit()
        con.close()
    return JSONResponse({"ok": True})


# ── Static frontend (mounted last so /api/* wins) ─────────────────────────────

if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/")
    def _index():
        return FileResponse(_DIST / "index.html")

    @app.get("/{full_path:path}")
    def _spa(full_path: str):
        # SPA fallback: serve a real file if it exists, else index.html.
        candidate = _DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
else:
    @app.get("/")
    def _no_build():
        return JSONResponse(
            {"message": "Frontend not built. Run `npm --prefix frontend install && "
                        "npm --prefix frontend run build`, or use the Vite dev server on :5173.",
             "api": ["/api/architecture", "/api/ask", "/api/health"]},
        )
