"""Generalized diff-first matched-pair judge with a persistent verdict cache.

Protocol (§2, mandatory since C2): byte-diff the ON/OFF cells per item and judge
ONLY the differing answers — identical pairs cannot carry a treatment effect and
judging them injects measured ~14% flip noise. Verdicts are cached in
judge_cache.jsonl keyed on (item_id, answer-hash, judge_model): re-running an
experiment, or a new experiment whose cells reproduce previously judged answers,
costs zero new judge calls for those items.

Usage:
  .venv/bin/python3 ab_judge.py --prefix c5_flash                 # pro judge (gate)
  JUDGE_MODEL=gemini-3.5-flash .venv/bin/python3 ab_judge.py --prefix c5_sent
      # cheap direction-read during iteration; use pro for the merge gate
"""
import argparse
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent
CACHE = REPO / "judge_cache.jsonl"


def _key(item_id: str, answer: str, judge_model: str) -> str:
    h = hashlib.sha256(answer.strip().encode("utf-8")).hexdigest()[:16]
    return f"{item_id}|{h}|{judge_model}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True, help="reads eval_results_<prefix>_{on,off}.jsonl")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    judge_model = os.getenv("JUDGE_MODEL", "gemini-3.1-pro-preview")
    os.environ["JUDGE_MODEL"] = judge_model

    sys.path.insert(0, str(REPO))
    os.chdir(REPO)
    import eval_run

    def load(cell):
        # eval artefacts live in eval_history/ since 2026-08-08; fall back to the
        # repo root so older cells still judge.
        fname = f"eval_results_{args.prefix}_{cell}.jsonl"
        p = REPO / "eval_history" / fname
        if not p.is_file():
            p = REPO / fname
        return {json.loads(l)["item"]["id"]: json.loads(l)
                for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}

    on, off = load("on"), load("off")
    common = sorted(set(on) & set(off))
    same = [i for i in common if on[i]["answer"].strip() == off[i]["answer"].strip()]
    diff = [i for i in common if i not in set(same)]
    print(f"cells: {len(common)} paired items | identical: {len(same)} (not judged) | differing: {len(diff)}")

    cache = {}
    if CACHE.exists():
        for l in CACHE.read_text(encoding="utf-8").splitlines():
            if l.strip():
                v = json.loads(l)
                cache[v["key"]] = v["judge_pass"]

    lock = threading.Lock()
    judge = eval_run.build_judge_llm()
    verdicts = {}  # (cell, id) -> judge_pass
    tasks = []
    hits = 0
    for cell, recs in (("on", on), ("off", off)):
        for i in diff:
            k = _key(i, recs[i]["answer"], judge_model)
            if k in cache:
                verdicts[(cell, i)] = cache[k]
                hits += 1
            else:
                tasks.append((cell, recs[i], k))
    print(f"judge calls needed: {len(tasks)}  (cache hits: {hits}, model: {judge_model})", flush=True)

    def work(cell, rec, k):
        v = eval_run.llm_judge(rec["item"], rec["answer"], judge)
        jp = v.get("judge_pass")
        if jp is not None:
            with lock:
                with open(CACHE, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": k, "id": rec["item"]["id"], "judge_pass": jp,
                                        "judge_model": judge_model,
                                        "begrundelse": v.get("begrundelse", "")},
                                       ensure_ascii=False) + "\n")
                cache[k] = jp
                verdicts[(cell, rec["item"]["id"])] = jp
        return cell, rec["item"]["id"], jp

    errors = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, c, r, k) for c, r, k in tasks]
        for n, fut in enumerate(as_completed(futs), 1):
            c, iid, jp = fut.result()
            if jp is None:
                errors += 1
            print(f"  [{n}/{len(tasks)}] {c:3} {iid} judge_pass={jp}", flush=True)

    on_p = sum(1 for i in diff if verdicts.get(("on", i)) is True)
    off_p = sum(1 for i in diff if verdicts.get(("off", i)) is True)
    print(f"\n============ DIFF-FIRST JUDGE ({judge_model}) — {args.prefix} ============")
    print(f"  footprint       : {len(diff)}/{len(common)} answers differ")
    print(f"  ON  on footprint: {on_p}/{len(diff)}")
    print(f"  OFF on footprint: {off_p}/{len(diff)}")
    print(f"  TREATMENT DELTA : {on_p - off_p:+d}   (judge errors: {errors}, cache hits: {hits})")
    print("\n  per-item flips:")
    nf = 0
    for i in diff:
        o, f = verdicts.get(("on", i)), verdicts.get(("off", i))
        if o != f:
            nf += 1
            print(f"    {i}: OFF={f} ON={o}   {'↑ ON gained' if o and not f else '↓ ON lost'}")
    if not nf:
        print("    (none — judge-flat on the footprint)")
    for cell, d in (("ON", on), ("OFF", off)):
        det = sum(1 for r in d.values() if r["scores"].get("overall_pass"))
        print(f"  det {cell}: {det}/{len(d)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
