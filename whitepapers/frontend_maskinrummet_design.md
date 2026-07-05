# Maskinrummet — frontend redesign (design doc)

**Status:** design complete (Fable 5, 2026-07-05); implementation = backlog **Phase E** (Opus), gated on user go.
**Visual mockup:** `whitepapers/mockups/maskinrummet_mockup.html` (also published as an Artifact) — the mockup IS the V1 scope definition; build what it shows, resist adding more.

## Why leave Streamlit

Streamlit's rerun-the-script model fights everything this product wants to become: no event-driven updates (the live trace is a JSON dump in an expander), custom visuals live in sandboxed iframes (the Mermaid diagrams via CDN), no real client state (scrubbing/replay impossible), and the primary/secondary use-case separation is bolted on. The new frontend is an event-driven web app where **explaining the agent is the product**, not a debug panel.

## The one big idea: the architecture diagram is the stage

Today the architecture drawing, the request trace, and the tool catalog are three disconnected views (two of them factually stale — hardcoded "13 tools", "Gemini 2.5 Flash"). In Maskinrummet they are **one surface**:

> **Kredsløbet** (the circuit): a map of the real system — Bruger → Agent (LLM) → værktøjsrækken → Neo4j/embeddings — generated at runtime from the actual tool list and provider (`GET /api/architecture`), so it can never lie. **Idle**, it's an explorable architecture diagram (click a tool → its description and schema). **During a question**, the same map animates: the query flows in, the LLM node pulses while thinking, edges light up as tool calls travel to the database, and the answer flows back. **Afterwards**, a timeline scrubber replays any moment.

Everything else hangs off this: understanding the system and watching it work are the same act.

## The four synchronized layers

All layers are pure functions of `(event_log, t)` — one shared clock, so play/pause/scrub replay everything in lockstep, and any **saved trace replays identically** (the SQLite traces table already stores event logs → free historical replay).

1. **Kredsløbet** — the live architecture map (above). Caption bar narrates the current event in Danish ("Søger: medarbejderaktier…").
2. **Graflinsen** — *what the agent sees in the graph*: the retrieved subgraph materializes as results arrive — Lov → Kapitel → § → stk. nodes (hierarchy depth = ordinal blue ramp), CITES edges as magenta cross-links, retrieved nodes glowing, used-in-answer nodes ringed in brass. Backed by `GET /api/graph/subgraph?ids=…` resolving retrieval hits to their hierarchy + CITES neighbors.
3. **Tankestrømmen** — the model's reasoning as a live monologue (Gemini `include_thoughts` / Ollama `<think>` — already captured in `_extract_llm_thinking`), interleaved with tool I/O cards (args + result preview, mono).
4. **Tidslinjen** — the waterfall reborn as a scrubber: LLM spans (violet) and tool spans (aqua) on one track; dragging the playhead re-renders layers 1–3 at that instant.

## Layout & modes

- **Split view:** chat left (~38%), Maskinrummet right with tab rail (Kredsløb / Graflinse / Tankestrøm), Tidslinjen docked under the right pane.
- **Chat** is the primary use case, Danish-first: kilder chips with deterministic graph-verification (✓/⚠, deep link to retsinformation.dk ELI), click a chip → highlights that node in Graflinsen; example-question chips on empty state; disclaimer footer. (Backlog B3/B4/B5/B7 become native features here.)
- **`APP_MODE=user`**: chat full-width; a "Se, hvordan jeg fandt svaret" button slides Maskinrummet in — observability as a *trust* feature, not a debug leak. Provider pinned, no dev routes.
- **`APP_MODE=dev`**: everything, plus (Phase E3) the eval dashboard (golden-set grid, pass/flaky heatmap, judge columns, click item → replay its trace) and the tool-health table (call counts, empty-result rate — from observability.db).

## Event protocol (SSE)

`POST /api/ask` → `text/event-stream` of typed JSON events, mapped 1:1 from what `stream_agent_answer` already produces:

| event | payload | source today |
|---|---|---|
| `run_start` | question, provider, ts | — |
| `llm_start` / `llm_end` | duration, in/out tokens, `thinking` | `llm_call` events |
| `tool_call` | tool, args | `tool_call` events |
| `tool_result` | tool, duration, preview, `graph_refs` (resolved node ids for Graflinsen) | `tool_result` events |
| `answer` | final text (Phase 2: `answer_token` deltas via LangGraph `stream_mode="messages"`) | return value |
| `citations` | per-§ verification results (law-aware) | `validate_citations` |
| `done` | latency, totals | — |

