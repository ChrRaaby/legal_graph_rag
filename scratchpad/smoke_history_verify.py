"""Verify the two fixes: smoke runs land in Historik, and are replayable.

  1. a smoke run appends an eval record to eval_history/eval_results_smoke_*.jsonl
  2. that file shows up in /api/eval/runs like any other run
  3. its items carry run_id + source=smoke (CLI runs carry neither)
  4. the run_id resolves through /api/traces/<id> with a full event log — which
     is exactly what the lenses replay for a normal chat turn
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
REPO = Path(__file__).resolve().parent.parent
fail = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        fail.append(label)


def get(p):
    with urllib.request.urlopen(BASE + p, timeout=90) as r:
        return json.load(r)


def sse(path, payload, timeout=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    events, buf = [], ""
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            c = r.read(1)
            if not c:
                break
            buf += c.decode("utf-8", "replace")
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                for line in frame.split("\n"):
                    if line.startswith("data:"):
                        try:
                            events.append(json.loads(line[5:].strip()))
                        except Exception:
                            pass
    return events


before = {r["name"] for r in get("/api/eval/runs")}
print(f"runs before smoke: {len(before)}")

print("\n=== run a smoke (one gated item — fast and free of graph work) ===")
evs = sse("/api/eval/run", {"item_ids": ["gs-051"]})
v = next((e for e in evs if e.get("type") == "eval_item"), None)
check("verdict returned", v is not None)
if not v:
    sys.exit(1)
run_id = v["run_id"]
print(f"  id={v['id']} run_id={run_id} pass={v['scores']['overall_pass']}")

print("\n=== 1+2. it appears in the run history ===")
day = time.strftime("%Y%m%d", time.gmtime())
f = REPO / "eval_history" / f"eval_results_smoke_{day}.jsonl"
check("smoke history file written", f.is_file(), str(f))
after = get("/api/eval/runs")
names = {r["name"] for r in after}
check("smoke file listed by /api/eval/runs", f.name in names,
      f"new: {names - before}")
row = next((r for r in after if r["name"] == f.name), None)
if row:
    print(f"  {row['name']}  model={row['model']}  items={row['n_items']}  "
          f"usage={'yes' if row.get('usage') else 'no'}")
    check("smoke row carries usage/cost", bool(row.get("usage")))

print("\n=== 3. items carry run_id + source=smoke ===")
items = get(f"/api/eval/runs/{f.name}")["items"]
mine = next((i for i in items if i["id"] == v["id"]), None)
check("item present in drill-down", mine is not None)
if mine:
    print(f"  run_id={mine.get('run_id')} source={mine.get('source')}")
    check("item carries run_id", bool(mine.get("run_id")))
    check("item marked as smoke", mine.get("source") == "smoke")

print("\n=== 4. the run replays like a normal chat turn ===")
trace = get(f"/api/traces/{run_id}")
evtypes = [e.get("type") for e in trace.get("events", [])]
print(f"  {len(trace.get('events', []))} events: {sorted(set(evtypes))}")
check("trace resolves by run_id", bool(trace.get("run_id")))
check("trace has the answer", bool(trace.get("answer")))
check("trace carries a replayable event log",
      "run_start" in evtypes and "done" in evtypes)

print("\n=== CLI runs correctly have NO run_id (E3 gap, expected) ===")
cli = next((r for r in after if r["name"].startswith("eval_results_f3_")), None)
if cli:
    ci = get(f"/api/eval/runs/{cli['name']}")["items"][0]
    check("CLI item has no run_id", not ci.get("run_id"), str(ci.get("run_id")))

print("\n" + "=" * 60)
if fail:
    print(f"FAILED ({len(fail)}): " + "; ".join(fail))
    sys.exit(1)
print("SMOKE HISTORY + REPLAY VERIFIED")
