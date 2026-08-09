# Handover prompt — GCP Cloud Run persistence for Maskinrummet

*Written 2026-08-09. Paste the whole of this file to the agent that will do the GCP work.*

---

You are taking over the GCP/Cloud Run side of a Danish tax-law GraphRAG project.
Everything below was verified against the live service and the repo on 2026-08-09 —
treat it as measured fact, not assumption, and re-verify anything you intend to
change.

## The system

- **Repo:** `github.com/ChrRaaby/legal_graph_rag` (branch `main`).
- **Backend:** `server.py` — FastAPI. `app.py` is the single source of the agent
  runtime (LangGraph ReAct agent over a Neo4j Aura graph of Danish tax law) and is
  pure runtime: importing it starts nothing and renders nothing.
- **Frontend:** `frontend/` — React + Vite, built to `frontend/dist`, served by
  FastAPI. Called *Maskinrummet*. Lenses: Kredsløbet (circuit), Graflinsen
  (subgraph), Tankestrømmen (reasoning/tool cards), Tidslinjen (scrub/replay),
  and **Eval** (two sub-tabs: *Testsuite* and *Historik*).
- **Live service:** `https://legal-graph-rag-hggxdvzfya-ew.a.run.app`
  (Cloud Run, europe-west). There is **no cloudbuild.yaml, no deploy script and no
  service YAML in the repo** — only a `Dockerfile`. However the currently deployed
  revision *does* contain code committed on 2026-08-08, so something is deploying
  it (Cloud Build trigger, `gcloud run deploy --source`, or manual). **Find out
  which before you change anything.**

## The problem to solve

**Eval history and run traces do not persist in the cloud.** The Historik tab is
empty on the live service, and any data the app writes at runtime disappears.

### Verified evidence (2026-08-09, against the live URL)

| endpoint | live result | interpretation |
|---|---|---|
| `/api/eval/runs` | `[]` | no run history at all |
| `/api/eval/fixtures` | 1 object, `git_sha 1f98c12-dirty` | **today's code IS deployed** |
| `/api/eval/golden` | v4.2, 69 items | golden set ships fine (it is tracked) |
| `/api/architecture` | `app_mode=user`, `provider=gemini:gemini-2.5-flash`, 11 tools, `pricing` present | current code, but drifted env |
| `/api/traces` | 4 objects | runtime writes work — but only until the container dies |

### Three independent causes — all must be addressed

**1. The run files are gitignored, so they are never in the image.**
`.gitignore` excludes `eval_history/*.jsonl` (and `observability.db`), with two
deliberate exceptions re-included: `eval_fixtures_baseline.jsonl` and
`eval_fixtures_scope_baseline.jsonl`. That is exactly why `/api/eval/fixtures`
returns data on live while `/api/eval/runs` returns `[]` — the fixture baselines
are tracked, the 47 local run files are not. The exclusion is intentional (the
files are large and reproducible); do **not** simply commit them.

**2. Cloud Run's filesystem is ephemeral.** Everything the app writes at runtime
goes into the container and is lost on restart/scale-out, and is not shared
between instances. Two writers matter:
   - `server._persist_run()` → SQLite `observability.db`, tables `mr_runs`
     (question, answer, provider, latency, **full SSE event log**, citations) and
     `feedback`. This is what powers `/api/traces` and trace replay.
   - `server._append_smoke_record()` → `eval_history/eval_results_smoke_<YYYYMMDD>.jsonl`,
     one eval record per UI smoke run.
   Path resolution for the second is `app.eval_artifact_path(name)`: a *relative*
   name lands in `eval_history/`, an *absolute* path passes through untouched.
   That function is the single seam to redirect if you move to object storage.

**3. `APP_MODE=user` is hardcoded in the Dockerfile**, and in user mode the
frontend hides Maskinrummet entirely (`showMaskinrum = isDev || revealed` in
`App.tsx`) — so the Eval tab is not normally reachable on the live service at all.
Decide deliberately whether the cloud deployment should expose it.

## What good looks like

Pick per your judgement, but the owner's stated preference order is:

1. **Short term / cheap:** make the cloud app *honest* — hide or disable Historik
   when `APP_MODE=user`, so an empty tab never looks broken. Optionally commit a
   small curated set of reference runs so Historik has meaningful content.
2. **Real fix:** durable storage.
   - `mr_runs` / `feedback` → **Cloud SQL (Postgres)** or **Firestore**. SQLite on
     a shared volume is not appropriate for a multi-instance service.
   - eval records → **GCS bucket**, read through the same `/api/eval/runs`
     summariser. `eval_artifact_path()` and the two directory globs in `server.py`
     (`eval_runs()`, `eval_fixtures()`, `eval_run_items()`) are the only read/write
     points; all three already fall back to the repo root, so adding a GCS backend
     is contained.
3. Optionally move the graph/model config out of drift (below).

## Constraints and gotchas — read before touching code

