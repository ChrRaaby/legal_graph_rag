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
- ☑ **gs-039 reworked 2026-08-08** (user-approved). `admit_unknown → answer`;
  retagged `out_of_scope_law → coverage_honesty`. Anchors **graph-verified**
  (`scratchpad/gs039_verify_pbl16.py`): grundbeløb **50.000 kr. (2010-niveau)**,
  the §§ 15/15 A/15 B exception and the PSL § 20 regulation are verbatim in § 16.
  ⚠ **The agent's 65.500 kr. (2025) / 68.700 kr. (2026) figures were NOT asserted**
  — they appear nowhere in § 16 or any reguleringstabel row in the graph; the
  facit instead requires the year-amount to be fetched from the graph. Verified:
  gs-039 + gs-066 both pass. The absence-test role passed to **gs-066**
  (virksomhedsskatteloven, still missing from the graph).
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

## Phase G — live app: real eval runs, eval UI rework, architecture tab (NEW, user request 2026-08-12)

**Goal:** the deployed service should show at least one *full* eval run (not just
smoke), the Eval lens should be reworked, Maskinrummet should gain an architecture
tab covering the whole solution including its GCP substrate, and the tool set
should face a fresh usage census.

**State verified against the live service 2026-08-12** (project
`gen-lang-client-0167283966`, service `legal-graph-rag`, europe-west1, revision
`legal-graph-rag-00012-mqf` serving 100 %):
- The GCS read path **already exists** — `/api/eval/runs` and `/api/eval/runs/{name}`
  fall back to bucket `gen-lang-client-0167283966-eval-history`, prefix
  `eval_history/` (server.py:911–971), and fixtures likewise (server.py:1028).
  So the 2026-08-09 gate note ("`/api/eval/runs` returns `[]`") is **stale** —
  persistence landed in `54fb7af`/`58fd7e7`.
- What the bucket actually holds: **4 objects** — 2 fixture baselines and 2 smoke
  runs (`eval_results_smoke_20260810/11.jsonl`). Smoke is capped at 5 items, so
  **there is still no full-50 run live.** 57 full run files sit in local
  `eval_history/`.
- `migrate_to_gcp.py` migrates **only** `mr_runs` + `mr_feedback` to Firestore.
  It does **not** upload eval jsonl. Nothing in the repo does.

### G1. Get a full eval run visible in the live app ☐

