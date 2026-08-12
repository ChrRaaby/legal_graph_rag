# Eval-arbejdsrummet — Eval lens rework (design doc)

**Status:** design proposed (Opus, 2026-08-12); implementation = **G2** in `TODO.md`, gated on user go.
**Visual mockup:** `whitepapers/mockups/eval_workspace_mockup.html` — as with Phase E, **the mockup IS the V1 scope definition**; build what it shows, resist adding more.
**Supersedes:** the Eval-lens portion of `frontend_maskinrummet_design.md` §"Feedback round 1" item 5 (the lens was pulled *into* the tab rail there; this doc pulls it back out, for the reason below).

## The mistake this corrects

The user, 2026-08-12:

> *"kredsløb, graflinse and tankestrøm are tools to inspect and understand a single samtale. The stuff in eval is different — it is a list of test cases that can be explored and executed, and then you have the aggregated eval history."*

The redesign doc already stated half of this without noticing the consequence. Its §"The four synchronized layers" opens: **"All layers are pure functions of `(event_log, t)`"** — one shared clock, one trace, scrubbable in lockstep. That is the definition of a lens in this product.

**Eval has no event log and no `t`.** It is a corpus you browse and execute, plus an aggregate you analyse across runs. It was placed in the tab rail during Phase E feedback because that was where dev-only surfaces were going, not because it satisfied the rail's contract. Everything that is wrong with the Eval tab today follows from that one misfiling:

- It inherits a ~400 px bottom pane sized for a diagram, and needs a table.
- It sits under a chat pane that is irrelevant to it and ~500 px of it empty.
- Its two genuinely different jobs (browse/execute vs analyse) are flattened into sub-tabs inside that pane.

**Eval is not a lens. It is a second workspace.** The fix is structural, and it is small: promote it.

## The shape

Two workspaces, switched in the header (`Samtale | Eval`), dev-gated:

| | **Samtale** | **Eval** |
|---|---|---|
| scope | one conversation | the test corpus |
| content | chat + Kredsløb / Graflinse / Tankestrøm + Tidslinjen | Testsæt + Historik |
| state | `(event_log, t)` | `(runs, filters)` |
| layout | split view (unchanged) | full height, no chat pane |

The lens rail goes back to exactly the three surfaces that are functions of a trace. That is the first time it will be conceptually clean.

### Testsæt — corpus workbench

Two columns; full height is what buys the width.

- **Left:** search, dimension filters, the 69-item table with selection.
- **Right — the execution rail:** appears on *Kør smoke*, shows live SSE progress, per-item verdicts as they land, running pass count and usage/cost, and the substrate the run is on.

This is the fix for the user's specific complaint ("the card with test execution info is hidden in the very bottom of the screen"). The root cause is DOM order, not styling: `Eval.tsx:536-537` renders `<GoldenBrowser>` — which contains the entire table — and *then* `<RunnerPanel>`, so the card is structurally guaranteed to be below the list.

The user asked for the card **on top**. The mockup puts it **beside** instead, and that is a deliberate upgrade: as a right rail the run is visible *while the list stays visible*, so items resolve against the selection you just made. Below 1100 px it collapses to a card **above** the table (`order:-1`) — never below it again. Both geometries verified in the mockup.

### Historik — aggregate analysis

Same content as today, reordered by decision value, with four corrections:

1. **Default to the newest run.** Today both selects default to **index 32 of 48** — two files from 2026-07-05 — while the newest sits at index 0. The default view is a five-week-old comparison, and nothing on screen says so.
2. **Group and label the picker.** Grouped by substrate, searchable, and labelled with the **real model**: recent entries say `ollama` where older ones say `gemma4:26b`, so which local model produced the v4.2 runs is not recoverable from the UI. G1 is about to add two more local runs, which makes this urgent rather than cosmetic.
3. **Normalise the score, segregate the stubs.** One dropdown currently mixes `/69`, `/50`, `/30`, `/13`, `/7`, `/3`, `/2`, `/1`. Show percentages, and fold smoke/debug runs into a collapsed group — a `1/1` run is not a peer of a 69-item run. Only same-set-version runs compare number-for-number; cross-version pairs stay visible but marked.
4. **Rank by signal, damp thin cells.** The tags matrix is ~40 rows sorted alphabetically, mostly `n=5`, comparison column mostly empty. Sort by gap size, and grey + label any row with `n < 10`. **A 0 % at n=5 is noise, and presenting it identically to a 43 % gap at n=35 invites acting on it.**

**Tool health moves up.** It is currently the last thing in a nested scroll and, with G4 live, the most decision-relevant table in the app: 115 calls, one tool at 66 % of them, 4 of 12 never called. It must state its own provenance — *live samtaler* vs *eval runs*, and always per substrate. The two sources must never be summed.

## The one door between the rooms

Splitting the workspaces must not sever the drill-down E4 built. UI-triggered eval runs persist their event log to `mr_runs`, so any eval run can be replayed in the lenses. **"Vis forløbet"** on a run or item switches to the Samtale workspace with that trace loaded and scrubbable.

Stated cleanly: **Eval selects and aggregates; Samtale inspects one trace.** One explicit door, no duplicated lens code in the Eval workspace.

## Mode interaction (depends on G1)

G1's decision is that the app **defaults to dev** with a user/dev toggle. That makes the Eval workspace a top-level destination on a public URL rather than something buried in a rail — with a runner behind it that spends real API money on every click.

⚠ **Open decision:** whether the Eval workspace is dev-only, or visible-but-read-only in user mode (browse and history, no runner). Basic Auth (`58fd7e7`) is currently the only thing in front of it. The smoke runner's existing confirm + item cap stay regardless; they were sized for a buried tab, not a front-door one.

## Scope

**In** (user-selected 2026-08-12): the workspace split with full-height Eval; the execution rail; newest-run defaults; normalised scores and stub segregation; grouped/searchable/model-labelled pickers; tags sorted by signal with `n<10` damped; tool health promoted; empty stat tiles collapsed.

**Out:** any change to scoring, the golden set, the runner's tier caps, or the three samtale lenses. No agent-visible change anywhere in this doc.

## Phasing

- **G2a — content fixes, inside `Eval.tsx` (632 lines).** Defaults, normalisation, pickers, tags ranking, tool-health promotion, tile collapsing, and moving `<RunnerPanel>` above `<GoldenBrowser>`. Self-contained, ships independently, and removes the actively-misleading defaults without waiting on the restructure. **Recommended first.**
- **G2b — the workspace split.** Header switch, full-height Eval route, lens rail reduced to three, the "vis forløbet" door, mode gating. Touches `App.tsx`, `Maskinrummet.tsx`, `Eval.tsx`.

Not agent-visible → no matched pair required (ground rule 6). Verify as E1–E4 were: Playwright + replay assertions + real-data eyeball.

## Risks

- **The split becomes a rewrite.** Mitigation: G2a is pure edits inside one component; G2b moves components without changing them. The mockup is the V1 contract — new ideas go to the backlog, not the sprint.
- **The door rots.** If "vis forløbet" is not built in G2b, the workspaces become genuinely separate apps and the E4 replay capability is stranded. It is not optional.
- **Damping reads as hiding.** `n<10` rows are greyed and labelled, never removed — the project's standing rule is null over fake-zero, and this is the same principle applied to statistical thinness.
- **Dev-by-default widens exposure** (G1 interaction above) — resolve the mode question before G2b ships, not after.
