# F0 probe v2 — contrastive margin variant.
# score = max_sim(tax anchors) - max_sim(off-topic anchors); block if < 0.
# Relative comparison should cancel e5's compressed-cosine offset.
import json
import numpy as np
from sentence_transformers import SentenceTransformer

TAX_ANCHORS = [
    "dansk skattelovgivning og skatteregler",
    "indkomstskat, fradrag og skattepligt i Danmark",
    "moms og afgifter",
    "beskatning af aktier, udbytte og kapitalindkomst",
    "skattesatser og beløbsgrænser",
    "bo- og gaveafgift, arveafgift",
    "pension og beskatning af pensionsordninger",
    "selskabsskat og virksomhedsbeskatning",
]
# Deliberately generic counter-domains — NOT tuned to the probe questions.
OFF_ANCHORS = [
    "madlavning og opskrifter",
    "sport og fodboldresultater",
    "programmering og softwareudvikling",
    "underholdning, film og serier",
    "rejser og geografi",
    "helbred, træning og livsstil",
    "kæledyr og familieliv",
    "jobsøgning og ansøgninger",
    "oversættelse mellem sprog",
    "filosofi og livsspørgsmål",
]

OFF_TOPIC = [
    "Skriv et Python-script der sorterer en liste",
    "Hvad er hovedstaden i Frankrig?",
    "Hvordan bager jeg en chokoladekage?",
    "Hvem vandt VM i fodbold i 2022?",
    "Skriv en jobansøgning til en stilling som pædagog",
    "Hvad er meningen med livet?",
    "Oversæt 'jeg elsker dig' til engelsk",
    "Hvordan virker en elbils batteri?",
    "Fortæl mig en joke",
    "Hvad er den bedste serie på Netflix lige nu?",
    "Hvordan træner jeg til et maraton?",
    "Hvilken hund passer bedst til en børnefamilie?",
]
ADJACENT = [
    ("Skal jeg betale skat af min SU?", "IN (skat af ydelse)"),
    ("Hvad er reglerne for arv, når min far dør?", "afgiftsdel IN"),
    ("Er der skat på gevinster fra onlinespil?", "IN-ish"),
    ("Hvor meget kan jeg få i dagpenge?", "OUT (ydelse)"),
    ("Hvad er momssatsen i Tyskland?", "OUT (udenlandsk)"),
    ("Hvor meget SU kan jeg få som udeboende?", "OUT (ydelse)"),
    ("Hvilken aktie bør jeg købe lige nu?", "OUT (investeringsråd)"),
    ("Hvordan opretter jeg et ApS?", "OUT (selskabsret)"),
    ("Hvordan bogfører jeg en kreditnota?", "OUT (bogføring)"),
    ("Kan min arbejdsgiver fyre mig under barsel?", "OUT (ansættelsesret)"),
]

model = SentenceTransformer("intfloat/multilingual-e5-large", device="cpu")
d = json.load(open("eval_golden_set.json", encoding="utf-8"))
items = d["items"] if isinstance(d, dict) and "items" in d else d
golden = [(i["id"], i["question"]) for i in items]

tax_emb = model.encode(["passage: " + a for a in TAX_ANCHORS], normalize_embeddings=True)
off_emb = model.encode(["passage: " + a for a in OFF_ANCHORS], normalize_embeddings=True)

def margins(questions):
    emb = model.encode(["query: " + q for q in questions], normalize_embeddings=True)
    return (emb @ tax_emb.T).max(axis=1) - (emb @ off_emb.T).max(axis=1)

g = margins([q for _, q in golden])
o = margins(OFF_TOPIC)
a = margins([q for q, _ in ADJACENT])

print("=== CONTRASTIVE MARGIN (tax_sim - offtopic_sim; block if < threshold) ===")
for name, s in (("golden-50", g), ("off-topic", o), ("adjacent", a)):
    print(f"{name:<10} min={s.min():+.4f}  mean={s.mean():+.4f}  max={s.max():+.4f}")
print(f"\ngolden MIN {g.min():+.4f}  vs  off-topic MAX {o.max():+.4f}"
      f"  ->  gap {g.min() - o.max():+.4f} "
      f"({'CLEAN' if g.min() > o.max() else 'OVERLAP'})")

print("\ngolden-50, 5 lowest margins:")
for idx in np.argsort(g)[:5]:
    print(f"  {g[idx]:+.4f}  {golden[idx][0]}  {golden[idx][1][:66]}")
print("\noff-topic, highest margins first:")
for idx in np.argsort(o)[::-1]:
    print(f"  {o[idx]:+.4f}  {OFF_TOPIC[idx][:66]}")
print("\nadjacent band:")
for idx in np.argsort(a)[::-1]:
    q, label = ADJACENT[idx]
    print(f"  {a[idx]:+.4f}  {q[:54]:<54}  [{label}]")
