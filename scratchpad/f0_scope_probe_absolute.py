# F0 feasibility probe — zero LLM calls, local CPU embeddings only.
# Question: can the ALREADY-LOADED e5 embedding model separate tax-scope from
# off-topic queries well enough to serve as a deterministic pre-gate?
# Method: max cosine similarity of each query against a small FIXED anchor set
# (not derived from the golden set), compared across three groups:
#   A) all 50 golden-set questions  (must NOT be blocked — non-regression)
#   B) clearly off-topic queries    (should be blocked)
#   C) adjacent-band queries        (the gray zone F0 must rule on)
import json
import numpy as np
from sentence_transformers import SentenceTransformer

ANCHORS = [
    "dansk skattelovgivning og skatteregler",
    "indkomstskat, fradrag og skattepligt i Danmark",
    "moms og afgifter",
    "beskatning af aktier, udbytte og kapitalindkomst",
    "skattesatser og beløbsgrænser",
    "bo- og gaveafgift, arveafgift",
    "pension og beskatning af pensionsordninger",
    "selskabsskat og virksomhedsbeskatning",
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
    ("Hvor meget kan jeg få i dagpenge?", "velfærdsydelse, ikke skat"),
    ("Hvor meget SU kan jeg få som udeboende?", "velfærdsydelse, ikke skat"),
    ("Skal jeg betale skat af min SU?", "SKAT-spørgsmål → IN scope"),
    ("Hvordan opretter jeg et ApS?", "selskabsret, ikke skat"),
    ("Hvad er reglerne for arv, når min far dør?", "arveret; afgiftsdelen er IN"),
    ("Kan min arbejdsgiver fyre mig under barsel?", "ansættelsesret, ikke skat"),
    ("Hvordan bogfører jeg en kreditnota?", "bogføring, ikke skat"),
    ("Hvilken aktie bør jeg købe lige nu?", "investeringsråd, ikke skat"),
    ("Hvad er momssatsen i Tyskland?", "udenlandsk skatteret"),
    ("Er der skat på gevinster fra onlinespil?", "grænse: spilafgift/skat → IN-ish"),
]

model = SentenceTransformer("intfloat/multilingual-e5-large", device="cpu")

d = json.load(open("eval_golden_set.json", encoding="utf-8"))
items = d["items"] if isinstance(d, dict) and "items" in d else d
golden = [(i["id"], i["question"]) for i in items]

anchor_emb = model.encode(["passage: " + a for a in ANCHORS], normalize_embeddings=True)

def score(questions):
    emb = model.encode(["query: " + q for q in questions], normalize_embeddings=True)
    return (emb @ anchor_emb.T).max(axis=1)

g_scores = score([q for _, q in golden])
o_scores = score(OFF_TOPIC)
a_scores = score([q for q, _ in ADJACENT])

print("=== GROUP STATS (max cosine vs anchor set) ===")
for name, s in (("golden-50 (must pass)", g_scores),
                ("off-topic (should block)", o_scores),
                ("adjacent band", a_scores)):
    print(f"{name:<26} min={s.min():.4f}  mean={s.mean():.4f}  max={s.max():.4f}")

print("\n=== SEPARATION ===")
print(f"golden MIN      : {g_scores.min():.4f}  <- floor a threshold must stay under")
print(f"off-topic MAX   : {o_scores.max():.4f}  <- ceiling a threshold must stay over")
gap = g_scores.min() - o_scores.max()
print(f"gap             : {gap:+.4f}  ({'CLEAN SEPARATION' if gap > 0 else 'OVERLAP — single threshold insufficient'})")

print("\n=== golden-50, 5 lowest (closest to a would-be threshold) ===")
for idx in np.argsort(g_scores)[:5]:
    print(f"  {g_scores[idx]:.4f}  {golden[idx][0]}  {golden[idx][1][:70]}")

print("\n=== off-topic, all ===")
for idx in np.argsort(o_scores)[::-1]:
    print(f"  {o_scores[idx]:.4f}  {OFF_TOPIC[idx][:70]}")

print("\n=== adjacent band, all (label = F0's expected ruling) ===")
for idx in np.argsort(a_scores)[::-1]:
    q, label = ADJACENT[idx]
    print(f"  {a_scores[idx]:.4f}  {q[:58]:<58}  [{label}]")
