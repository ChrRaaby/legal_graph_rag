"""E4 verify: drive POST /api/eval/run end-to-end and check the contract.

Runs one gated item (gs-051, expect the shield to answer it: no tools, gate_flag
set) and one normal item (gs-001, expect the agent + tools), then asserts the
event shape, the scoring, and that BOTH runs were persisted to mr_runs — which is
what makes an eval item's trace replayable (E3's deferred gap).
"""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
REPO = Path(__file__).resolve().parent.parent
fail = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        fail.append(label)


def stream(item_ids):
    req = urllib.request.Request(
        f"{BASE}/api/eval/run",
        data=json.dumps({"item_ids": item_ids}).encode(),
        headers={"Content-Type": "application/json"})
    events = []
    with urllib.request.urlopen(req, timeout=600) as r:
        buf = ""
        while True:
            chunk = r.read(1)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                for line in frame.split("\n"):
                    if line.startswith("data:"):
                        try:
                            events.append(json.loads(line[5:].strip()))
                        except Exception:
                            pass
    return events


print("=== smoke run: gs-051 (gated) + gs-001 (normal) ===")
evs = stream(["gs-051", "gs-001"])
types = [e.get("type") for e in evs]
print(f"  {len(evs)} events: {sorted(set(types))}")

starts = [e for e in evs if e.get("type") == "eval_item_start"]
verdicts = [e for e in evs if e.get("type") == "eval_item"]
check("two eval_item_start events", len(starts) == 2, str(len(starts)))
check("two eval_item verdicts", len(verdicts) == 2, str(len(verdicts)))
check("eval_done terminates the stream", types[-1] == "eval_done", types[-1] if types else "none")
check("each item emitted its own run_start", types.count("run_start") == 2)

by_id = {v["id"]: v for v in verdicts}

g = by_id.get("gs-051")
if g:
    print(f"\n  gs-051: gate_flag={g['gate_flag']} pass={g['scores']['overall_pass']} "
          f"latency={g['latency_s']}s tools={g['scores'].get('tool_call_count')}")
    check("gs-051 was gated", g["gate_flag"] == "non_tax", str(g["gate_flag"]))
    check("gs-051 called no tools", g["scores"].get("tool_call_count") == 0)
    check("gs-051 passes its golden checks", g["scores"]["overall_pass"] is True)
    check("gs-051 scope_gate event reached the client",
          any(e.get("type") == "scope_gate" for e in evs))
else:
    check("gs-051 verdict present", False)

n = by_id.get("gs-001")
if n:
    print(f"  gs-001: gate_flag={n['gate_flag']} pass={n['scores']['overall_pass']} "
          f"latency={n['latency_s']}s tools={n['scores'].get('tool_call_count')}")
    check("gs-001 was NOT gated", n["gate_flag"] is None, str(n["gate_flag"]))
    check("gs-001 actually used tools", (n["scores"].get("tool_call_count") or 0) > 0)
    check("gs-001 verdict carries full score detail",
          "must_contain_details" in n["scores"])
else:
    check("gs-001 verdict present", False)

print("\n=== persistence: both runs replayable from mr_runs (E3 gap closed) ===")
db = REPO / "observability.db"
con = sqlite3.connect(db)
ok = 0
for v in verdicts:
    row = con.execute("SELECT question, events FROM mr_runs WHERE run_id=?", (v["run_id"],)).fetchone()
    if row:
        n_ev = len(json.loads(row[1] or "[]"))
        ok += 1
        print(f"  {v['id']}: run_id={v['run_id']} stored with {n_ev} events")
con.close()
check("every smoke run persisted to mr_runs", ok == len(verdicts), f"{ok}/{len(verdicts)}")

print("\n=== the run appears in /api/traces (so the UI can replay it) ===")
with urllib.request.urlopen(f"{BASE}/api/traces", timeout=30) as r:
    traces = json.load(r)
ids = {t["run_id"] for t in traces}
check("smoke run_ids are listed as traces",
      all(v["run_id"] in ids for v in verdicts),
      f"missing {[v['run_id'] for v in verdicts if v['run_id'] not in ids]}")

print("\n" + "=" * 60)
if fail:
    print(f"FAILED ({len(fail)}): " + "; ".join(fail))
    sys.exit(1)
print("E4 RUNNER VERIFIED")
