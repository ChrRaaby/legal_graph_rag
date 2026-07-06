# Improvement Backlog — execution plan

**Audience:** a Claude Code session (Opus/Sonnet) executing over the user's next few dev sessions.
**Origin:** full project review 2026-07-04 (Fable 5), incorporating a season of measured experiments. Line anchors are as of commit `0551739` — re-locate by function name if they've drifted.
**How to use:** work top-down within a phase; phases A→B are safe parallel tracks, C requires the measurement protocol, D requires user coordination. Check off tasks here (edit this file) as they land. One task = one commit.

---

## 0. Ground rules (NON-NEGOTIABLE — read before any change)

These encode hard-won experimental evidence. Violating them re-runs failed experiments.

1. **Never force-inject extra data into retrieval output.** Three enrichments (current-first re-ranking, kilde/gyldighed labels, CITES krydshenvisninger) were each measured net-negative-or-neutral on gemma-26B and reverted in `0551739`. The model cannot absorb extra context in the generation call. Extra graph data goes into **on-demand tools** or **narrowing/filtering steps** — never appended to `retrieve_text_with_context` rows. (Removing wrong/irrelevant rows is fine — that's narrowing.)
2. **Never hardcode rates/amounts/thresholds/legal facts in the system prompt.** Retrieve from the graph. (One existing violation is task D1/C5's target.)
3. **Do not prompt-tune to fix individual eval items.** Aggressive/assertive prompt additions churn small models (measured: an assertive citation prompt regressed flash 21→17). Prefer deterministic structure (graph, tools, guards).
4. **`stream_agent_answer()` must keep returning the `(answer, tool_events)` 2-tuple** and `build_runtime()` must keep returning `(analysis, agent_executor, tools)` — `eval_run.py` depends on both.
5. **Keep `app.py` as the single source** for the agent runtime. `eval_run.py` stubs Streamlit and imports from `app`. Do not refactor app.py into a package. (A shared function should live in app.py and be imported by eval_run.py, since the import direction is eval_run→app.)
6. **Any change the agent can see (tools, prompts, retrieval) must be measured** with the protocol in §2 before it's kept. UI-only and scoring-infrastructure changes don't need eval runs.
7. **Ask the user before using the local GPU** (Ollama runs, re-vectorization). The 4090 is often in use. Never start multi-hour eval runs without asking.
8. **Never print/echo `.env`** (contains real NEO4J_PASSWORD, GOOGLE_API_KEY). Never commit it.
9. **Aura free tier drops connections and hangs at startup.** Retry wrappers exist (`_retry_on_connection` in app.py, `build_runtime_with_retry` in eval_run.py) — don't remove them; don't misdiagnose a drop as a code bug.
10. **Kill eval runs by PID**, never `pkill -f eval_run` (it matches its own shell and self-kills).
11. **Gated items** (bottom of file) need explicit user approval before starting.

## 0.5 Who runs what — model triage (Fable 5 vs Opus/Sonnet)

This plan is written so a mid-tier model can execute it safely: the ground rules, anchors, verify steps, and measurement protocol are the guardrails. The split is about **where judgment, legal assertion, or direction-setting happens** — not task size. Rule of thumb: *Opus/Sonnet execute the written plan; Fable writes the next plan.*

| Work | Model | Why |
|---|---|---|
| Phase A (A1–A4) | **Sonnet** | Mechanical, explicit verify steps |
| Phase B UI (B1–B7) | **Sonnet**; B3 Kilder panel → **Opus** | Visually verifiable, low blast radius; B3 has design surface |
| C1–C3 implementation + protocol runs | **Opus** | Designs fully specified here |
| C-phase **keep/revert verdicts** | **Fable** (or user) | Cheap but pivotal: Opus runs the experiment and saves artifacts; a short Fable session reads the judge deltas and decides. This is where the "enrichment hurts the 26B" class of insight comes from |
| C4 reranker: run → interpret | **Opus** runs, **Fable** interprets | Outcome decides the whole retrieval roadmap (narrowing pipeline vs model swap) |
| C5 prompt pruning | **Fable only** | Most failure-prone change class in project history (two bad prompt regressions); scorer-fitting vs load-bearing is pure judgment |
| D1/D2 law loading | **Opus** | Operational pipeline; STOP and escalate if crawler output looks structurally odd (1922-statsskattelov stub lesson) |
| D3-style golden-set authoring | **Fable** + user review gate | Asserts legal content. Template: the 2026-07-05 v4.0 session (verify every anchor against graph/corpus, self-test expected_answers with the real scorer, gate on user review) |
| D4 re-chunking | **Opus** | Protocol-covered |
| D5 rate-table **design** | **Fable**; implementation → Opus | Data-modeling from garbled dual-column rows (see gs-047 note) is judgment work |
| Gated items (concept layer, Qwen3/flash-3.5 swap evaluation and interpretation) | **Fable** | Legal doctrine + architecture direction |
| Measurement mechanics (5× runs, judge re-scores, baselines) | **Sonnet** | Cookbook, commands in §2 |
| GCP migration (when resumed) | **Sonnet/Opus** | Decisions locked in memory todo |

**Escalation rule:** if a delegated session hits something these ground rules don't cover, or a result contradicts expectations (e.g. judge drops when it shouldn't), STOP — don't improvise a fix. Record the artifacts and hand the decision to a Fable session (or the user). Root-cause hunts on surprising results have historically produced the project's most valuable findings; they are not mid-tier work.

## 1. Established baselines (what "better" means)

| Config | Deterministic score | Judge score |
|---|---|---|
| gemma4:26b local (Ollama), lean retrieval | **18.8/30** (5×, stdev 0.4) | ~11–13/30 |
| gemini-2.5-flash, lean retrieval | 21/30 | 13/30 |

- The **judge score is the real quality metric**; the substring scorer overcounts and cannot see phrasing shifts. A structural change that moves deterministic ±2 but judge 0 is noise.
- gemma-26B is non-deterministic even at temperature=0, seed=0 — single-run deltas of ±2–3 items are noise. Only trust `--repeat 5` means and judge re-scores.

## 2. Measurement protocol (for every Phase-C task)

```bash
cd legal-legislation-explorer
# Provider selection via .env: LLM_PROVIDER=gemini:gemini-2.5-flash (hosted)
#   or LLM_PROVIDER=ollama + OLLAMA_MODEL=gemma4:26b (local; OLLAMA_BASE_URL=http://172.21.64.1:11434 — Windows-host IP, NOT localhost, this is WSL2)
# Quick smoke (2 items, hosted):
.venv/bin/python3 eval_run.py --item-ids gs-001,gs-002 --output /tmp/smoke.jsonl
# Full measured run (ask user first if local GPU):
.venv/bin/python3 eval_run.py --repeat 5 --output eval_results_<change>_5x.jsonl
# Judge-gated scoring:
.venv/bin/python3 eval_run.py --scorer judge --output eval_results_<change>_judge.jsonl
```

**Cheap offline judge re-score** (no agent re-run, ~60 API calls): stub `app` in `sys.modules` (MagicMock for streamlit-dependent parts), import `eval_run` as a module, call `build_judge_llm()` (env `JUDGE_MODEL`, default gemini-2.5-flash; use `gemini-2.5-pro` when the agent itself is flash, for independence), then `llm_judge(item, saved_answer, judge_llm)` over a saved `eval_results_*.jsonl`, compare `judge_pass` counts old vs new. This is how all reverts this season were decided.

**Decision rule:** keep a change only if judge improves, or judge is flat AND the change is a pure narrowing/removal with a determinism or latency win. Otherwise revert (keep the branch/commit for the record, as with `0551739`).

---

## Phase A — Deterministic fixes (no eval runs; do these first)

### A1. Fix the in-app Evaluation panel scorer (it crashes on the current golden set) ☐
- **Bug (verified):** `app.py` carries a stale copy of the scorer (`_eval_normalize`/`_eval_detect_behavior`/`_eval_score_item`, app.py:1650–1716, comment "ported from eval_run.py"). 12/30 golden-set items now use **any-of lists** in `must_contain` (e.g. gs-002 `[["48.300","67.500"]]`); the stale copy calls `.lower()` on a list → `AttributeError`. It also lacks numeric word-boundaries and uses a wrong behavior-priority order.
- **Fix:** single-source the scoring. Because eval_run imports app (not vice versa), **move the current implementations from eval_run.py into app.py** — `_normalize`, `_term_label`, `_term_present`, `detect_behavior` (+ `BEHAVIOR_PRIORITY`, `SUBSTANTIVE_BEHAVIORS`), `behavior_matches`, `score_item` — replacing app.py's stale `_eval_*` copies, and have eval_run.py import them from app. Delete the duplicates in eval_run.py.
- **Verify:** `python -c` score a synthetic answer against gs-002 via the app-imported function (no crash, any-of works); then `eval_run.py --item-ids gs-002,gs-021 --output /tmp/a1.jsonl` produces identical scores to before (this is pure refactor — CLI behavior must not change).

### A2. Delete dead code `_attach_kilde` ☐
- app.py:205–230, unused since the `0551739` revert. `grep -n _attach_kilde app.py` must return nothing afterwards.

### A3. Make `validate_citations` law-aware ☐
- **Now:** app.py:1743–1758 extracts § refs from the answer and checks `MATCH (s:Section) WHERE s.number = $num` — **against any law**, so "MOMSL § 33" cited where the answer meant PSL still shows ✅.
- **Fix:** parse the law context per § the same way `_verify_section_references` (app.py:266) does — track the nearest preceding law-name (regex `([a-zæøå]{4,}lov)(?:en|ens|s)?\b` plus the abbreviation aliases in `build_cites_edges.py` `ALIASES`); when a law is identified, constrain the § lookup to that law's versions; fall back to any-law match (current behavior) when no law is named. Return the law used, for display.
- This function becomes the engine of the Kilder panel (B3) — do A3 before B3.
- **Verify:** unit-style check: an answer citing "ligningslovens § 9 C" → found=True with law=Ligningsloven; "momslovens § 9 C" → found=False.

### A4. Architecture view: stop lying about the runtime ☐
- The System Overview mermaid (app.py:1793–1811) hardcodes "13 StructuredTools" and "LLM - Gemini 2.5 Flash"; there are **15 tools** and the provider is dynamic. Build those two labels from `len(tools)` and the selected provider string. Trivial; visual check only.

---

## Phase B — UI: separate primary use case (tax Q&A) from secondary (debug/eval)

User's explicit design goal. Primary = a clean Danish tax-question assistant; secondary = the developer's window into the solution. All UI-only — no eval runs; verify by running the app (`.venv/bin/streamlit run app.py`) and eyeballing both modes.

### B1. `APP_MODE=user|dev` gate ☐  (do first — everything else in B hangs off it)
- Env var, default `dev` locally (`APP_MODE` read next to the other env vars ~app.py:153). Future Cloud Run deploy sets `user`.
- **user mode:** ONLY the chat. No sidebar view radio (or a sidebar with just branding + "ryd samtale"), **no LLM-provider selector** (pin to `LLM_PROVIDER` from env), **no `_render_global_metrics` header** (app.py:2905 — currently graph stats render above the chat on every view), no "Tool trace (live)" expander (app.py:2939), no raw `tool_events` JSON on history messages (app.py:2927–2929). Danish-only strings (title is currently "Dansk Skattelovgivning — Graph Agent" + English caption/input placeholder — make them Danish in user mode). Citation validation stays but presented as B3's Kilder panel.
- **dev mode:** everything as today.
- Implementation: one `IS_DEV = os.getenv("APP_MODE", "dev") != "user"` and `if IS_DEV:` gates around the sidebar/header/expander blocks. Keep it boring.

### B2. Group the dev sidebar ☐
- Replace the flat 12-option radio (app.py:2566) with grouped navigation: **Assistent** (Chat) / **Indsigt** (Request Trace, Evaluation, Tools, Architecture) / **Graf** (Point in Time, Version Timeline, The Complete Graph, Legislation Graph, Parts, Commentaries, Supersedes). Simplest robust version: a two-level select (group radio + view radio) or `st.navigation` with `st.Page(callable)` — st.Page accepts functions so app.py stays one file. If `st.navigation` fights the existing structure, the two-level radio is fine; don't over-engineer.

### B3. "Kilder" panel — the trust feature (bridge between both use cases) ☐
- Under each assistant answer, replace the buried "Citation validation" expander (app.py:2959–2964) with a visible **Kilder** block:
  - Each § cited in the answer as a chip: `✅ Ligningsloven § 9 C` (law-aware verification from A3) or `⚠️ ikke fundet i grafen`.
  - Deep link per chip to `https://www.retsinformation.dk/eli/lta/{year}/{number}` (the Legislation `uri` is in the graph — A3's law resolution should return it).
  - Expandable: the retrieved passages that supported the answer — pull from `tool_events` (the `tool_result` `content_preview` for retrieval tools). Same data as the debug trace, reframed as user value.
- Works in both modes; in dev mode it complements (not replaces) the trace.

### B4. Progressive status during generation ☐
- Local answers take 30–60s behind a mute "Running agent..." spinner (app.py:2946). The `on_tool_event` callback already fires on every event — use it to update the `st.status` label with human lines: `🔍 Søger: <q-arg>…` on tool_call, `📖 Læste <n> uddrag` on tool_result, `✍️ Formulerer svar…` on final llm_call. Map tool names → Danish verbs; truncate args. Biggest perceived-latency win available; zero agent changes.

### B5. Empty-state example questions ☐
- On first load (only the greeting message), render 3–4 `st.button` chips with example questions drawn from golden-set `typical` items (e.g. gs-001 kørselsfradrag). Clicking submits it as the prompt. Skip complex suggestion logic.

### B6. Feedback capture ☐
- 👍/👎 (+ optional comment via `st.popover`) under each answer → new `feedback` table in `observability.db` (`ts, question, answer, verdict, comment, provider`). Add a small "Feedback" section in the dev Evaluation view listing rows. Purpose: real-usage mining for future golden-set items.

### B7. Disclaimer ☐
- Persistent caption in user mode (and on chat in dev): "Svar er vejledende og genereret af en AI ud fra lovtekster — ikke juridisk rådgivning." Non-optional for a deployable tax-law app.

---

## Phase C — Agent-visible quality experiments (measurement protocol REQUIRED, one at a time)

Ranked by expected value. For each: implement → smoke (`--item-ids`) → full protocol (§2) → keep-or-revert by the decision rule.

### C1. Rewrite `Citation_Network_Explorer` to use the real Section-level CITES edges ☐
- **Why:** the graph has **1,694 `(:Section)-[:CITES]->(:Section)` edges** (built by `build_cites_edges.py`, each with a `via` source phrase) but the tool (app.py:907–932) queries `(:Legislation)-[:CITES]->(:Legislation)` — **zero edges, dead tool**. The model calls a citation tool and concludes "no citations." Fixing this is the planned *on-demand* exposure of the citation data (ground rule 1: tools yes, retrieval injection no).
- **New shape:** input `lov` (name/abbrev, resolved via the ALIASES map + title containment) + `paragraf` (e.g. "16 A"); output two lists: *citerer* (outgoing `(:Section)-[:CITES]->`) and *citeret af* (incoming), each row `{lov, paragraf, via}` — resolve each Section back to its Legislation via the hierarchy for the law name. Cap ~15 rows each. Keep the tool name; rewrite the description to say exactly what it does ("Find hvilke §§ en bestemt § henviser til, og hvilke §§ der henviser til den — på tværs af love").
- **Verify first in Cypher** (read-only, e.g. via `Read_Only_Cypher`-style query in a script): LL § 16 A should show incoming/outgoing cross-law edges. Then eval: watch gs-024/gs-025 (multi-law chains) in the judge re-score.

### C2. Narrow the direct-§ lookup in `retrieve_text_with_context` ☐
- **Why:** the direct lookup (app.py:791–816) matches `(sec:Section {number: $sec})` across **all laws and all versions** — a "ligningslovens § 16" query can prepend PSL § 16 or a historic 2019 LL § 16 to the context. This is wrong-context *removal* (narrowing), allowed and encouraged by ground rule 1.
- **Fix:** parse a law name from the query (same regex+aliases as A3/C1); if found, filter the direct lookup to that law; always prefer `coalesce(l.is_current, true)` versions in the direct lookup (add `l` to the MATCH — note the current query doesn't bind the Legislation for filtering). Do NOT touch the vector-hit path or add any output fields.
- **Verify:** scripted check that the direct rows for "ligningslovens § 16" are all LL-current; then full protocol.

### C3. Prune the tool set ☐
- 15 tools is a big decision surface for a 26B. Remove: **`Semantic_Search`** (app.py:480–484 — duplicates `Contextual_Text_Retriever` but without the Commentary filter or hierarchy context; strictly worse), **`Citation_Counts`** (app.py:1051–1078 — queries `LINKED_TO`, which has 0 edges in the graph; permanently empty). Consider demoting/removing `Text2Cypher_Expert` (last-resort NL→Cypher; mostly burns a hop — check `observability.db`/eval logs for how often it's called and whether it ever helped, and keep it if unclear).
- Also update the CLAUDE.md tool list and the Tools view categorization sets (app.py:2291–2296) accordingly.
- **Measure:** full protocol — tool removal changes agent behavior. Expect flat-or-better judge with fewer wasted hops.

### C4. Cross-encoder reranker over vector hits ☐  (the parked "narrowing" idea #1)
- Retrieve broad (the code already fetches `k*4` candidates, app.py:821), score each `(question, matched_text)` pair with a cross-encoder — `BAAI/bge-reranker-v2-m3` via `sentence_transformers.CrossEncoder` (multilingual, runs on the 4090; CPU works but slower — **ask the user about GPU**) — and keep only the top 3–4 rows for the tool output. CITES/`is_current` may be used as *ranking boosts*, never as output text. Cache the model like the embedder (bake-in consideration also noted in the GCP todo).
- **Hypothesis:** leaner, more relevant context → judge up AND determinism up. This is the highest-upside experiment in the backlog. Measure with the full protocol; also compare answer lengths/latency.

### C5. Judge-measured prompt pruning ☐  (only after D1)
- The system prompt (app.py:1373–1450) contains substring-scorer fitting: exact-phrase mandates like "brug præcist 'er ikke ændret' (IKKE 'er ikke blevet ændret')", "brug ordene 'ingen beløbsgrænse' og 'afgiftsfri'", the § 9 Z answer template "(Denne formulering indeholder de nødvendige signaler)". These optimize the OLD scorer, not answers.
- **One experiment:** remove the exact-phrase/wording mandates (keep ALL structural facts, tool guidance, behavior sections, CITATIONSKÆDER); score with `--scorer judge` + repeat protocol. **Warning:** a previous full "lean prompt" experiment collapsed to 4/30 — this is NOT that; cut only wording mandates, keep everything substantive. Revert on any judge drop. The boafgiftsloven directive (app.py:1409) can only be cut after D1 loads BAL.

---

## Phase D — Graph content & eval growth (user coordination needed)

### D1. Load boafgiftsloven (BAL) into the graph ☐
- **Why (correctness landmine):** golden-set gs-019 expects BAL § 22 (ægtefællegaver), but **BAL is not in the graph** — the answer currently comes from hardcoded prompt phrases (app.py:1409), violating the never-hardcode rule.
- **How:** find the current LBK of boafgiftsloven on retsinformation.dk, add its ELI year/number to `danish_tax_legislation.txt`, run the pipeline (`danish_crawler.ipynb` → `loader.ipynb` → vectorize (`/tmp/vectorize_danish.py` may need recreating; e5-large, `passage: ` prefix, **GPU — ask user**) → `indices.ipynb`). **The 1922-statsskattelov failure mode:** retsinformation only had a metadata stub for very old laws — verify the XML actually contains structured law text before loading; if it's a stub, skip and tell the user (gs-019 would then be reworked like gs-024 was).
- **After loading:** re-run `build_supersedes_edges.py` and `build_cites_edges.py --commit` (both idempotent). Then remove the BAL prompt directive (fold into C5 or as its own measured mini-change) and verify gs-019 passes from retrieval.

### D2. Data-driven law expansion ☐
- `build_cites_edges.py` (dry run, no `--commit`) prints `skip_outof_graph` — a frequency-ranked list of laws referenced by the loaded corpus but missing from it (~35 laws: pensionsbeskatningsloven, virksomhedsskatteloven, …). Present the top 5 to the user with counts and let them pick what to load next (each load = crawl+load+vectorize cycle as in D1). This is the evidence-based way to grow graph content.

### D3. Golden-set expansion to ~50 items ☑ DONE 2026-07-05  (v4.0: +20 items gs-031..gs-050, graph-verified, expert-reviewed; re-baseline run started — v3.x scores NOT comparable)
- Current 30 items are thin exactly where behavior matters: 1 clarify, 1 refuse, 2 admit_unknown, 1 safety-pillar. Single-item classes make behavior regressions invisible/noisy.
- Author ~20 new items: ≥4 each of clarify/refuse/admit_unknown, more cross_reference chains, coverage of newly loaded laws (D1/D2). Judge-first authoring: `question`, `expected_answer`, `expected_behavior`, `expected_legislation` — `must_contain` optional and any-of-style only where genuinely unambiguous (numbers).
- **Gate: the user (Danish tax expert) must review every new item's legal content before it becomes ground truth.** Draft, present, incorporate, then commit.
- After expansion, re-establish baselines (§1) with a 5× run — old scores aren't comparable across set versions.

### D4. Re-chunk the 43 dense amendment nodes ☐
- 43 Text nodes >3000 chars (mostly amendment-law walls of text) pollute retrieval: one hit eats the context budget. Split into per-provision chunks (the existing year-restructure heuristic in `retrieve_text_with_context`, app.py:866–894, partially compensates — keep it), re-embed the new chunks (**GPU — ask user**), keep hierarchy links. Then full measurement protocol.

### D5. Structured reguleringstabel nodes ☐
- Longstanding TODO: year-specific rate tables live as flat text rows parsed by regex (`_parse_regulering_row`). Model them as `(:Sats {lov, paragraf, beskrivelse, grundbeloeb})-[:VAERDI {aar}]->(:Beloeb {kr})` or simpler property maps, and have `Regulering_Table_Lookup` query structure instead of regex. Design doc first (whitepapers/), then migration script in the `build_supersedes_edges.py` mold (idempotent, dry-run default).

---

## Phase E — Maskinrummet: the new frontend (replaces Streamlit; design done, implementation gated on user go)

**Design doc: `whitepapers/frontend_maskinrummet_design.md`** (Fable, 2026-07-05) + visual mockup `whitepapers/mockups/maskinrummet_mockup.html` — **the mockup is the V1 scope contract.** Core idea: the architecture diagram and the live agent trace are ONE surface ("Kredsløbet"), generated from runtime truth, with three synchronized lenses (circuit / graph lens / thought stream) + a scrubbable timeline; everything a pure function of `(event_log, t)`. Stack: FastAPI `server.py` (app.py stays single source, streamlit-stub import pattern) + SSE + React/Vite/TS. Implementation = Opus, per the doc's phasing:

**Feedback round 1 incorporated (user approved direction 2026-07-05):** node inspector, tool/LLM I/O drill-down, deterministic-first context search + AI analyze endpoint, per-call token/cost badges, Eval lens with dimension matrix — see the doc's "Feedback round 1" section; all are V1 contract.

**Implementation kickoff (green-lit by user 2026-07-05):**
- **Prerequisite: DONE 2026-07-05** — Node v24.18.0 LTS installed via nvm; symlinked into `~/.local/bin` so it works in BOTH interactive shells and Claude Code's non-interactive Bash (plain `node`/`npm` just work). Backend runs on the existing `.venv`.
- **Branch:** create `maskinrummet-e1` off the current branch; E-work must not touch agent logic in app.py (additive imports only, like eval_run.py does).
- **Layout:** `server.py` next to app.py; frontend in `frontend/` (Vite root), build output served by FastAPI.
- **Session discipline:** one session per task (E0, then E1, …). Kickoff prompt for the implementing session: *"Read IMPROVEMENT_BACKLOG.md Phase E, whitepapers/frontend_maskinrummet_design.md, and open whitepapers/mockups/maskinrummet_mockup.html — then implement E<N>. The mockup is the V1 scope contract; the doc's acceptance criteria gate completion."*
- **Verify E1 by the doc's acceptance test:** ask the gs-025 question in the new UI, watch it live, scrub the replay; both APP_MODE values behave per spec.

### E0. eval_run.py run-metadata stamp ☑ DONE 2026-07-05 (Sonnet) — every output record now carries `git_sha`, `provider` (the actual resolved string, e.g. `gemini:gemini-2.5-flash`), `set_version` (from golden set metadata), and a per-record UTC `ts`. Implemented via a new pure `resolve_llm_provider()` in app.py (extracted from `build_runtime`, zero behavior change — `build_runtime`'s signature/return untouched) + a `_git_sha()` helper in eval_run.py (falls back to "unknown", never aborts a run). Verified: smoke run + `--repeat 2` + `--failing-only` all parse the new fields correctly; old records without these keys remain readable (additive only).
### E1. The spine ☑ DONE 2026-07-05 (Opus) — `server.py` (FastAPI, streamlit-stub import of app, `/api/architecture` + `/api/ask` SSE, serves the built SPA, `APP_MODE`) + `frontend/` (Vite+React+TS): chat, Kredsløbet (circuit generated from the real tool list, lit live as f(log,t)), Tidslinjen (scrub = replay). Graflinse/Tankestrøm/Eval tabs shelled for E2/E3. Impl note: mockup token-CSS ported verbatim (no Tailwind), CSS transitions (no Framer Motion) for pixel-faithfulness + minimal deps. Verified: real gs-025 SSE run end-to-end; built SPA served; **20 replay/scrub assertions on a real captured log** (`frontend/tests/replay.test.ts`, `npm run test:replay`); APP_MODE user+dev both confirmed. Playwright smoke (`tests/e2e.spec.ts`, `npm run test:e2e`) **PASSES** in a real browser (mount → live SSE run → answer renders → scrub → circuit nodes; zero page errors) once browser sys-libs are installed. Deps: requirements-server.txt (fastapi/uvicorn).
### E2. The lenses ☐ — Graflinsen (+subgraph & node-detail endpoints, node inspector), Tankestrømmen (+I/O drill-down, context reconstruction, context search/analyze, token/cost badges), kilder-chips m/ verification + graph-highlight, feedback, historical replay
### E3. Dev depth ☐ — Eval lens (dimension matrix, runs list keyed by model+set+app-commit, item→replay), tool-health table, retire Streamlit

**Phase B is absorbed:** do NOT build B2–B7 in Streamlit; B1 (APP_MODE) survives as a backend concept (see the doc's "Relationship to Phase B" for the interim-deploy decision rule).

## Gated / parked (do NOT start without explicit user approval)

| Item | Where documented | Gate |
|---|---|---|
| Flash-3.5 control/treatment runs (lean vs restored-enriched retrieval on a stronger model) | memory: `todo_flash35_and_retrieval.md`; restore enrichments via `git revert 0551739` | User schedules; costs API money |
| Concept layer (indkomstkategori spine) | `whitepapers/concept_layer_design.md` | User must expert-review the seed edges (they ASSERT doctrine); build as **on-demand tool only** |
| GCP deployment (Cloud Run + Gemini API + Aura) | memory: `todo_gcp_migration.md` | **Paused by user 2026-07-04**; B1's APP_MODE gate is its prerequisite and IS in scope |
| Qwen3-30B-A3B model swap | — | Postponed by user; revisit after C4 |
| Time-dimension views (Point in Time was rebuilt; Temporal Diff/Schedules still removed) | memory: `todo_time_dimension.md` | Low priority |

## Known traps index (things that already burned a session)

- Substring scorer can't measure phrasing-shifting changes → judge re-score (§2).
- `pkill -f eval_run` self-kills → kill by PID.
- Ollama from WSL2 = `http://172.21.64.1:11434`, not localhost. Model stalls silently if the user is gaming on the GPU.
- Hosted `gemma-4-26b-a4b-it` on the Gemini API is free-tier TPM-capped regardless of billing → use `gemini-2.5-flash` for hosted runs.
- Aura hangs `build_runtime()` at startup sometimes → `build_runtime_with_retry` handles it; be patient before assuming a code hang.
- Golden-set `must_contain` entries may be strings OR any-of lists — handle both (`_term_present` does).
- `status` property on Legislation is unreliable for currency — use `is_current` (set by `build_supersedes_edges.py`).
