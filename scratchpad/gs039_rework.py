"""gs-039 rework (user-approved 2026-08-08), with the anchors graph-verified.

The item was authored as an ABSENCE test: pensionsbeskatningsloven was not in the
graph, so the correct behaviour was admit_unknown. D2 loaded PBL, so § 16 now has
real content and the agent answers it correctly. Per spec §1 this is coverage
growing, not a regression — the item flips to `answer`.

⚠ WHAT IS AND IS NOT ASSERTED. Verified directly against Neo4j
(scratchpad/gs039_verify_pbl16.py):
  * "grundbeløb på højst 50.000 kr. (2010-niveau)" for ratepension  ✅ in § 16
  * exception for ordninger omfattet af §§ 15, 15 A og 15 B          ✅ in § 16
  * "Grundbeløbet reguleres efter personskattelovens § 20"           ✅ in § 16
  * the year-schedule 42.000 / 42.900 / 43.900 / 44.800 / 45.750     ✅ in § 16
The agent's smoke answer also quoted 65.500 kr. (2025) and 68.700 kr. (2026).
Those figures appear NOWHERE in § 16 or in any reguleringstabel row in the graph
— they are the agent's own regulation arithmetic. They are therefore NOT asserted
in must_contain and are NOT stated as fact in expected_answer; the facit says the
regulated year-amount must come from the graph. Asserting an unverifiable number
would make the item a hallucination generator, which is the exact opposite of
what it was created to test.
"""
import json
from pathlib import Path

GS = Path(__file__).resolve().parent.parent / "eval_golden_set.json"
data = json.loads(GS.read_text(encoding="utf-8"))
items = {i["id"]: i for i in data["items"]}

g = items["gs-039"]
g["category"] = "typical"
g["pillar"] = "effectiveness"
g["difficulty"] = "medium"
g["tags"] = ["coverage_honesty", "pensionsbeskatningsloven", "ratepension", "d2_loaded"]
g["expected_behavior"] = "answer"
g["expected_reasoning_steps"] = [
    "Pensionsbeskatningsloven ER indlæst i grafen (D2, LBK 2024/1243).",
    "Slå PBL § 16 op og citér grundbeløbet for rateordninger derfra.",
    "Angiv at grundbeløbet reguleres efter personskattelovens § 20 — hent det "
    "årsregulerede beløb fra grafen frem for at regne det ud selv.",
]
g["expected_answer"] = (
    "Pensionsbeskatningslovens § 16 fastsætter loftet for indbetalinger til "
    "rateforsikring og rateopsparing i pensionsøjemed samt ophørende livrenter: der kan "
    "for et indkomstår i alt anvendes et grundbeløb på højst 50.000 kr. (2010-niveau) "
    "pr. person. Grundbeløbet reguleres efter personskattelovens § 20, så det beløb, der "
    "faktisk kan indbetales i et givet indkomstår, er det årsregulerede beløb — det skal "
    "hentes fra grafen, ikke udregnes. Loftet gælder ikke for ordninger omfattet af "
    "§§ 15, 15 A og 15 B. Ved opgørelsen ses bort fra bl.a. tilskrivning af bonus og "
    "renter samt erlagte arbejdsmarkedsbidrag, jf. § 16, stk. 3."
)
g["must_contain"] = ["50.000", "pensionsbeskatningslov"]
g["must_not_contain"] = [
    # the pre-D2 absence claim must never resurface now that the law IS loaded
    "ikke blandt de love",
    "kan ikke finde loven",
]
g["expected_legislation"] = [{"lov": "PBL", "paragraf": "16"}]
g["notes"] = (
    "⚠ OMSKREVET 2026-08-08 (bruger-godkendt). Item'et var oprindeligt en FRAVÆRS-test: "
    "pensionsbeskatningsloven var ikke i grafen, så admit_unknown var korrekt. D2 indlæste "
    "PBL (LBK 2024/1243), så § 16 har nu rigtigt indhold og agenten svarer korrekt — det er "
    "DÆKNING der er vokset, ikke en regression (spec §1: scope ≠ coverage). "
    "GRAFVERIFICERET (scratchpad/gs039_verify_pbl16.py): grundbeløb 50.000 kr. (2010-niveau), "
    "undtagelsen for §§ 15/15 A/15 B, og reguleringen efter PSL § 20 står ordret i § 16. "
    "IKKE ASSERTERET: agentens smoke-svar nævnte 65.500 kr. (2025) og 68.700 kr. (2026); "
    "de tal findes IKKE i § 16 eller i nogen reguleringstabel-række i grafen — de er agentens "
    "egen regulering. Derfor står de hverken i must_contain eller som fakta i facit. "
    "Fraværs-testen er ikke tabt: gs-066 (virksomhedsskattelovens § 10) er den nye "
    "admit_unknown-vagt på en lov der stadig mangler."
)

GS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("gs-039 reworked: admit_unknown -> answer, anchors graph-verified")
print("  must_contain:", g["must_contain"])
print("  must_not_contain:", g["must_not_contain"])
import collections
print("behaviour mix:", dict(collections.Counter(i["expected_behavior"] for i in data["items"])))
