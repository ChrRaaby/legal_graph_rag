# Maskinrummet — frontend (Phase E1)

Event-driven UI for the Danish tax-law agent. Design + scope: `../whitepapers/frontend_maskinrummet_design.md`
and the V1 mockup `../whitepapers/mockups/maskinrummet_mockup.html`.

**E1 delivered the spine:** chat + **Kredsløbet** (architecture map from runtime
truth, lit live) + **Tidslinjen** (scrub to replay) + `APP_MODE` user/dev.

**E2 added the lenses:**
- **Graflinsen** — the retrieved subgraph (Law → § + CITES cross-references),
  built live as retrieval happens (scrub-revealed), with a **node inspector**
  (click a § → full stk text, gældende/historisk badge, retsinformation link).
  Rendered as Law + § + CITES; stk detail lives in the inspector (a real § can
  have >10 stk — nodes would clutter).
- **Tankestrømmen** — reasoning + tool cards with **token/cost badges**, **I/O
  drill-down** (full tool output; the LLM call's reconstructed context), and
  **context search** (deterministic, instant) with an **AI-analyze** escalation.
- **Chat** — **kilder chips** (law-aware ✓/⚠ verification, ELI deep link, click →
  highlight the § in Graflinsen), **feedback** 👍/👎, and **history** (load and
  replay a past run — identical `(event_log, t)` path).

**E3 added dev depth:**
- **Eval lens** — the golden-set dashboard. A run selector (+ a compare column,
  e.g. gemma vs flash) over `eval_results_*.jsonl`, stat tiles, a **pass-% per
  dimension matrix** (category / behaviour / difficulty, worst cells flagged),
  and an items drill-down (pass-frequency colour-coding, which checks failed,
  last answer). Backed by `/api/eval/runs(+/{name})`.
- **Tool-health table** — per-tool call count, **empty-result rate** (the
  dead-tool detector this project has been bitten by), and mean duration, from
  persisted live runs (`/api/tools/health`).

This is the **primary UI**; the Streamlit `app.py` is legacy (its runtime
functions remain the single source the backend imports).

Everything is a pure function of `(event_log, t)`: the SSE stream fills the event
log; one shared clock (`useRunClock`) drives every layer, so live play and
after-the-fact scrubbing are the same code path.

## Stack note (implementation decision)

React + Vite + TypeScript. The mockup's token-CSS is ported **verbatim**
(`src/styles.css`) rather than reimplemented in Tailwind, and animation is CSS
transitions rather than Framer Motion — this keeps the result pixel-faithful to
the mockup (the agreed ceiling) and the dependency surface minimal. No graph lib
yet (Kredsløbet is hand-drawn SVG; Graflinsen's force graph is an E2 decision).

## Run it

Backend first (serves the API and, in prod, this bundle):

```bash
cd ..                       # legal-legislation-explorer
.venv/bin/pip install -r requirements-server.txt   # once
.venv/bin/uvicorn server:app --port 8000           # dev mode (APP_MODE=dev default)
# APP_MODE=user .venv/bin/uvicorn server:app --port 8000   # end-user mode
```

Frontend, two options:

```bash
# A) Dev server with HMR — proxies /api → :8000
npm install
npm run dev            # → http://localhost:5173

# B) Production build — FastAPI then serves it from /
npm run build          # → dist/, served by uvicorn at http://localhost:8000
```

## Tests

```bash
npm run test:replay    # replay/scrub math vs a real captured gs-025 log (no browser)
npm run test:e2e       # Playwright: real UI + real agent run + scrub (needs server on :8000)
```

`test:replay` is the primary automated check — it validates the `(event_log, t)`
functions (spans, run length, circuit lighting, captions) against
`tests/gs25_events.json`, a real recorded run.

**Playwright note:** in this WSL environment the headless browser needs system
libraries (`libXdamage`, `libgbm`, `libxkbcommon`, …). Install once with
`sudo npx playwright install-deps chromium`, then `npm run test:e2e` with the
server running on :8000. Without those libs the browser cannot launch; the test
file is correct and ready.
