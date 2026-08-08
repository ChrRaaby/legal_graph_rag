"""Refine the gs-062 / gs-065 annotation with the F3 evidence.

After the F2 run I recorded them as "fails on gemma". The F3 OFF cell — same
model, same night, one hour later — shows both PASSING. So the accurate statement
is not "fails on gemma" but "flaky on gemma": the model answers sometimes and
falls back to the clarify template other times. Ground truth stays unchanged
(answering is the better behaviour), but the reason is now correct.
"""
import json
from pathlib import Path

GS = Path(__file__).resolve().parent.parent / "eval_golden_set.json"
data = json.loads(GS.read_text(encoding="utf-8"))
items = {i["id"]: i for i in data["items"]}

OLD_MARK = " ⚠ SUBSTRATBUNDET (målt 2026-08-02)"
NEW = (" ⚠ USTABIL PÅ GEMMA (målt 2026-08-02, to celler samme nat): item'et består på "
       "gemini-flash og består OGSÅ på gemma4:26b i den ene af to kørsler — i den anden "
       "svarer gemma med systempromptens clarify-skabelon i stedet. Det er altså "
       "run-to-run-varians på gemma, ikke en fast substratforskel. Ground truth er "
       "BEVIDST uændret: at besvare spørgsmålet er bedre adfærd end at bede om "
       "præcisering, og at løsne forventningen til den svageste kørsel ville være "
       "scorer-fitting. Skjoldet fyrede korrekt IKKE i nogen af kørslerne — "
       "falsk-positiv-testen (must_not_contain) bestod hver gang.")

for iid in ("gs-062", "gs-065"):
    n = items[iid]["notes"]
    items[iid]["notes"] = (n[:n.index(OLD_MARK)] if OLD_MARK in n else n.rstrip()) + NEW
    tags = items[iid].setdefault("tags", [])
    if "substrate_dependent" in tags:
        tags[tags.index("substrate_dependent")] = "flaky_on_gemma"

GS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("refined gs-062 / gs-065: substrate_dependent -> flaky_on_gemma (ground truth unchanged)")