- ☐ **Upload path for eval artefacts.** One-shot `gcloud storage cp` is enough to
  unblock, but the repeatable fix is extending `migrate_to_gcp.py` (or the
  eval runner's writer at server.py:1205, which already uploads smoke results) to
  push a chosen full run. Decide: upload *all* 57, or a curated few? The lens
  scans every blob under the prefix, so bulk upload changes what the Historik tab
  shows by default.
- ☑ **DECIDED 2026-08-12 — the app defaults to `dev` mode, with a UI toggle between
  user and dev.** So the Eval tab is reachable live by default. ⚠ This is a
  **posture change on a public URL**: dev mode exposes the eval runner (which spends
  API money) and dev routes. Basic Auth from `58fd7e7` is the thing standing in
  front of it — confirm the toggle cannot be reached unauthenticated, and that
  `APP_MODE` remains the *default* rather than the only control. A dev/user toggle
  already landed in `23feaa9`; check how much of this is done before building.
- ☑ **DECIDED 2026-08-12 — both runs execute locally and are uploaded** to the live
  eval history. Neither needs to run in Cloud Run. This removes the GPU/Ollama
  blocker for gemma *and* means the flash run needs no cloud substrate either.
  Consequence to keep straight: these runs measure the **local** substrate
  (3.5-flash / 12 tools), which is still not what the deployed agent runs — the
  drift noted in the gate table stands, and the run tiles must not imply otherwise.
- ☐ **Run 1 — `gemini-3.6-flash`, full 50, run locally.** ⚠ Lifts the 🔒 gate at the
  bottom of this file ("Newer models now listed … switching is a **substrate
  change**"): approved *as a run* 2026-08-12, but ground rule 6 still applies — a
  **matched pair** is required if the result is ever used to justify switching the
  default substrate. Costs API money. Pin the full model id, never an alias.
- ☐ **Run 2 — on-prem gemma, full 50, run locally.** Note the reproducibility rule
  from F3: unload/reload the model between cells, never back-to-back.
- ☐ **Label the substrate in the UI.** Two runs on different substrates will sit
  side by side in Historik; the run tile must make model + provider unmissable or
  the comparison silently misleads. Result files already carry the stamp.

### G2. Eval UI rework ⏳ — **design doc written 2026-08-12, awaiting user go**

**Design:** `whitepapers/eval_workspace_design.md` · **mockup:**
`whitepapers/mockups/eval_workspace_mockup.html` (the mockup is the V1 contract,
same convention as Phase E). Phasing: **G2a** = content fixes inside `Eval.tsx`
(ships alone, kills the misleading defaults); **G2b** = the workspace split.

Current shape is E4's: Eval tab split into *Testsuite* (browse + smoke runner) and
*Historik* (past runs, matrix, items, fixtures, tool health).

**Observed against the running local app 2026-08-12** (`app_mode=dev`,
3.5-flash, 12 tools, 48 runs in history, golden set v4.2 · 69 items). Findings are
ranked by how much they distort the analysis, not by how easy they are to fix:

1. ☐ **Historik opens on a stale run by default.** Both selects default to
   **index 32 of 48** — `eval_results_v4_flash_5x.jsonl` vs
   `eval_results_v4_gemma_run5.jsonl`, **both from 2026-07-05, five weeks old** —
   while the newest run (2026-08-09) sits at index 0. Every screenshot in this
   file's history is therefore of an obsolete comparison. Default should be
   newest-vs-its-natural-counterpart, or explicitly "no run selected".
2. ☐ **Denominators are not comparable and the headline tile is raw.** One dropdown
   mixes `/69`, `/50`, `/30`, `/13`, `/7`, `/3`, `/2`, `/1` (set grew v4→v4.2, plus
   smoke and debug runs). "36/69" and "36/50" read as equal at a glance and are not.
   Normalise the headline to a percentage, and **separate real runs from smoke/debug
   stubs** — a `1/1` run does not belong in the same list as a 69-item run.
3. ☐ **Tags matrix is a wall of noise.** ~40+ rows, sorted **alphabetically**, almost
   all with **n=5**, and the comparison column is mostly `—` (the two runs rarely
   share tags). Bare `0%`/`20%`/`100%` at n=5 invites over-reading pure noise. Sort
   by signal (gap size, or n), collapse or grey n<10, and show the pair count.
4. ☐ **Run labels lose the model.** Recent entries read `ollama · v4.2 · …` while
   older ones read `gemma4:26b · v4 · …`. Which local model produced the v4.2 runs
   is **not recoverable from the picker** — directly harmful with G1 about to add
   two more local runs. Six near-identical `ollama · v4.2 · 1×` entries currently
   differ only by score and date.
5. ☐ **A 48-entry raw `<select>` is the only way to find a run.** No search, no
   grouping by substrate or set version, no filtering. Compare-picker repeats it.
6. ☐ **Two of six stat tiles are dead** on older files (`APP-COMMIT —`,
   `FORBRUG IKKE REGISTRERET I DENNE FIL`). Honest (E4 chose null over fake 0) but
   they occupy prime space; collapse when empty.
7. ☐ **Vertical space is the structural problem.** The lens lives in the bottom
   pane (~400 px) with its own nested scrollbar, beneath a chat pane that is
   ~500 px of mostly empty whitespace. The dimension tables show 2–3 rows at a
   time. Eval is a **data-dense, full-attention view** and does not fit the
   split-screen model the other three lenses were designed for. Options: give Eval
   a full-height mode, or let the chat pane collapse when Eval is active.
8. ☐ **Tool health is buried at the very bottom** of that nested scroll — yet with
   G4 it is currently the most decision-relevant table in the app. Promote it.

9. ☐ **The smoke-run execution card renders *below* the whole item table.** User's
   own complaint 2026-08-12: "you need to scroll to find it". Root cause is DOM
   order, not styling — `Eval.tsx:536-537` renders `<GoldenBrowser>` (which holds
   the full 69-row table) and *then* `<RunnerPanel>`. The card must move above the
   table, or beside it.

**☑ Scope set by user 2026-08-12:** findings 1–6 and 9 are in (wrong defaults &
scales · signal over noise · run navigation · the run card), **plus a dedicated
full-height Eval mode** for finding 7.

**☑ The user's framing — this is the load-bearing insight, not a styling
preference:** *"kredsløb, graflinse and tankestrøm are tools to inspect and
understand a single samtale. The stuff in eval is different — it is a list of test
cases that can be explored and executed, and then you have the aggregated eval
history."* That is exactly right and the README already says the first half: the
three lenses are **pure functions of `(event_log, t)`** for one run. Eval has no
event log and no `t`. **Eval is not a lens; it is a second workspace that was
parked in the lens rail.** Design must follow that split — see G2-design below.

### G3. Architecture tab in Maskinrummet ☐

- ☐ New tab beside Kredsløb / Graflinse / Tankestrøm showing the **whole solution**:
  LLM, Python backend, React frontend, Neo4j, and the GCP components
  (Cloud Run, Firestore `mr_runs`/`mr_feedback`, GCS `…-eval-history`,
  Secret Manager, Artifact Registry).
- ☑ **DECIDED 2026-08-12 — generate it dynamically, not by hand.** This settles the
  tension with the project's own rule that Kredsløbet *"genereres fra runtime … og
  kan aldrig"* go stale (App.tsx:205); the redesign doc opens by condemning exactly
  the hand-drawn failure ("factually stale — hardcoded '13 tools', 'Gemini 2.5
  Flash'"). Kredsløbet is the working precedent — same pattern, wider scope.
- ☐ **Sketch the generation approach before building.** The honest split: the app's
  own layer is *observable* (`/api/architecture` already serves provider, model,
  tool list and graph stats; Neo4j reports its own counts), while the GCP layer is
  not — the container cannot enumerate its own Firestore/GCS/Secret Manager wiring
  without admin credentials it should not hold. **Proposed:** declare the GCP
  topology as data (a small server-side manifest) but derive every drift-prone
  label from runtime — and mark each node in the UI as *observed* vs *declared* so
  a stale declaration is visible rather than silently trusted. Cheap runtime signal
  available for free: `K_SERVICE`/`K_REVISION` env vars exist in Cloud Run, so the
  live revision can be observed rather than declared.
- Not agent-visible (UI only) → no matched pair (ground rule 6); verify like
  E1–E4: Playwright + real-data eyeball.

### G4. Re-run the tool usage census ☐ (user 2026-08-12: "most of the tools are not being used")

**There is a proven method for exactly this** — don't invent one. C3
(backlog §194–196) ran a usage census across **2,301 saved eval item-runs** and
cut **15 → 12** tools. Re-run it on the current 12.

**First evidence, read off the running app 2026-08-12 — the hunch holds.** The
"Værktøjs-sundhed · 37 live-kørsler" table reports **115 tool calls across 8 tools**:

| tool | calls | empty | mean |
|---|---:|---:|---:|
| `Contextual_Text_Retriever` | 76 | 3 % | 0,31 s |
| `Regulering_Table_Lookup` | 15 | 0 % | 0,06 s |
| `Skattesats_Opslag` | 14 | 7 % | 0,14 s |
| `Read_Only_Cypher` | 4 | 25 % | 0,09 s |
| `Graph_Schema_Navigator` | 2 | 0 % | 0,22 s |
| `Legislation_Title_Resolver` | 2 | 0 % | 0,07 s |
| `Legislation_Finder` | 1 | 0 % | 0,24 s |
| `Citation_Network_Explorer` | 1 | 0 % | 0,22 s |

**One tool is 66 % of all calls.** Four of the 12 never appear at all —
`Supersedes_Network_Explorer`, `Superseded_By_Network_Explorer`,
`Legislation_By_URI`, `Hierarchy_Path_Resolver` — and four more sit at ≤2 calls,
which is indistinguishable from unused. `Read_Only_Cypher` returns **empty 25 %**
of the time, the worst rate in the table.
⚠ Caveats before anyone acts on this: it is **37 live chat runs, not eval runs**
(a different and small sample), and it is **not split by substrate**. Treat it as
the prompt for the census, not the census.

- ☐ **Census.** Every eval record carries `tool_sequence` (app.py:1952, 2158;
  surfaced at server.py:1006 and in the lens's tool-health table), so this is a
  read-only pass over `eval_history/` — no API spend to get the counts.
  C3's decision rule stands: **0 calls ever → remove**; called-but-worse than the
  retriever baseline → remove (`Semantic_Search` went at 23 % pass-when-called
  vs 51 %).
- ⚠ **Separate "unused" from "unusable" before deleting anything.** Two different
  causes look identical in the counts: (a) the tool earns nothing, (b) the model
  never *chooses* a tool that would help. **C1b is a live case of (b)** — the
  citation tool is correct and flash simply never invokes it, and the recorded fix
  is a deterministic post-answer step, *not* removal. A census that doesn't split
  these will delete working capability. The user's framing — "figure out if there
  is anything to gain by actually using them" — is exactly this question, so for
  each 0-call tool decide explicitly: **force it and measure, or drop it.**
- ⚠ **Census per substrate, not pooled.** C3's sharpest finding was that the
  substrates disagree: flash never touched the pruned tools, while **gemma made
  most of the 13 low-quality `Semantic_Search` calls**. A pooled count would have
  hidden that.
- **Synergy with G1:** the two full runs in G1 (3.6-flash + on-prem gemma) produce
  exactly the fresh per-substrate `tool_sequence` data this census needs. Sequence
  G1 → G4 and the census costs nothing extra.
- ⚠ **Removal is agent-visible** (tool schemas change in every LLM request) →
  matched pair required, both substrates, per ground rule 6. Note C3's gate came
  back *flat* on flash and was kept on the §2 flat-clause (fewer schemas = fewer
  tokens), not on a quality win — expect the same shape of argument here.
- Reference: the 15 defined tools live at app.py:537–1446; three
  (`Semantic_Search`, `Text2Cypher_Expert`, `Citation_Counts`) are already pruned
  behind `C3_TOOL_PRUNE`, leaving the 12 in play.

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

☑ **Feedback round 1 (user, 2026-08-08) implemented:**
1. **Eval tab split into sub-tabs** — *Testsuite* (browse the set + run a smoke)
   and *Historik* (past runs, matrix, items, fixtures, tool health). They were one
   long scroll that buried the history under the browser.
2. **Tools, tokens and cost in the test log and UI** — eval records, trajectories,
   smoke verdicts and run summaries now carry a `usage` block
   (`input_tokens / output_tokens / llm_calls / cost_dkk`), and `scores` already
   carried `tool_sequence`. Shown per item, per verdict and as run tiles.
3. **Cost beside tokens everywhere** — `/api/ask` done event, run tiles, item
   drill-down, smoke verdicts, Tankestrøm LLM cards, and the run caption.
   ⚠ Pricing is now single-sourced in `app.py` and served via
   `/api/architecture`; the frontend's own table had **drifted to 2.5-era ids
   while the agent ran on 3.5**, so every cost shown was quietly wrong. Unknown
   provider → `null`, never a fake 0. Pre-2026-08-08 result files report
   `usage: null` ("not recorded"), never 0.
   **Not done deliberately:** the legacy Streamlit UI's token metrics
   (app.py ~2430/2509/2575/2780) were left alone — that UI is retired doc-level
   (Phase E) and the backlog forbids building there.

☑ Residual closed 2026-08-08: fixture records now carry a **classifier-model /
git_sha / set_version / ts stamp** (E0 lesson — the classifier is swappable, so an
unstamped baseline can't be compared), and `GET /api/eval/fixtures` + a
"Skjold-fixtures" table list them in the lens, kept separate from agent runs so a
zero-LLM L0 rung never mixes into agent statistics. The table leads with **false
positives**, the number the fixture exists to protect.

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
- ☑ **A2 — dead retrieval-row annotator deleted** 2026-08-08. grep returns 0.
  **Phase A is now fully closed** (A1 with E4; A3/A4 superseded by Phase E).
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
- ☑ **README.md corrected** 2026-08-08 (all three counts): tool table now 12 with a
  note on the C3-pruned three; architecture diagram shows Maskinrummet + the scope
  gate instead of Streamlit; law table gained BAL and PBL; the observability section
  describes the real lenses. CLAUDE.md updated in the same pass.
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
  → User approved a **full 3.6-flash run** 2026-08-12 (**G1**). Approval covers the
  run; switching the default substrate still needs the pair.

---

## Deferred refactors

- ☑ **Streamlit UI removed** 2026-08-08 (user request). app.py is pure runtime,
  **3556 → 2166 lines**; the `sys.modules` stubs are gone from all four importers;
  streamlit/altair/pandas/neo4j_viz dropped as dependencies. Verified by importing
  app with `streamlit` poisoned in `sys.modules`. Backlog Phase E.
- ☑ **Workspace cleaned** 2026-08-08: eval artefacts live in **`eval_history/`**
  (56 run outputs + 2 baselines + 15 logs). Relative `--output` names resolve
  there; absolute paths pass through. Readers fall back to the root for older
  checkouts. See `eval_history/README.md`.
- ☐ **Graflinsen edge routing** — CITES edges spanning law-columns pass visually over
  intermediate nodes. A real graph-layout problem (arc-bundling / elkjs / waypoints),
  flagged as Fable-lane design work.
- ☐ Per-message citation history; kapitel nodes in Graflinsen; item→Maskinrummet trace
  replay (blocked: eval jsonl stores no event log).

---

## 🔒 Gated — do not start without explicit user approval

| Item | Gate |
|---|---|
| GCP Cloud Run deployment | **Delegated to Gemini 2026-08-09** — handover prompt: `whitepapers/gcp_persistence_handover.md`. ⚠ **Partly superseded 2026-08-12 (see Phase G):** persistence landed in `54fb7af`/`58fd7e7`, so the ephemeral-filesystem and empty-`/api/eval/runs` findings no longer hold — mr_runs/mr_feedback go to Firestore and eval artefacts read from GCS. Still true: `APP_MODE=user` hides the Eval tab, only smoke runs are in the bucket, and live config has drifted (2.5-flash / 11 tools vs 3.5-flash / 12 local) — **the deployed agent is not the substrate the evals measure.** |
| Concept layer (indkomstkategori spine) | Asserts tax doctrine — user must expert-review seed edges; build as on-demand tool only |
| Qwen3-30B model swap | Postponed; C4 (its blocker) is now concluded |
| Flash-3.5 control/treatment runs | User schedules; costs API money |
| Time-dimension views (Temporal Diff / Schedules) | Low priority |
