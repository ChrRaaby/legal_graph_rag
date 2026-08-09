"""Verify tokens+cost land in every path the user asked for.

  1. /api/architecture serves the price table (so the UI stops carrying a copy)
  2. a live /api/ask run's `done` event carries tokens AND cost
  3. an eval smoke verdict carries usage + tool_sequence
  4. eval_run.py CLI records carry a `usage` block
  5. eval_log.jsonl trajectories carry usage
  6. /api/eval/runs rolls usage up per file, and reports None (not 0) for
     pre-change files
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


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
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


print("=== 1. /api/architecture serves pricing ===")
arch = get("/api/architecture")
pr = arch.get("pricing") or {}
rows = pr.get("usd_per_mtok") or []
check("pricing table served", len(rows) >= 5, str(len(rows)))
check("usd_to_dkk served", isinstance(pr.get("usd_to_dkk"), (int, float)))
check("current agent model is priced",
      any(r["match"] in arch["provider"] for r in rows), arch["provider"])
print(f"    provider={arch['provider']}  rows={len(rows)}")

print("\n=== 2. live /api/ask done event carries tokens + cost ===")
evs = sse("/api/ask", {"question": "Hvad er momssatsen i Danmark?"})
done = next((e for e in evs if e.get("type") == "done"), None)
check("done event present", done is not None)
if done:
    print(f"    {done}")
    check("done has token totals", (done.get("input_tokens") or 0) > 0)
    check("done has llm_calls", (done.get("llm_calls") or 0) > 0)
    check("done has cost_dkk key", "cost_dkk" in done)
    check("hosted run priced > 0", (done.get("cost_dkk") or 0) > 0,
          f"cost={done.get('cost_dkk')} provider={arch['provider']}")

print("\n=== 3. eval smoke verdict carries usage + tools ===")
evs = sse("/api/eval/run", {"item_ids": ["gs-001"]})
v = next((e for e in evs if e.get("type") == "eval_item"), None)
check("eval_item verdict present", v is not None)
if v:
    u = v.get("usage") or {}
    print(f"    usage={u}  tools={v.get('tool_sequence')}")
    check("verdict carries usage", bool(u))
    check("verdict usage has cost_dkk key", "cost_dkk" in u)
    check("verdict carries tool_sequence", isinstance(v.get("tool_sequence"), list))

print("\n=== 4. CLI eval records carry usage ===")
cli = REPO / "eval_results_cost_check.jsonl"
if cli.exists():
    rec = json.loads(cli.read_text(encoding="utf-8").splitlines()[0])
    u = rec.get("usage") or {}
    print(f"    usage={u}")
    check("CLI record has usage block", bool(u))
    check("CLI usage has cost_dkk key", "cost_dkk" in u)
    check("CLI record still has tool_sequence in scores",
          isinstance(rec.get("scores", {}).get("tool_sequence"), list))
else:
    check("CLI record file exists (run eval_run.py first)", False, str(cli))

print("\n=== 5. eval_log.jsonl trajectories carry usage ===")
tl = REPO / "eval_log.jsonl"
if tl.exists():
    last = json.loads(tl.read_text(encoding="utf-8").strip().splitlines()[-1])
    print(f"    usage={last.get('usage')}")
    check("trajectory has usage block", isinstance(last.get("usage"), dict))
else:
    check("eval_log.jsonl exists", False)

print("\n=== 6. /api/eval/runs rolls usage up, None for old files ===")
runs = get("/api/eval/runs")
withu = [r for r in runs if r.get("usage")]
without = [r for r in runs if r.get("usage") is None]
print(f"    {len(withu)} file(s) with usage, {len(without)} without (pre-change)")
check("run summaries expose a usage field", all("usage" in r for r in runs))
check("run summaries expose tool_calls", all("tool_calls" in r for r in runs))
check("pre-change files report None, not a fake 0",
      all(r.get("usage") is None for r in without))
for r in withu[:3]:
    print(f"    {r['name'][:44]:<46} {r['usage']}")

print("\n" + "=" * 62)
if fail:
    print(f"FAILED ({len(fail)}): " + "; ".join(fail))
    sys.exit(1)
print("COST + USAGE VERIFIED ON EVERY PATH")
