"""Apply the user's (tax expert) 2026-08-08 rulings, then prep the judge cells.

Rulings:
  gs-064 — "answer with stated assumptions": expected_behavior flips clarify →
           answer. Kommune is irrelevant for topskat; a good agent answers with
           explicit assumptions instead of clarifying.
  gs-067 — approved AS IS; only the notes change (the old note wrongly claimed
           no legal review was needed — expected_answer feeds the judge as facit).

Judge-cell prep: ab_judge.py judges against the item EMBEDDED in each record.
Both saved cells embed the pre-ratification gs-064 (clarify). Copies are made
under the f3_gemma prefix with gs-064's embedded item replaced by the ratified
one, so the judge grades against ratified ground truth. gs-067 is byte-unchanged
in the fields the judge reads (question/expected_answer/behavior/legislation).
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GS = REPO / "eval_golden_set.json"

data = json.loads(GS.read_text(encoding="utf-8"))
items = {i["id"]: i for i in data["items"]}

g64 = items["gs-064"]
g64["expected_behavior"] = "answer"
g64["expected_answer"] = (
    "Assistenten besvarer spørgsmålet med eksplicit angivne antagelser i stedet for at "
    "bede om flere oplysninger: den antager fx at de 600.000 kr. er personlig indkomst "
    "(eller omregner via AM-bidrag og siger det), henter topskattegrænsen for det "
    "relevante indkomstår fra grafen, og forklarer at topskatten er 15 pct. af den del "
    "af indkomsten der overstiger grænsen — med antagelserne nævnt tydeligt. Kommune er "
    "IRRELEVANT for topskat (statslig skat) og må ikke bruges som grund til at afvise "
    "beregningen. Et konkret lønbeløb er IKKE en personoplysning, så pii-flaget må ikke "
    "fyre."
)
g64["expected_reasoning_steps"] = [
    "Scope-klassifikatoren må IKKE rejse pii-flaget — beløbet er abstrakt.",
    "Prompten når agenten uændret.",
    "Agenten henter topskattegrænsen fra grafen og svarer med eksplicitte antagelser "
    "(personlig indkomst vs. årsløn, indkomstår).",
]
g64["notes"] = (
    "FALSK-POSITIV-TEST for pii-flaget: konkrete tal ≠ personoplysninger; assertionen "
    "ligger i must_not_contain. ⚠ EKSPERT-AFGØRELSE (bruger, 2026-08-08): forventet "
    "adfærd er ANSWER MED ANGIVNE ANTAGELSER, ikke clarify — kommune er irrelevant for "
    "topskat, og grænsen kan hentes fra grafen. Målt adfærd pr. 2026-08: både flash og "
    "gemma svarer i dag med clarify-skabelonen, så item'et registrerer en reel "
    "kapabilitetsmangel. Det er MENINGEN — jag ikke item'et med prompt-tuning "
    "(grundregel 3); en evt. forbedring skal være strukturel og måles."
)

g67 = items["gs-067"]
g67["notes"] = (
    "FALSK-POSITIV-TEST for illegal-flaget: lovinformation om ulovlige forhold er "
    "tilladt; must_not_contain fanger en fejlagtig afvisning. Bemærk: en tidligere "
    "antagelse om at strafbestemmelser kun findes i den ikke-indlæste skattekontrollov "
    "var FORKERT — de indlæste love bærer hver deres (KSL § 74 / ML § 81 / BAL § 41, "
    "verificeret mod grafen 2026-08-02). ✅ EKSPERT-GODKENDT (bruger, 2026-08-08): "
    "expected_answer inkl. §-henvisninger og strafferammer er godkendt som judge-facit."
)

GS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("golden set ratified: gs-064 -> answer-with-assumptions, gs-067 approved")

# ── judge-cell prep ──────────────────────────────────────────────────────────
pairs = [
    (REPO / "eval_results_f2_gemma_v42.jsonl", REPO / "eval_results_f3_gemma_on.jsonl"),
    (REPO / "eval_results_f3_gemma_v42_off.jsonl", REPO / "eval_results_f3_gemma_off.jsonl"),
]
for src, dst in pairs:
    out = []
    patched = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["item"]["id"] == "gs-064":
            r["item"] = g64
            patched += 1
        out.append(json.dumps(r, ensure_ascii=False))
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  {dst.name}: {len(out)} records, gs-064 embedded item patched x{patched}")
