"""Record the measured substrate dependence on gs-062 / gs-065.

Both pass on flash (expected_behavior=answer) and fail on gemma4:26b, which
returns the system prompt's clarify template instead. Ground truth is left
UNCHANGED on purpose: answering "Skal jeg betale skat af min SU?" is better
behaviour than asking for clarification, so the item is correctly registering a
real capability difference. Relaxing it to match the weaker model would be
scorer-fitting — the failure mode this project has burned sessions on.
"""
import json
from pathlib import Path

GS = Path(__file__).resolve().parent.parent / "eval_golden_set.json"
data = json.loads(GS.read_text(encoding="utf-8"))
items = {i["id"]: i for i in data["items"]}

NOTE = (" ⚠ SUBSTRATBUNDET (målt 2026-08-02): item'et består på gemini-flash "
        "(expected_behavior=answer) men fejler på gemma4:26b, som i stedet svarer med "
        "systempromptens clarify-skabelon. Ground truth er BEVIDST ikke ændret: at "
        "besvare spørgsmålet er bedre adfærd end at bede om præcisering, så item'et "
        "registrerer en reel kapabilitetsforskel. At løsne forventningen til den "
        "svagere model ville være scorer-fitting. Skjoldet fyrede korrekt IKKE i begge "
        "kørsler — falsk-positiv-testen (must_not_contain) bestod på begge substrater.")

for iid in ("gs-062", "gs-065"):
    items[iid]["notes"] = items[iid]["notes"].rstrip() + NOTE
    items[iid].setdefault("tags", []).append("substrate_dependent")

GS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("annotated gs-062, gs-065 as substrate_dependent (ground truth unchanged)")
