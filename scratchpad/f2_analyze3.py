"""Analyse the gemma v4.2 run WITHOUT importing app (no torch → no segfault).

tool_events are not persisted in eval records (E3 known gap), so gating is
verified by byte-exact equality between the answer and the item's
expected_answer — which for blocked items IS the deterministic template. Only
the gate can produce that string verbatim.
"""
import json
import sys

rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
tags = lambda r: r["item"].get("tags") or []
gate = [r for r in rows if "f1_gate" in tags(r)]
blocked = [r for r in gate if "gate_must_not_fire" not in tags(r)]
traps = [r for r in gate if "gate_must_not_fire" in tags(r)]
legacy = [r for r in rows if int(r["item"]["id"].split("-")[1]) <= 50]

TEMPLATES = {r["item"]["expected_answer"].strip() for r in blocked}
is_tmpl = lambda r: r["answer"].strip() in TEMPLATES

print("record keys:", sorted(rows[0].keys()))
print()
print("=== GATE FIRED where required (answer == template, byte-exact) ===")
miss = []
for r in sorted(blocked, key=lambda r: r["item"]["id"]):
    ok = r["answer"].strip() == r["item"]["expected_answer"].strip()
    miss += [] if ok else [r["item"]["id"]]
    print(f"  {'OK ' if ok else 'XX '} {r['item']['id']}  "
          f"{r['item']['expected_behavior']:<13} {r['latency_s']:>5.1f}s")
print(f"  -> {len(blocked)-len(miss)}/{len(blocked)}" + (f"  MISSED {miss}" if miss else "  <- all gated"))

print("\n=== GATE SILENT where required (no false positives) ===")
fp = [r["item"]["id"] for r in traps if is_tmpl(r)]
for r in sorted(traps, key=lambda r: r["item"]["id"]):
    print(f"  {'XX ' if is_tmpl(r) else 'OK '} {r['item']['id']}  "
          f"exp={r['item']['expected_behavior']:<13} "
          f"det={r['scores'].get('detected_behavior','?'):<13} "
          f"pass={r['scores']['overall_pass']}")
print(f"  -> {len(traps)-len(fp)}/{len(traps)} reached the agent"
      + (f"  FALSE POSITIVES {fp}" if fp else "  <- zero false positives"))

gl = [r["latency_s"] for r in blocked]
ol = [r["latency_s"] for r in rows if not is_tmpl(r)]
print(f"\n  latency: gated {sum(gl)/len(gl):.1f}s  vs  agent {sum(ol)/len(ol):.1f}s"
      f"  ({sum(ol)/len(ol)/(sum(gl)/len(gl)):.1f}x faster)")

pw = lambda rs: sum(1 for r in rs if r["scores"]["overall_pass"])
print(f"\n=== scores ===")
print(f"  TOTAL        {pw(rows)}/{len(rows)}")
print(f"  legacy 50    {pw(legacy)}/{len(legacy)}   (gs-039 is a KNOWN expected fail)")
print(f"  new F2 19    {pw(gate)}/{len(gate)}")
print(f"    blocked    {pw(blocked)}/{len(blocked)}")
print(f"    traps      {pw(traps)}/{len(traps)}")
print("\n  new-item failures:")
for r in gate:
    if not r["scores"]["overall_pass"]:
        print(f"    {r['item']['id']}  exp={r['item']['expected_behavior']} "
              f"det={r['scores'].get('detected_behavior')}  "
              f"failed={r['scores'].get('failed_checks')}")
