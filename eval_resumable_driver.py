#!/usr/bin/env python3
"""Resumable N-run eval driver (local Ollama runs that must survive GPU handoffs).

Runs the golden set N times sequentially (like eval_run.py --repeat N) but with
item-level pause/resume: completed items are merged into per-run state files, so
stopping only ever loses the item currently generating.

Usage:
  .venv/bin/python3 eval_resumable_driver.py                # start or resume
  .venv/bin/python3 eval_resumable_driver.py --summary      # print summary from state files

Pause (frees the GPU within ~5 min — Ollama's keep_alive):
  touch PAUSE_EVAL && kill $(cat gemma_eval_child.pid)
Resume:
  rm PAUSE_EVAL && .venv/bin/python3 eval_resumable_driver.py

State: eval_results_v4_gemma_run{1..N}.jsonl (merged, deduped by item id).
When all runs are complete the driver prints the repeat summary
(per-run pass, mean/stdev, ALWAYS/NEVER/FLAKY per item).
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
RUNS = 5
GOLDEN = BASE / "eval_golden_set.json"
STATE_TMPL = "eval_results_v4_gemma_run{r}.jsonl"
PAUSE_FILE = BASE / "PAUSE_EVAL"
PID_FILE = BASE / "gemma_eval_child.pid"
OLLAMA_MODEL = "gemma4:26b"
MAX_NO_PROGRESS = 3  # consecutive attempts with 0 new records -> abort


def all_ids() -> list[str]:
    with open(GOLDEN, encoding="utf-8") as f:
        return [it["id"] for it in json.load(f)["items"]]


def read_records(path: Path) -> dict[str, dict]:
    """id -> record (first occurrence wins)."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # torn final line from a killed writer — item just reruns
        out.setdefault(r["item"]["id"], r)
    return out


def merge_part(part: Path, state: Path, run_idx: int) -> int:
    done = read_records(state)
    added = 0
    for rid, rec in read_records(part).items():
        if rid in done:
            continue
        rec["run_idx"] = run_idx
        with open(state, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        added += 1
    part.unlink(missing_ok=True)
    return added


def summary() -> None:
    ids = all_ids()
    per_run, pass_counts, complete = [], {i: 0 for i in ids}, 0
    for r in range(1, RUNS + 1):
        recs = read_records(BASE / STATE_TMPL.format(r=r))
        n_pass = sum(1 for x in recs.values() if x["scores"]["overall_pass"])
        done = len(recs)
        status = "complete" if done == len(ids) else f"partial {done}/{len(ids)}"
        print(f"RUN {r}: {n_pass}/{done} passed  ({status})")
        if done == len(ids):
            complete += 1
            per_run.append(n_pass)
        for rid, x in recs.items():
            if x["scores"]["overall_pass"]:
                pass_counts[rid] += 1
    if per_run:
        mean = statistics.mean(per_run)
        sd = statistics.stdev(per_run) if len(per_run) > 1 else 0.0
        print(f"\nComplete runs: {complete}/{RUNS}  mean pass {mean:.1f}/{len(ids)}  stdev {sd:.2f}")
    if complete == RUNS:
        always = sorted(i for i, c in pass_counts.items() if c == RUNS)
        never = sorted(i for i, c in pass_counts.items() if c == 0)
        flaky = sorted((i, c) for i, c in pass_counts.items() if 0 < c < RUNS)
        print(f"\nALWAYS pass ({len(always)}): {', '.join(always)}")
        print(f"NEVER pass ({len(never)}): {', '.join(never)}")
        print(f"FLAKY ({len(flaky)}): " + ", ".join(f"{i}({c}/{RUNS})" for i, c in flaky))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true", help="print summary from state files and exit")
    args = ap.parse_args()
    if args.summary:
        summary()
        return

    ids = all_ids()
    env = dict(os.environ, OLLAMA_MODEL=OLLAMA_MODEL)
    for r in range(1, RUNS + 1):
        state = BASE / STATE_TMPL.format(r=r)
        no_progress = 0
        while True:
            missing = [i for i in ids if i not in read_records(state)]
            if not missing:
                print(f"### RUN {r}/{RUNS} complete ({len(ids)} items) ###", flush=True)
                break
            if PAUSE_FILE.exists():
                print(f"PAUSED (run {r}, {len(missing)} items left). "
                      f"Resume: rm PAUSE_EVAL && rerun this driver.", flush=True)
                return
            print(f"### RUN {r}/{RUNS}: {len(missing)} item(s) to go ###", flush=True)
            part = BASE / f".gemma_part_r{r}_{int(time.time())}.jsonl"
            cmd = [str(BASE / ".venv/bin/python3"), str(BASE / "eval_run.py"),
                   "--llm", "ollama", "--item-ids", ",".join(missing),
                   "--output", str(part), "--no-log"]
            child = subprocess.Popen(cmd, cwd=BASE, env=env)
            PID_FILE.write_text(str(child.pid))
            rc = child.wait()
            PID_FILE.unlink(missing_ok=True)
            added = merge_part(part, state, r)
            print(f"--- attempt done (exit {rc}): +{added} record(s) merged ---", flush=True)
            if rc != 0 and not PAUSE_FILE.exists():
                no_progress = 0 if added else no_progress + 1
                if no_progress >= MAX_NO_PROGRESS:
                    print(f"ABORT: {MAX_NO_PROGRESS} consecutive attempts with no progress "
                          f"(run {r}). Check Ollama/Neo4j and rerun to resume.", flush=True)
                    sys.exit(1)
                time.sleep(10)
    print("\nAll runs complete.\n")
    summary()


if __name__ == "__main__":
    main()
