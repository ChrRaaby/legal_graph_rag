"""Reproducibility discriminating test — analysis.

Compares the two fresh OFF cells (guard off => zero classifier involvement)
with each other. Interpretation, fixed in advance so the number can't be
rationalised after the fact:
  ~84%+ byte-identical  -> classifier-sharing hypothesis SURVIVES (the ON cell's
                           extra generations are the plausible perturbation)
  ~47%                  -> hypothesis DIES: gemma-on-this-stack is simply less
                           reproducible than the C2-era measurement, classifier
                           or not (Ollama/driver/WSL state drift).
Also cross-checks each fresh cell against the 2026-08-02 OFF cell (cross-night
comparison — expected lower, for context only).
"""
import json
import sys

def load(p):
    return {json.loads(l)["item"]["id"]: json.loads(l)
            for l in open(p, encoding="utf-8") if l.strip()}

a = load("eval_history/eval_results_f3_repro_off1.jsonl")
b = load("eval_history/eval_results_f3_repro_off2.jsonl")
old = load("eval_history/eval_results_f3_gemma_v42_off.jsonl")

ids = sorted(set(a) & set(b), key=lambda s: int(s.split("-")[1]))
norm = lambda s: " ".join((s or "").split())

def ident(x, y, subset):
    return sum(1 for i in subset if norm(x[i]["answer"]) == norm(y[i]["answer"]))

n = len(ids)
same_ab = ident(a, b, ids)
print(f"paired items: {n}")
print(f"WITHIN-NIGHT  off1 vs off2 : {same_ab}/{n} byte-identical  ({100*same_ab//n}%)")
for name, cell in (("off1", a), ("off2", b)):
    common = sorted(set(cell) & set(old))
    s = ident(cell, old, common)
    print(f"CROSS-NIGHT   {name} vs Aug-02: {s}/{len(common)}  ({100*s//len(common)}%)")

pw = lambda c: sum(1 for i in ids if c[i]["scores"]["overall_pass"])
print(f"\ndet: off1 {pw(a)}/{n}   off2 {pw(b)}/{n}")

thresh_hi, thresh_lo = 0.75, 0.55
frac = same_ab / n
if frac >= thresh_hi:
    print("\nVERDICT: hypothesis SURVIVES — classifier-free runs reproduce tightly;")
    print("the ON cell's interleaved classifier calls remain the prime suspect for 47%.")
elif frac <= thresh_lo:
    print("\nVERDICT: hypothesis DIES — even classifier-free same-night runs sit near")
    print("the 47% level. Cause is substrate-wide (Ollama/driver/WSL state), not the")
    print("classifier. Consequence: local matched pairs need diff-first judging even")
    print("more, and byte-identity should never gate a local verdict.")
else:
    print(f"\nVERDICT: INCONCLUSIVE ({frac:.0%} — between {thresh_lo:.0%} and {thresh_hi:.0%}).")
    print("Record and move on; do not over-interpret.")
