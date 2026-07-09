"""Generalized resumable matched-pair driver — level L1/L2 of the eval ladder.

Runs the ON and OFF cells of an env-gated change (the §2 protocol), resumably:
each cell resumes from its output file's done-ids; each eval_run call is a fresh
subprocess writing to a temp file that is appended on exit (eval_run truncates
its --output at startup — never point it at the accumulating file). Sequential
cells; per-attempt timeout catches silent stalls; WSL2 torch segfaults are
absorbed by the retry loop.

Usage:
  .venv/bin/python3 ab_driver.py --env-var C5_PROMPT_LEAN --prefix c5_flash
  .venv/bin/python3 ab_driver.py --env-var C2_DIRECT_NARROW --prefix c2_sent \
      --item-ids gs-001,gs-007,...   --workers 4
Cells land in eval_results_<prefix>_{on,off}.jsonl. Judge with ab_judge.py.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent


def done_ids(path: Path) -> set:
    if not path.exists():
        return set()
    return {json.loads(l)["item"]["id"] for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def run_cell(cell: str, ids: list, args) -> bool:
    final = REPO / f"eval_results_{args.prefix}_{cell}.jsonl"
    env = dict(os.environ, OMP_NUM_THREADS="1", PYTHONUNBUFFERED="1")
    env[args.env_var] = cell
    last, stall = -1, 0
    for attempt in range(1, args.max_attempts + 1):
        done = done_ids(final)
        rem = [i for i in ids if i not in done]
        if not rem:
            print(f"[{cell}] COMPLETE {len(done)}/{len(ids)}", flush=True)
            return True
        stall = stall + 1 if len(done) == last else 0
        last = len(done)
        if stall >= args.stall_limit:
            print(f"[{cell}] STALLED ({args.stall_limit} zero-progress attempts) — remaining={rem}", flush=True)
            return False
        print(f"=== [{cell}] attempt {attempt}: {len(done)} done, {len(rem)} remaining ===", flush=True)
        tmp = tempfile.mktemp(suffix=".jsonl")
        cmd = [str(REPO / ".venv/bin/python3"), str(REPO / "eval_run.py"),
               "--item-ids", ",".join(rem), "--output", tmp, "--no-log",
               "--workers", str(args.workers)]
        try:
            rc = subprocess.call(cmd, env=env, timeout=args.attempt_timeout, cwd=str(REPO))
        except subprocess.TimeoutExpired:
            rc = f"TIMEOUT({args.attempt_timeout}s)"
        print(f"[{cell}] attempt {attempt} rc={rc}", flush=True)
        tmp_p = Path(tmp)
        if tmp_p.exists():
            if tmp_p.stat().st_size > 0:
                with open(final, "a", encoding="utf-8") as fo:
                    fo.write(tmp_p.read_text(encoding="utf-8"))
            tmp_p.unlink()
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-var", required=True, help="escape-hatch env var, set to on/off per cell")
    ap.add_argument("--prefix", required=True, help="output prefix: eval_results_<prefix>_{on,off}.jsonl")
    ap.add_argument("--item-ids", default=None, help="comma-separated subset (default: full golden set)")
    ap.add_argument("--golden-set", default="eval_golden_set.json")
    ap.add_argument("--workers", type=int, default=4, help="eval_run --workers (use 1 for Ollama)")
    ap.add_argument("--max-attempts", type=int, default=25)
    ap.add_argument("--stall-limit", type=int, default=6)
    ap.add_argument("--attempt-timeout", type=int, default=2700)
    args = ap.parse_args()

    golden = json.loads((REPO / args.golden_set).read_text(encoding="utf-8"))
    items = golden.get("items", golden) if isinstance(golden, dict) else golden
    ids = [it["id"] for it in items]
    if args.item_ids:
        want = set(args.item_ids.split(","))
        ids = [i for i in ids if i in want]
    print(f"matched pair: {len(ids)} items, env {args.env_var}=on/off, prefix {args.prefix}", flush=True)

    ok_on = run_cell("on", ids, args)
    ok_off = run_cell("off", ids, args)
    print(f"=== ALL DONE  on={ok_on}  off={ok_off} ===", flush=True)
    return 0 if (ok_on and ok_off) else 1


if __name__ == "__main__":
    sys.exit(main())
