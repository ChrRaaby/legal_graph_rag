# TODO — consolidated index

**Relationship to `IMPROVEMENT_BACKLOG.md`:** the backlog is the *execution plan* — it
carries the ground rules, measured evidence, and the reasoning behind every keep/revert.
This file is the *index*: one line per open item, so nothing gets lost between phases.
Detail lives in the backlog; when an item here grows a design, it graduates into the
backlog as a proper phase entry.

**Ground rules still bind everything below** (backlog §0). The two that bite most often:
never add data to retrieval output (measured net-negative 3×), and every agent-visible
change needs a measured matched pair before it's kept.

Status: ☐ open · ⏳ in progress · ☑ done · 🔒 gated on user approval

---

## Phase F — Scope guardrails (NEW, 2026-08-02)

**Goal:** the assistant should not answer questions outside Danish tax law.

**Why this is genuinely new work, not a tweak to what exists.** The project already has
four behaviour classes that decline in some way — but none of them covers off-topic:

| existing behaviour | fires when | golden items |
|---|---|---|
| `refuse` | user asks for help with something **illegal** (evasion, hiding assets, fake invoices) | 4 |
| `clarify` | question is **too vague** to answer correctly | 4 |
| `admit_unknown` | a specific **§ doesn't exist** in the graph | 5 |
| `correct_premise` | question rests on a **false assumption** | 7 |

All 4 `refuse` items are illegality (gs-026/034/035/036). **Zero items cover a
well-formed, legal, non-tax question** — "skriv mig et Python-script", "hvad er
hovedstaden i Frankrig". Today nothing stops the agent from attempting those, and the
50-item golden set cannot detect it either way. There is no scope guard in `app.py`.

### F0. Work out detailed requirements ☑ DONE 2026-08-02 — **design APPROVED, Phase F graduated to the backlog**
`whitepapers/guardrails_design.md` v2 is the implementation spec: user-chosen
architecture (one LLM classifier call flagging **pii / illegal / non_tax**; no flags →
byte-identical pass-through), all §6 decisions settled (flash-lite pinned via env,
4-message history window, fail-open with warning, PII log redaction, templates
approved). v1's layered approach rejected by user as overcomplicated; v1 evidence
retained in spec §4 (embedding gates falsified by probe, scope-vs-coverage,
false-positive asymmetry).

### F1. Implement the gate ☑ DONE 2026-08-02 (Opus) — all 6 verify steps green
Classifier gate live in `app.py` behind `F_SCOPE_GUARD`; fixture baseline
**50/50 (46/46 false-positive non-regression, 4/4 refuse items migrated)**;
42 replay assertions, Playwright green. Details: backlog Phase F + spec §5b.
### F2. Golden-set guardrail items ☑ DONE 2026-08-02 — **verified end-to-end on local gemma4:26b**
gs-051–gs-069 live (set v4.2, 69 items). Full run on the 4090 with guard ON:
**gate fired 12/12** (byte-exact templates), **zero false positives 7/7**,
gated latency 4.5 s vs 20.8 s agent (**4.6× faster**), new items 17/19.
Found + fixed a real F1 gap on the way: the classifier prompt never encoded §2's
*mixed prompts* rule → **50/50 unstable at temp 0**, now 8/8 stable.
The 2 remaining failures (gs-062/gs-065) are **substrate-dependent** — they pass
on flash, and gemma answers with the clarify template; ground truth deliberately
NOT loosened (that would be scorer-fitting). Spec §5c–5d.
- ☑ **Local classifier backend** — `SCOPE_CLASSIFIER_MODEL=ollama:<model>`;
  validated 69/69, same bar as flash-lite. The whole guardrail now runs at zero
  API cost. Gemini remains the default.
- ☐ **gs-039 rework** still open — separate item, asserts PBL § 16 values,
  needs your legal review.
- ☑ **Fable-review flags RESOLVED by user 2026-08-08**: gs-064 ratified as
  answer-with-stated-assumptions (kommune irrelevant for topskat); gs-067
  approved as-is incl. the legal content in expected_answer. Both applied to
  the golden set before the judge pass.
