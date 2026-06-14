# Concept layer — design / prep notes

Goal: encode the **non-textual** legal connections that CITES (textual cross-refs)
cannot capture — the income-category spine (rule → indkomstkategori → rate hjemmel)
and a few named doctrines — as deterministic graph data, surfaced in retrieval so a
small model completes multi-law chains without inferring them.

**PREP ONLY — do NOT implement until the CITES test is finalized/measured.**
Status 2026-06-14.

## Why (the gap CITES leaves)
A complete Danish tax answer connects a substantive rule to the income *category*
it produces and to that category's *taxation hjemmel* (rate). That hop is never in
the law text, so CITES can't see it. The judge keeps docking gs-024/025 for the
missing rate-half of the chain. Example (gs-025): LL § 7 P → gain is **aktieindkomst**
→ ABL § 12 + **PSL § 8 a**. "ABL § 12 → PSL § 8 a" is a category fact, not a textual ref.

## Key insight: it already exists as PROSE in the system prompt
The `CITATIONSKÆDER` block hard-codes these chains as instructions:
- Tab på aktier: ABL § 13/§ 13 A → PSL § 8 a (aktieindkomst)
- Rentefradrag: PSL § 4 (kapitalindkomst-def) + PSL § 11 (skatteværdi-loft)
- Underskud selvstændig: PSL § 13 + PSL § 13 a
- Medarbejderaktier (LL § 7 P, salg): ABL § 12 + PSL § 8 a
Small models follow prose chain-rules INCONSISTENTLY (Sonnet's circles; the
citation-fix regression). The concept layer = **move this from the prompt into the
graph** so it's retrieved as data, not "instructed" — and the prompt can shrink.

## Verified preconditions (2026-06-14)
- All seed §§ EXIST in current versions: PSL § 8 a/§ 4/§ 4 a/§ 11, ABL § 12/§ 13/§ 13 A,
  LL § 2/§ 16 A/§ 7 P, SEL § 17.
- Income-category terms are in the graph text: aktieindkomst (57), kapitalindkomst (85),
  personlig indkomst (27), CFC-indkomst (54).
- maskeret udlodning (0) and rette indkomstmodtager (0) are NOT in text → new
  doctrine Concept nodes. rette indkomstmodtager's classic hjemmel is SL § 4, which
  is NOT loaded (see [[kg_structure_work]]) → leave it ungrounded or point to its
  practical hjemler (LL § 2 / § 16 A); flag in the answer.

## Node / edge model
```
(:Concept {navn, type})           type ∈ "indkomstkategori" | "doktrin"
(:Concept)-[:DEFINERET_AF]->(:Section)        // where the category is defined
(:Concept)-[:BESKATTES_EFTER]->(:Section)     // the rate/taxation hjemmel
(:Section)-[:GIVER_INDKOMST]->(:Concept)      // this provision yields income of this category
(:Concept {doktrin})-[:HJEMMEL]->(:Section)   // doctrine -> its statutory hook
```
Targets are CURRENT-version Section nodes (resolve like build_cites_edges.py).

## Drafted seed set (~30 edges — FOR EXPERT REVIEW, not yet written)
Income categories:
| Concept | DEFINERET_AF | BESKATTES_EFTER |
|---|---|---|
| aktieindkomst | PSL § 4 a | PSL § 8 a |
| kapitalindkomst | PSL § 4 | PSL § 11 (skatteværdi-loft for neg.) |
| personlig indkomst | PSL § 3 | (progression: PSL §§ 6, 7, 7 a, 8) |
| CFC-indkomst | PSL § 4, nr. 9 | PSL § 8 b |

Provision → category (GIVER_INDKOMST):
| Section | → Concept | note |
|---|---|---|
| ABL § 12 | aktieindkomst | gevinst på aktier |
| ABL § 13 / § 13 A | aktieindkomst | tab på aktier |
| LL § 16 A | aktieindkomst | udbytte |
| LL § 7 P | aktieindkomst | medarbejderaktier ved salg (via ABL § 12) |
| PSL § 4 | kapitalindkomst | renteudgifter m.v. |

Doctrines:
| Concept | HJEMMEL | GIVER_INDKOMST |
|---|---|---|
| maskeret udlodning | LL § 16 A | aktieindkomst |
| armslængde | LL § 2 | — |
| rette indkomstmodtager | (SL § 4 not loaded; practical: LL § 2/§ 16 A) | — |

## Retrieval integration (the payoff)
Extend the krydshenvisninger enrichment: when a retrieved Section
-[:GIVER_INDKOMST]->(c)-[:BESKATTES_EFTER]->(hjemmel), add a `beskatningshjemmel`
field, e.g. `"PSL § 8 a (aktieindkomst)"`. For doctrine Concepts retrieved by
topic, surface their HJEMMEL. This completes the rule→category→rate chain as DATA.
Pure additive output, NO prompt change. Once it lands, DELETE the now-redundant
`CITATIONSKÆDER` prose from the system prompt (shrinks the prompt, removes a
churn-prone directive) — but only after measuring the data path works.

## Build approach
`build_concept_layer.py` — a small curated migration (like build_supersedes_edges.py):
a hand-written Python list of the seed edges above, resolved to current-version
Section element-ids, idempotent MERGE, dry-run by default. Because these edges
ASSERT legal relationships (unlike CITES which mirrors text), the seed list must be
**expert-reviewed by the user** before --commit. Small + bounded (tens of edges).

## Measurement
After building + the retrieval `beskatningshjemmel` field: agent run + offline judge
re-score vs the current baseline; watch gs-024, gs-025, gs-010 (chain/category
items). Revert the retrieval block if it churns (same protocol as CITES).

## Limitations
- Asserts doctrine → must be curated/verified, not derived.
- The income-category system has nuances the flat model simplifies (positive vs
  negative kapitalindkomst, progression bases) — keep the seed to the high-value,
  unambiguous links the prompt already encodes; expand cautiously.
