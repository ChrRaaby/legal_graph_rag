"""Read /api/eval/runs/<name> JSON on stdin; report gate_flag coverage."""
import json
import sys

d = json.load(sys.stdin)
items = d.get("items", [])
gated = [i for i in items if i.get("gate_flag")]
print(f"{len(items)} items, {len(gated)} with gate_flag")
for i in gated:
    print(f"  {i['id']} -> {i['gate_flag']}")
