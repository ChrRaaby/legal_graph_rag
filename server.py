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
import sys
import threading
import time
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
from app import build_runtime, stream_agent_answer, resolve_llm_provider  # noqa: E402

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


# ── SSE run ───────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    """One SSE message. json.dumps escapes embedded newlines (e.g. in the model's
    thinking text), so the payload is always a single `data:` line."""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _run_events(messages: list[dict]):
    """Generator of SSE strings for one agent run. stream_agent_answer runs in a
    worker thread; its on_tool_event callback (called with the full, growing
    tool_events list) is diffed into per-event SSE messages, then the final
    answer + done are emitted. Every event is a pure record — the frontend
    rebuilds all four layers as f(event_log, t)."""
    q: queue.Queue = queue.Queue()
    t0 = time.perf_counter()

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
            # Flush trailing events the callback never delivered (the final,
            # tool-call-less llm_call is appended but does not fire on_event).
            for ev in tool_events[sent:]:
                q.put(("event", ev))
            totals = {"input_tokens": 0, "output_tokens": 0}
            for ev in tool_events:
                if ev.get("type") == "llm_call":
                    totals["input_tokens"] += int(ev.get("input_tokens") or 0)
                    totals["output_tokens"] += int(ev.get("output_tokens") or 0)
            q.put(("answer", answer))
            q.put(("done", {"latency_s": round(time.perf_counter() - t0, 3), **totals}))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", str(exc)))
            q.put(("done", {"latency_s": round(time.perf_counter() - t0, 3)}))

    threading.Thread(target=_worker, daemon=True).start()

    yield _sse({
        "type": "run_start",
        "provider": PROVIDER,
        "question": messages[-1]["content"] if messages else "",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    while True:
        kind, payload = q.get()
        if kind == "event":
            ev = dict(payload)
            # Rename llm_call → llm for the client; pass others through as-is.
            if ev.get("type") == "llm_call":
                ev["type"] = "llm"
            yield _sse(ev)
        elif kind == "answer":
            yield _sse({"type": "answer", "text": payload})
        elif kind == "error":
            yield _sse({"type": "error", "message": payload})
        elif kind == "done":
            yield _sse({"type": "done", **(payload or {})})
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
