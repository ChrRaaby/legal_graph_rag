"""Read /api/eval/runs JSON on stdin; report E4's new fields."""
import json
import sys

rows = json.load(sys.stdin)
print(f"{len(rows)} run files")
for r in rows:
    if any(k in r["name"] for k in ("f2_gemma_v42", "f3_gemma_v42_off", "f3_gemma_on", "f3_gemma_off")):
        d = r.get("dims", {})
        print(f"  {r['name']:<40} gated={r.get('gated'):<4} "
              f"pillar_rows={len(d.get('pillar', []))} tag_rows={len(d.get('tags', []))}")