- **`app.py` is the single source of the agent runtime.** `server.py` and
  `eval_run.py` import from it. Do not fork agent logic into the server, and do
  not add a UI layer to `app.py` (its Streamlit UI was deliberately deleted on
  2026-08-08).
- **`stream_agent_answer()` must keep returning the `(answer, tool_events)`
  2-tuple**, and `build_runtime()` must keep returning
  `(analysis, agent_executor, tools)` — `eval_run.py` depends on both.
- **Secrets:** `.env` is in `.dockerignore` and must never be baked into an image
  or committed. Required at runtime: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`,
  `GOOGLE_API_KEY`. Use **Secret Manager** wired to Cloud Run env vars. Optional
  behaviour flags the app reads: `APP_MODE`, `LLM_PROVIDER`, `GEMINI_MODEL`,
  `GEMINI_MODELS`, `SCOPE_CLASSIFIER_MODEL`, `SCOPE_HISTORY_MESSAGES`,
  `F_SCOPE_GUARD`, `EVAL_RUN_MAX_ITEMS`, `AGENT_RETRIEVAL_K`,
  `AGENT_HISTORY_MESSAGES`, `NEO4J_DATABASE`, plus experiment hatches
  `C2_DIRECT_NARROW`, `C3_TOOL_PRUNE`, `C5_PROMPT_LEAN`, `C7_ROW_DEDUP`.
  `OLLAMA_*` are local-GPU only and irrelevant in Cloud Run.
- **Neo4j Aura free tier pauses when idle** and drops connections. Retry wrappers
  exist (`_retry_on_connection` in app.py, `_build_with_retry` in server.py) —
  keep them; a cold Aura is not a code bug.
- **Cost telemetry is server-authoritative.** `app.py` owns
  `PRICE_USD_PER_MTOK` / `cost_dkk()` / `token_usage()` and serves the table via
  `/api/architecture`; the frontend installs it with `setPricing()`. Do not
  reintroduce a client-side price table — the previous one silently drifted to
  wrong model ids.
- **The smoke runner is capped** (`EVAL_RUN_MAX_ITEMS`, default 5) on purpose:
  each run costs real API money. Do not raise it for a public deployment; if
  anything, disable the runner when `APP_MODE=user`.
- **PII handling:** the app has a scope gate that blocks prompts containing
  personal data, and `redact_if_pii()` ensures such prompts persist as
  `[REDACTED-PII]`. **Any new storage backend must preserve that redaction** —
  route writes through `server._persist_run()` rather than writing records
  directly.
- **Image size:** the `Dockerfile` does `COPY . .` with a minimal `.dockerignore`
  (`.venv`, `node_modules`, `__pycache__`, `.git`, `.env`). That currently drags in
  ~37 MB of material irrelevant to serving: `eval_history/` (11 MB),
  `danish_json_out/` (13 MB), `whitepapers/` (7.8 MB), `_uk_archive/` (3.3 MB),
  `renderings/` (2.2 MB). Tightening `.dockerignore` is an easy win — but note
  that excluding `eval_history/` would also drop the two tracked fixture baselines
  that `/api/eval/fixtures` currently serves, so exclude selectively.
- The Dockerfile builds on **python:3.10-slim** while local dev runs **3.13**, and
  installs `requirements.txt` + `requirements-server.txt`. `streamlit`, `altair`,
  `pandas` and `neo4j_viz` were removed as dependencies on 2026-08-08; if the
  image still installs them, the requirements copy in the image is stale.

## Configuration drift to reconcile

The live service reports `provider=gemini:gemini-2.5-flash` and **11 tools**;
local runs `gemini:gemini-3.5-flash` with **12 tools**. So the Cloud Run env vars
differ from local `.env`. This matters beyond tidiness: **the deployed agent is
not running the substrate the evals measure**, so eval results do not describe
production behaviour. Reconcile deliberately — decide which is intended and set it
explicitly, pinning full model ids (the project has been burned by alias
re-pointing; never use `gemini-flash-latest`-style aliases).

## Definition of done

1. State clearly how the service is currently built and deployed.
2. Traces and eval records survive a Cloud Run restart and are consistent across
   instances.
3. Historik on the live URL either shows real data or is honestly hidden in user
   mode — never an empty tab that reads as broken.
4. Secrets come from Secret Manager; no credential is in an image or in git.
5. Provider/tool config on live matches an explicit, documented intent.
6. Local development is unaffected: `uvicorn server:app` against local files must
   still work with no GCP credentials present.

## Verifying your work

```bash
# local, from the repo root
.venv/bin/python3 -m uvicorn server:app --port 8000
python3 scratchpad/smoke_history_verify.py     # smoke → history → replay
python3 scratchpad/cost_verify.py              # tokens + cost on all six paths
cd frontend && npm run build && npm run test:replay && npm run test:e2e
```

Reference reading in the repo: `CLAUDE.md` (project rules), `TODO.md` (open work),
`IMPROVEMENT_BACKLOG.md` (§0 ground rules and the **traps index** — read the traps
index before debugging anything surprising), `eval_history/README.md`.
