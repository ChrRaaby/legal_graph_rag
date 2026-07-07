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
- **⚠ 2026-07-08: stored baseline numbers are SUBSTRATE-BOUND and go stale** (Ollama decode drift ≈ −4.8 det in one week on identical configs; judge drift 33→20 on identical answers). Do NOT compare a new run against this table — run **same-night ON-vs-OFF matched cells** (env escape hatches) and judge both cells in one pass. Details in the C4 entry.

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

> **⚠ LARGELY SUPERSEDED BY PHASE E (2026-07-06).** These tasks targeted the old
> Streamlit UI, now retired doc-level. A1 (shared scorer) → the Streamlit eval
> panel is dead; the Eval lens reads eval_results directly. A3 (law-aware
> citations) → done natively in server.py `resolve_citations`. A4 (architecture
> labels) → Kredsløbet is generated from runtime truth. **Still valid: A2**
> (delete dead `_attach_kilde` — trivial) and A1's underlying idea only if the
> stale scorer copy in app.py confuses future readers (it's unused by the new UI).
> Do NOT implement these against the legacy Streamlit views.

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

> **✅ FULLY SUPERSEDED BY PHASE E (2026-07-06).** Every B-task exists natively in
> Maskinrummet: B1 = `APP_MODE` in server.py; B2 = the tab rail; B3 = kilder chips
> (law-aware verify + ELI links + graph-highlight); B4 = live Kredsløb/caption/
> thinking indicator; B5 = (only gap — empty-state example chips, small frontend
> nicety if wanted); B6 = 👍/👎 → mr_feedback; B7 = disclaimer. Do not build any
> of this in Streamlit.

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

### C1. Rewrite `Citation_Network_Explorer` to use the real Section-level CITES edges ☑ DONE 2026-07-06 (Opus impl, Fable verdict: KEEP)
- **Outcome:** tool rewritten to `(lov, paragraf)` → `{citerer[], citeret_af[]}` over the 1694 Section-level CITES edges (alias+genitive law resolution incl. merværdiafgiftslov→momslov; current-version filter; 15-row caps; informative not-found response). Cypher-verified: LL § 16 A = 13 out/15 in cross-law with `via` phrases; ABL § 12 ← PSL § 4 a (the gs-025 bridge).
- **Measurement:** smoke (gs-024/025) + full 50-item flash run (`eval_results_c1_flash.jsonl`, stamped cfb8bec): **the model called the tool 0 times in 50 items** → change is inert-by-construction on flash (37/50 vs 36/50 = noise). The 5× protocol was deliberately skipped: with zero invocations the system under eval is identical to baseline, so further runs measure noise at API cost. **KEEP: a correct on-demand tool strictly dominates a dead one** (zero cost uncalled; feeds Graflinsen; ready for any model that reaches for it). Gemma cell unmeasured (needs GPU) — see C1b.

### C1b. Get the model to actually CALL the citation tool — lever (i) TRIED & REVERTED 2026-07-06 (Fable); (ii)/(iii) remain ☐
- The recurring lesson (rate tools, now this): fixing a tool ≠ the model using it. Flash never invoked Citation_Network_Explorer even on the chain items it was built for (gs-024/025/043/044/046/050 — the weakest eval dimension, cross_reference 25–40%).
- **(i) prompt tool-guidance line: MEASURED NEGATIVE, reverted.** One calm line in CITATIONSKÆDER ("Brug Citation_Network_Explorer(lov, paragraf) til dette …") moved invocations 0→3/50 but on the WRONG items (gs-007/009/021 tab-på-aktier; chain items still 0 calls), det 34/50 vs 36–37 baseline singles, regressions 6:3 vs the identical no-line run (`eval_results_c1b_flash.jsonl`, stamped 3873c30-dirty). Judge re-score skipped: mistargeted adoption + det dip can't become a keep. Consistent with the project's prompt-churn history — do NOT retry wording variants of this line on flash.
- **Remaining levers:** (ii) test on gemma-26B (different tool-selection; GPU + user coordination); (iii) deterministic post-answer citation-completion step (cf. the parked post-generation enrichment idea in memory todo_flash35_and_retrieval) — likely the robust path since it doesn't depend on model tool-choice at all.
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

### C4. Cross-encoder reranker over vector hits ☑ MEASURED 2026-07-07/08 (Opus impl+runs, Fable interpret) — **verdict: REVERT the top-5 config (judge −3.6, det −1.0); code kept on branch `c4-reranker` (e686732), UNMERGED**
- **Implementation (on the branch, guarded):** `BAAI/bge-reranker-v2-m3` via `CrossEncoder`, loaded cached in `build_runtime()`; rerank inserted in `retrieve_text_with_context` after row assembly, before year-restructure; reorder+truncate to `RERANK_TOP` (default was 5). Escape hatches `RERANK_TOP=0` / `RERANKER_DEVICE=off`; any load/predict failure falls through to unranked. `RERANKER_DEVICE=cpu` default (4090 shared with Ollama's gemma).
- **Offline probe of the 5 C6 rate cases — half-confirms C6's hypothesis:** the cross-encoder fixes **ranking** where the target is in the candidate pool (selskabsskat SEL §17 #6→#1; aktieindkomst PSL §8a held #1) but **cannot fix recall**: moms/ML §33, aktiesparekonto/ASKL §14, mellemskat/LOV 482 never enter the vector pool for their natural query (duplicated LL §9 J/§9 K reguleringstabel rows flood every rate query). All 5 targets verified present in the graph.
- **Measured verdict — same-night matched A/B, gemma4:26b, 5× each cell** (the ONLY valid protocol — see the 2026-07-08 finding below): ON 30.0/50 det vs OFF 31.0/50 det (net −1: only 3 item flips, ALL rate items — gs-014 ↓ *answered a confidently WRONG rate, 15% instead of ASKL §14's 17%, because truncation evicted §14's row*; gs-044 ↓ lost FBL §11/22 pct.; gs-030 ↑ SEL §17's 22 pct. surfaced = the probe's fix working end-to-end). Judge (same-night, same judge over both cells): **ON 16.8 vs OFF 20.4 → −3.6**. Decision rule: judge down → **revert**. The wrong-rate failure mode alone (gs-014) disqualifies truncate-to-5 for a tax product.
- **⚠ METHODOLOGY FINDING (2026-07-08, supersedes §1's use of stored baselines):** cross-week comparisons are INVALID on this stack. Measured directly: (a) *substrate drift* — gs-023 flipped 5/5→0/5 vs baseline week with `tools=0` in both configs (identical inputs, different week); det vs the stored baseline moved −4.8 from drift alone; (b) *judge drift* — the identical baseline answers re-judged 33→20/50 by the same prompt+model a week later; (c) *in-process `--repeat 5` ≈ 1 effective sample* (stdev 0.00; warm `vector_hits_cache` + stable Ollama session = correlated replicas) — use separate-process repeats (the resumable-driver pattern) for real variance; (d) *set-version skew* — per-item diffs must diff the embedded item definitions first (gs-037's "improvement" was v4.1 dropping its citation check). **Also:** tonight's OFF answers judge at 20.4 ≈ re-judged baseline answers (20) despite det 31 vs 35.8 → the det drop from drift is *phrasing-level* brittleness of the substring scorer; content quality was flat. Judge is the real metric (re-confirmed). **Every future C-experiment: measure as same-night ON-vs-OFF matched cells via env escape hatches, judged same-night by one judge pass over both cells.**
- **Follow-ups (open, user gates):** (i) the **RECALL gap is the real headroom**, not ranking — dedup the duplicated LL §9 J/9 K reguleringstabel rows at retrieval time, and/or query-expand (moms→merværdiafgift); relates to D4/D5. (ii) reorder-only reranking (`RERANK_TOP≥15`, no truncation, direct-§ rows exempt) remains untested — plausible but modest upside; needs the matched-pair protocol. (iii) Qwen3-30B swap re-evaluation was postponed to "after C4" — C4 is now concluded.

### C6. Validate the `Skattesats_Opslag` tool ☑ INVESTIGATED 2026-07-06 (Fable) — verdict: KEEP AS-IS, relevance fix deferred INTO C4
- **User observation confirmed and quantified.** Offline replica of the tool's exact query: for `selskabsskat` the correct rate (SEL § 17, 22 pct.) ranks **4th** behind KSL/ABL/AL dividend-withholding noise; for `moms sats` ML § 33 is **missed entirely** (its text contains neither "moms" nor "sats" — pure token-containment cannot find it). Topic-in-text cases (mellemskat, aktieindkomst, aktiesparekonto) rank 1–2.
- **But outcomes are healthy:** across all v4 runs (500 item-runs) the tool fired 141× on 18 items; the rate-core items (gs-002/005/006/011/040) call it every run and pass 10/10; within-item pass-when-called 90/127 (71%) vs not-called 37/53 (70%) — aggregate-neutral. The noise costs context, not (measurably) correctness — the system prompt's deterministic §-pointers (SEL § 17, MOMSL § 33) cover the tool's blind spots.
- **Fix candidate FALSIFIED by probe:** a law-title score boost (`selskabsskat`→SEL) repaired that case (4→1) but broke 3 of 5 others — the blanket boost floods the LIMIT-6 row pool with same-score sibling rows, pushing the target provision out. Lexical ranking is a dead end here; don't retry variants of it.
- **Decision:** keep the tool unchanged (removal off the table; a measured rewrite isn't justified by neutral outcomes). **The general fix is C4's cross-encoder** — semantic scoring trivially ranks "Indkomstskatten udgør 22 pct." top for "selskabsskat". → **C4 acceptance tests must include these five rate cases** (selskabsskat/SEL §17 · moms sats/ML §33 · mellemskat 2026/LOV 482 · aktieindkomst/PSL §8a · aktiesparekonto/ASKL §14).
- Side-findings: all 20 `is_current` flags verified correct (incl. LOV 482 = current); minor behavior note — on the clarify item gs-033, reaching for this tool instead of asking correlates with failing (1/3 vs 7/7) — behavior-selection issue, no tool action taken.

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
### E2. The lenses ☑ DONE 2026-07-05 (Opus) — Graflinsen (subgraph + node-detail endpoints; Law→§+CITES view, scrub-revealed, node inspector w/ full stk text + validity + ELI link), Tankestrømmen (reasoning+tool cards, token/cost badges, I/O drill-down of full tool output + reconstructed LLM context, deterministic context search + `/api/run/{id}/analyze` AI escalation), kilder-chips (law-aware verify + ELI link + click→graph-highlight), feedback (👍/👎→mr_feedback), historical replay (mr_runs + /api/traces + loadTrace). One additive app.py change: `content_full` on tool_result events (untruncated output; does not change what the model sees). Impl note: graph simplified to Law+§+CITES (stk → inspector) for readability with real data. Verified: 30 replay assertions (`npm run test:replay`) + Playwright E2 green in a real browser (run→scrub→kilder→feedback→kilde-jumps-to-Graflinse+inspector→graph nodes→Tankestrøm drill-down; zero page errors). Remaining E2-adjacent niceties deferred: per-message citation history, kapitel nodes.
### E3. Dev depth ☑ DONE 2026-07-06 (Opus) — **Eval lens** (`/api/eval/runs` scans `eval_results_*.jsonl` → per-run summaries + pass-% dimension matrix by category/behaviour/difficulty; model/set inferred from filename when unstamped, from the E0 stamp when present; run selector + compare column [gemma-vs-flash], stat tiles, items drill-down with pass-frequency colour-coding + failed-check breakdown + last answer) and **tool-health table** (`/api/tools/health` from persisted live `mr_runs`: calls, **empty-result rate** = the dead-tool detector, mean duration). **Streamlit retired at the doc level** (CLAUDE.md: Maskinrummet frontend is now the primary UI; the app.py Streamlit UI is legacy) — the UI code is NOT deleted because app.py is the single runtime source and its module-level st.* is load-bearing for the stub-import; **full Streamlit-UI removal is a deferred careful refactor.** Fixed: run-repeat inferred from DISTINCT run_idx (the resumable gemma driver stamps run_idx=r per single-run file). Deferred (data not stored in eval records): item→Maskinrummet trace replay (eval jsonl has no event log). Verified: build clean, 30 replay assertions, Playwright E2 green incl. Eval tab, screenshots reviewed (matrix + items + tool-health render with real data).

**Streamlit-UI removal (deferred follow-up, Opus-lane w/ care):** slim app.py to the runtime (build_runtime, stream_agent_answer, tools, guards, resolve_llm_provider) by removing the sidebar/view/chat/eval render functions + the module-level `st.*` page code, while keeping the stub-import working for server.py/eval_run.py. Needs: confirm nothing imports the view fns; keep `st.cache_resource`/`st.stop` seams the stub relies on; re-run eval_run + server smoke after.

**Known follow-up — Graflinsen edge routing (FABLE-LANE, design):** node-to-node
CITES edges are trimmed to the circle rim (endpoints are clean), but edges that
span multiple law-columns still visually pass *over* intermediate nodes. Fixing
that needs real edge routing / crossing-minimisation (route around nodes, or a
layout lib, or an orthogonal/arc-bundling scheme) — a graph-layout design problem,
not a mechanical tweak. Fable 5 should work out the approach (candidate ideas:
curved arc-bundling below the node band; a force/tree hybrid; per-edge waypoints
that dodge occupied cells; or adopt a lib like elkjs/d3-dag). Until then the
current look is acceptable (labels stay readable via the paint-order halo).

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
- Long-running eval drivers: python block-buffers stdout to a file → a log-grep staging check sees nothing and kills healthy runs. Set `PYTHONUNBUFFERED=1` in the driver env.
- torch-importing processes intermittently segfault at startup in WSL2 (worse after long uptime — 2026-07-08 also saw interpreter-level corruption via the pygments import chain). Wrap heavy runs in a retry loop; never start two torch processes concurrently; `wsl --shutdown` clears a degraded VM.
- The Claude Code harness does not preserve the shell working directory between calls — use absolute paths in background/long-running commands (a relative `.venv/bin/python3` intermittently resolves to nothing).
