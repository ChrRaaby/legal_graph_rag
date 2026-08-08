import json, sys
off = {json.loads(l)["item"]["id"]: json.loads(l) for l in open(sys.argv[1], encoding="utf-8")}
for iid in ("gs-055", "gs-053", "gs-058"):
    r = off[iid]
    print("=" * 72)
    print(f"{iid}  latency={r['latency_s']}s  pass={r['scores']['overall_pass']}  "
          f"det={r['scores'].get('detected_behavior')}")
    print(f"Q: {r['item']['question']}")
    print(f"ANSWER (len {len(r['answer'])}):")
    print(repr(r["answer"][:700]))
    print()