## Backend: `server.py` (FastAPI) — app.py stays the single source

Same proven pattern as `eval_run.py`: stub `streamlit` in `sys.modules`, import `build_runtime` / `stream_agent_answer` from `app`. **No agent logic moves.** Endpoints: `POST /api/ask` (SSE via worker thread + queue), `GET /api/architecture` (tools, provider, graph stats — runtime truth), `GET /api/graph/subgraph`, `GET /api/traces` (+ replay), `GET /api/eval/*` (dev), `POST /api/feedback` (B6). Serves the static frontend build → **one container, unchanged Cloud Run plan** (`APP_MODE` env). The Streamlit app remains as dev fallback during E1–E2, then retires.

## Frontend stack

Vite + React + TypeScript + Tailwind; Framer Motion for circuit animation; graph rendering: `sigma.js` or `react-force-graph` (decide at implementation; requirements: ≤200 nodes, custom node glow/ring states, click-select, no physics jitter after settle); timeline: hand-rolled SVG + d3-scale. Playwright smoke test: ask the gs-001 question against a live backend, assert events render. (CSP constraints apply only to the design mockup artifact, not the real app — normal npm deps are fine.)

## Visual identity — "nordisk instrumentbord"

Calm dark control-room as the hero theme (light theme fully supported, token-level). No neon, no purple-gradient AI slop; the drama comes from *motion with restraint* (200–400 ms eases, `prefers-reduced-motion` honored: jump-to-state, scrub still works).

- **Surfaces (dark):** page `#0B1220` (night-navy, hue-biased toward the blues), panel `#111B2C`, hairline `rgba(148,170,200,.14)`; ink `#E9EEF5` / `#A7B4C6` / `#6C7A8F`. Light: page `#F7F6F2`, panel `#FFF`, ink `#14202E`.
- **Accent (the one bold spend):** brass `#E8A33D` (light theme: `#B87A17`) — the § glyph, active-node glow, playhead, used-in-answer rings. Reserved for "live/selected"; never a data series.
- **Data colors** (validated dataviz reference palette; color follows entity, fixed): Agent/LLM violet `#9085e9`/`#4a3aa7`; tools aqua `#199e70`/`#1baf7a`; graph hierarchy = blue **ordinal ramp** by depth (dark: Lov `#1c5cab` → Kapitel `#2a78d6` → § `#3987e5` → stk `#86b6ef`); CITES magenta `#d55181`/`#e87ba4`; status good/critical `#0ca30c`/`#d03b3b`. Every node direct-labeled (relief rule — identity never color-alone).
- **Type:** display serif for identity/§ (Palatino stack — Danish legal gravitas), system sans for UI, mono for payloads. `tabular-nums` on the timeline.

## Phasing (Phase E in the backlog)

- **E1 — the spine:** `server.py` + chat + Kredsløbet (runtime-truth map, live events) + Tidslinjen with replay. Acceptance: ask gs-025's question, watch the run live, scrub it afterwards; APP_MODE both work.
- **E2 — the lenses:** Graflinsen (subgraph endpoint + view), Tankestrømmen, kilder chips with verification + graph-highlight linking, feedback buttons, historical trace replay.
- **E3 — dev depth:** eval dashboard (golden-set grid + judge + flakiness from the `--repeat` artifacts), tool-health table, retire Streamlit.

Model triage: design = done here (Fable). E1–E3 implementation = **Opus** (this doc + the mockup are the spec); visual-polish iterations with the user. Escalation rule from backlog §0.5 applies.

## Relationship to Phase B

B3 (Kilder), B4 (progressive status), B5 (examples), B6 (feedback), B7 (disclaimer) are **absorbed into Phase E** as native features — do not build them in Streamlit. B1 (`APP_MODE`) survives as a backend concept. If the GCP move resumes before E1 lands, deploy the Streamlit app with a minimal B1 gate as the interim; otherwise skip straight to E.

## Risks

- **Two frontends drift** during E1–E2 → agent logic only ever in app.py; Streamlit is dev-only from E1 and deleted in E3.
- **Animation as noise** → every motion must encode a real event; nothing loops decoratively; the mockup defines the ceiling.
- **Scope creep** → the mockup is the V1 contract. New ideas go to the backlog, not the sprint.
- **SSE through Cloud Run** → supported (HTTP streaming); set sensible request timeout; long runs already bounded by the agent's own latency.