### F3. Measured matched pair ☑ DONE 2026-08-08 — **KEEP: judge +9 (treated +6 real, untreated +3 noise), 0 errors**
Judge pass ran after credits top-up (gemini-3.1-pro-preview, diff-first,
footprint 44/69): **ON 33 vs OFF 24.** The treated +6 is genuine — the judge
passed 6/12 ungated self-limiting declines on substance and failed exactly the
concrete misbehaviours (Python script, empty answer, 3× PII-solicitation).
Deterministic half had already shown zero regression on never-gated items
(28/53 both cells). **Phase F F1–F3 complete.** Spec §5e–5f.
- Judge-infra bug fixed en route: gemini list-shaped content nulled all 88
  verdicts on the first pass ("+0, 88 errors" reads like clean-flat). Now in
  the traps index: **never read a judge delta without checking errors == 0.**
- ☑ User rulings 2026-08-08 applied: gs-064 → answer-with-stated-assumptions
  (now registers a real capability gap on both substrates — don't chase);
  gs-067 approved as judge facit.
- ☑ Reproducibility test resolved 2026-08-08: **classifier exonerated** (39 %
  divergence with zero classifier involvement), but not uniform noise — a
  fresh-model run reproduced a 10-hour-old cell at **95 %**; the back-to-back
  second run dropped to 39 %. New traps-index rule: **unload/reload the model
  between matched-pair cells; never run cells back-to-back.** Spec §5f.

---

## E4 — Eval lens v2 ☑ DONE 2026-08-08 (Opus) — **A1 landed with it**

Shipped: `GET /api/eval/golden` (full set + facets + search + tag filtering),
`POST /api/eval/run` (smoke tier, capped at 5, SSE, scores with the A1 scorer,
**persists to mr_runs so eval runs are replayable — E3's deferred gap closed**),
pillar + tags matrix dimensions, and 🛡 gate-verdict badges via template equality.
**A1: app.py's stale scorer fork deleted**; scoring single-sourced in app.py and
imported by eval_run. Proven a pure refactor — pre- vs post-refactor scorer:
**2915/2915 identical** (`scratchpad/a1_refactor_proof.py`).
Verified: 30 offline checks, API smoke, end-to-end runner smoke, 50 replay
assertions, Playwright green, CLI unchanged, real-data eyeball. Backlog Phase E.

Residual (small, deliberate): `eval_scope_fixtures.py` output is still not listed
in the lens, and its records still lack a classifier-model stamp (E0 lesson) —
scoped as optional/low-priority in the original E4 spec below and left undone.

<details><summary>Original E4 scope (2026-08-02)</summary>

## E4 — Eval lens v2: golden-set browser + runner in Maskinrummet (NEW, user request 2026-08-02)

**Goal:** port the old Streamlit Evaluation tab's functionality — browse golden-set
cases, run them, and see results along the set's dimensions — into the Maskinrummet
Eval lens, brushed up. The Streamlit panel stays untouched (legacy; its scorer copy is
broken anyway, see A1).

**What E3 already built vs what's missing (verified against server.py):**

| capability | old Streamlit tab | E3 Eval lens today | E4 target |
|---|---|---|---|
| browse golden-set item definitions | ✅ | ❌ (only id/category/difficulty echoed from *result* files) | ✅ full-item browser |
| run cases from the UI | ✅ (broken scorer) | ❌ read-only | ✅ smoke-scoped runner |
| results by dimension | partial | ✅ category/difficulty/behavior matrix | + **pillar** and **tags** (the focus areas), both present in the set since v4 |
| per-item run history / drill-down | ❌ | ✅ | keep |
| item → trace replay | ❌ | ❌ (E3 deferral: eval jsonl stores no event log) | ✅ for UI-triggered runs (see below) |

Scope decisions to respect:
- ☐ **`GET /api/eval/golden`** — serve the full item definitions (question, expected_answer,
  behavior, must_contain incl. any-of lists, pillar, tags, notes) with filter/search by
  any dimension. Read-only, zero risk.
- ☐ **UI runner = smoke tier only.** Single item or small subset, live progress via the
  existing SSE machinery, scored on completion. **NOT a replacement for the §2
  measurement protocol** — full-50 matched pairs stay CLI (`ab_driver.py`); the UI must
  not make casual full-set runs one click away (confirm + item-count cap). Each UI run
  costs real API money.
- ☐ **Store the event log for UI-triggered runs** (mr_runs already persists events for
  chat runs — reuse it). This closes E3's deferred item→trace-replay gap *for UI runs*:
  an eval item's run becomes scrubbable in Kredsløbet/Tankestrømmen like any chat turn.
  Historical CLI jsonl stays replay-less (no data).
- ☐ **Add pillar + tags to `_DIMS`** (server.py:692) and the matrix UI — trivial once the
  golden endpoint exists.
- **Prerequisite/synergy — A1 (single-source the scorer):** the UI runner needs
  `score_item` server-side; import direction is server→app, so A1's move
  (eval_run's current scorer implementations → app.py) should land first or as part of
  E4, killing the stale broken copy in the same stroke.
- Not agent-visible (UI + scoring infra) → no matched pair needed (ground rule 6);
  verify like E1–E3: replay assertions + Playwright + eyeball with real data.
- Lane: Opus (mechanical, spec'd), same session pattern as E1–E3.

</details>

---

## Active thread

### D2 — data-driven law expansion ⏳
- ☑ **gs-039 re-verify** — DONE 2026-08-02. Agent smoke: the predicted flip, exactly —
  `detected=answer` vs `expected=admit_unknown`, 3 tool calls, PBL § 16 quoted from
  retrieval (50.000 kr. grundbeløb 2010-niveau; reguleret 65.500 kr. 2025 / 68.700 kr.
  2026 — **values pending user legal review in the rework**). Note: the backlog's
  "expect §✓" on the L0 fixture couldn't happen — gs-039 has `expected_legislation: []`
  (authored as an absence test), so the fixture is vacuous (0/0). The smoke is the
  meaningful verification.
- ☐ **gs-039 rework** — now unblocked. Direction proposed in
  `whitepapers/guardrails_design.md` §1: retag `out_of_scope_law`→coverage-honesty,
  flip to `expected_behavior=answer` with expert-reviewed content (the smoke answer is
  the draft basis), and add a replacement absence-item on a still-missing tax law
  (e.g. virksomhedsskatteloven) staying `admit_unknown`. User review gate.
- 🔒 **Next law to load** — `lov 369/2025` (bo-/gaveafgift reform) is pre-picked and
  awaiting your call. Evidence-ranked alternatives from `build_cites_edges.py`:
  skattekontrollov (111 refs) · dødsboskattelov (39) · pensionsafkastbeskatningslov (32)
  · ejendomsskattelov (28) · virksomhedsskattelov (26) · statsskattelov (26).

---

## Ready to do (small, well-specified)

- ☐ **Cut the BAL prompt line** (C5/D1 follow-up) — the last hardcoded-legal-fact
  violation of ground rule 2, redundant now that BAL retrieval works. Trivial via the
  `c5_bal` template slot; gate with the ladder.
- ☐ **A2 — delete dead `_attach_kilde`** (app.py, unused since the `0551739` revert).
  The only Phase-A task left — A1 landed with E4 on 2026-08-08.
- ☐ **B5 — empty-state example question chips.** The backlog calls this the single
  Phase-B gap Maskinrummet didn't absorb.
- ☐ **Persist tool args in `log_trajectory`.** Infra gap found during C2: query-level
  attribution ("did the model's retriever query contain a §?") was impossible post-hoc.
  Not agent-visible, so no eval run needed.

---

## Bigger, unstarted

- ☐ **D4 — re-chunk the 43 dense amendment Text nodes** (>3000 chars) that eat the
  context budget. **GPU — ask user first.**
- ☐ **D5 — structured reguleringstabel nodes**: model rates as
  `(:Sats)-[:VAERDI {aar}]->(:Beloeb)` instead of regex-parsing flat text. Design doc
  first, then an idempotent dry-run-default migration script.
- ☐ **Close the retrieval recall gap (C4's real finding).** The reranker was reverted
  (judge −3.6), but its diagnosis stands: **recall is the headroom, not ranking** —
  targets like ML § 33 and ASKL § 14 never enter the vector pool for their natural
  query. ⚠ C7 proved recall gains must arrive by **replacement** (query expansion
  moms→merværdiafgift, D4 re-chunking) or **on-demand tools** — never by adding rows.
- ☐ **Reorder-only reranking** (`RERANK_TOP≥15`, no truncation, direct-§ rows exempt) —
  untested, plausible but modest. Code exists on branch `c4-reranker`.
- ☐ **C1b — get the model to actually call the citation tool.** The tool is correct but
  flash never invokes it. Prompt nudge measured negative and reverted; remaining robust
  lever is a **deterministic post-answer citation-completion step** that doesn't depend
  on model tool-choice.
- ☐ **Root-cause the fond-query 0-rows filter cascade** (found during C7 probing).

---

## Found this session (2026-08-02)

- ☑ **`.env` model config was internally inconsistent** — `GEMINI_MODEL=gemini-2.5-flash`
  contradicted `LLM_PROVIDER=gemini:gemini-3.5-flash` *and* app.py:162's own default, so
  any fallback path silently ran an older substrate; `GEMINI_MODELS` was missing both the
  live agent and judge models, making the actual agent model unselectable in the dev UI
  and invalid in `resolve_llm_provider`'s `_known` set. Fixed; additive, so historical
  2.5-flash cells stay reproducible.
- ☑ **Traps-index model entry corrected.** It recorded `gemini-2.5-flash`/`-2.5-pro` as
  permanently pulled. Re-probed 2026-08-02: **both alive**, along with all 7 ids the
  project references. A 404 is an outage signal, not a removal — re-probe before
  rewriting configs. The entry's real lessons (pin ids, never aliases, probe before long
  runs) stand.
- ☐ **Two non-tax bekendtgørelser sit in the graph as *current* legislation** —
  `Bekendtgørelse om markfrø` and a `jernbaneloven` delegation order (2 of 16 current
  Legislation nodes). The backlog knows the *symptom* (C2 records markfrø § 33 crowding
  momsloven § 33 out of the direct-§ lookup's `LIMIT 5`) but not the cause. C2's
  law-narrowing defuses §-bearing queries; they can still occupy vector-pool slots
  elsewhere, which touches the recall gap above. Likely a stray seed entry in
  `danish_tax_legislation.txt` or a crawl artifact. **Investigate, then decide whether to
  remove** — removal is a graph change and therefore agent-visible.
- ☐ **README.md is stale on three counts**: says "13 StructuredTools" and lists
  `Semantic_Search` / `Citation_Counts` / `Text2Cypher_Expert` (all pruned by C3 →
  12 tools); presents Streamlit as the product (Maskinrummet is primary per CLAUDE.md);
  law table predates BAL and PBL.
- ☐ **`environment.yml` has drifted** from the real environment: pins python 3.11 (the
  working `.venv` is 3.13), and omits `fastapi`/`uvicorn` — anyone rebuilding from it
  gets an env that can't run `server.py`.
- ☐ **Stale third checkout** at `~/projects/antigravity/legal_graph_rag` (2 commits
  behind, own `.env` and `.venv`). Delete? — awaiting your call.
- ☐ **Untracked files** in the working copy: `overnight_eval.sh`,
  `eval_determ_flash.jsonl`, `c4off_launcher.log`, two judge logs. Commit the script,
  gitignore or archive the rest.
- 🔒 **Newer models now listed**: `gemini-3.6-flash`, `gemini-3-pro-preview`. Switching
  is a **substrate change** — matched pair required, not a config edit.

---

## Deferred refactors

- ☐ **Remove the Streamlit UI from `app.py`** — slim it to the runtime while keeping the
  stub-import working for `server.py`/`eval_run.py`. Careful, Opus-lane: module-level
  `st.*` is load-bearing.
- ☐ **Graflinsen edge routing** — CITES edges spanning law-columns pass visually over
  intermediate nodes. A real graph-layout problem (arc-bundling / elkjs / waypoints),
  flagged as Fable-lane design work.
- ☐ Per-message citation history; kapitel nodes in Graflinsen; item→Maskinrummet trace
  replay (blocked: eval jsonl stores no event log).

---

## 🔒 Gated — do not start without explicit user approval

| Item | Gate |
|---|---|
| GCP Cloud Run deployment | **Paused by user 2026-07-04**; APP_MODE prerequisite is in scope |
| Concept layer (indkomstkategori spine) | Asserts tax doctrine — user must expert-review seed edges; build as on-demand tool only |
| Qwen3-30B model swap | Postponed; C4 (its blocker) is now concluded |
| Flash-3.5 control/treatment runs | User schedules; costs API money |
| Time-dimension views (Temporal Diff / Schedules) | Low priority |
