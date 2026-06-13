# CITES cross-reference edges — design / prep notes

Goal: enable multi-law citation chains (gs-024: LL § 2 → LL § 16 A → PSL § 8 a;
gs-025: LL § 7 P → ABL § 12 → PSL § 8 a) by encoding the statutory cross-references
that already exist *in the text* as `CITES` edges in the graph, then surfacing a
provision's outgoing citations during retrieval so the agent can follow the chain.

This is **prep only** — not yet implemented. Status as of 2026-06-12.

## Feasibility (measured)
- Cross-law references in `Paragraph.text`: **2857 parsed** ("[lov]lovens § X").
- Same-law references ("jf. § X"): **464**.
- Naive parse+resolve: law 63%, section 56%. The shortfall is **mostly expected**
  (see failure mode 1), not a real accuracy problem.

## Failure modes (what the parser must handle)
1. **Out-of-graph laws (dominant, ~37% of "unresolved").** The text cites ~35
   laws NOT loaded in the graph: arbejdsmarkedsbidragsloven, boafgiftsloven,
   dødsboskatteloven, ejendomsavancebeskatningsloven, ejendomsskatteloven,
   ejendomsvurderingsloven, ejendomsværdiskatteloven, fusionsskatteloven,
   kulbrinteskatteloven, pensionsafkastbeskatningsloven, pensionsbeskatningsloven,
   opkrævningsloven, etc.  → **Correctly skip these** (no target node). CITES only
   ever connects the 10 loaded laws. The true in-graph resolution rate is much
   higher than 56%.
2. **§ parser over-capture (minor).** "§ 1 E" (×41) etc. come from grabbing a
   trailing letter that is actually `litra`/the next word. KSL § 1 has no Section
   "1 E".  → Parse `§ <num>[ <UPPER-letter>]` where the letter is a genuine
   section-letter (graph stores "2 A", "9 C" — uppercase, single, space-sep), and
   STOP before `, stk.` / `, nr.` / `, litra` / lowercase continuations.

## Law-name → graph description map (build this)
Graph has 10 current laws (use `is_current` version as the target):
Personskatteloven, Ligningsloven, Selskabsskatteloven, Kildeskatteloven,
Momsloven, Aktieavancebeskatningsloven, Kursgevinstloven, Afskrivningsloven,
Fondsbeskatningsloven, Aktiesparekontoloven.
Aliases needed beyond simple stem match:
- "merværdiafgiftsloven" / "momsloven" → Momsloven
- common abbreviations in text: LL, PSL, SEL, KSL, ML, ABL, KGL, AL, FBL, ASKL
- genitive forms ("ligningslovens") → strip "ens"/"s".

## § normalization (reuse existing logic)
Same rules as the hallucination guard / Regulering parser: uppercase the
section-letter, collapse whitespace ("12 a" → "12 A"). Resolve against the
**current-version** law's `Section.number` set only.

## Edge model
- `(:Section)-[:CITES]->(:Section)` — source = the Section containing the ref,
  target = the resolved Section in the (current) cited law.
- Source from **current-version laws only** (don't mint edges from historic text).
- Same-law refs ("jf. § X" with no law name): resolve within the source's own law.
- **High-confidence only**: create an edge only when BOTH the law resolves to a
  loaded law AND the § resolves to a real Section node. Skip everything else
  (partial coverage with zero wrong edges — the right tradeoff for a legal KB).
- Idempotent `MERGE`, same migration-script pattern as `build_supersedes_edges.py`.
  Optionally store the raw reference phrase as an edge property for audit.

## Retrieval integration (the payoff)
In `retrieve_text_with_context`, for each returned Section/Paragraph, add a
`krydshenvisninger` field listing its outgoing CITES targets as ready citations
(e.g. `["Personskatteloven § 8 a", "Aktieavancebeskatningsloven § 13 A"]`). This
hands the agent the next link in the chain deterministically — the citation-chain
items fail today because the agent can't discover the related §, not because it
won't cite. Pure data enrichment, **no prompt change** (the citation-fix attempt
proved prompt directives churn without helping).
- Optionally also make `Citation_Network_Explorer` traverse the new §-level CITES
  (it currently queries Legislation-level CITES, which never existed).

## Validation plan
- After building edges: spot-check a sample of CITES against the source text for
  correctness (no wrong edges is the bar).
- Then 5× local-gemma eval; watch gs-024, gs-025 (chain items) and confirm no
  regression on the stable 16. Measure AFTER the supersession (#1) result is in,
  so the two changes aren't entangled.

## Out of scope (deliberately)
- Doctrinal concepts not in statute ("maskeret udlodning", "rette
  indkomstmodtager") — these are not textual cross-references; a separate curated
  concept→§ layer would be needed (lower priority).
