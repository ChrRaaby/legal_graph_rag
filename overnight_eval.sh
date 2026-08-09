#!/bin/bash
# Robust overnight multi-run eval: retries if the Neo4j Aura instance wedges
# build_runtime() at startup (no item completes within the grace window).
cd /home/maskinen/github_repos/legal_kg/legal-legislation-explorer
LOG=eval_multirun.log
OUT=eval_results_multirun.jsonl
for attempt in 1 2 3 4 5 6; do
  echo "=== launcher attempt $attempt $(date) ===" >> overnight_eval_launcher.log
  .venv/bin/python3 eval_run.py --repeat 10 --output "$OUT" --no-log > "$LOG" 2>&1 &
  PID=$!
  start=$(date +%s)
  staged=0
  while ps -p $PID >/dev/null 2>&1; do
    done=$(grep -cE '✓|✗' "$LOG" 2>/dev/null)
    if [ "$done" -ge 1 ]; then staged=1; fi
    if [ "$staged" -eq 0 ] && [ $(( $(date +%s) - start )) -gt 360 ]; then
      echo "  stalled at startup (0 items in 360s) — killing, retry" >> overnight_eval_launcher.log
      kill $PID 2>/dev/null; sleep 5; break
    fi
    sleep 15
  done
  # if process exited on its own, check for completion
  if ! ps -p $PID >/dev/null 2>&1; then
    if grep -q "MULTIRUN SUMMARY" "$LOG" 2>/dev/null; then
      echo "  COMPLETE on attempt $attempt $(date)" >> overnight_eval_launcher.log
      exit 0
    fi
    # exited without summary and wasn't a startup stall we triggered → past startup but died mid-run; stop retrying to avoid clobbering partial data
    if [ "$staged" -eq 1 ]; then
      echo "  died mid-run after staging — not retrying" >> overnight_eval_launcher.log
      exit 1
    fi
  fi
done
echo "  gave up after retries $(date)" >> overnight_eval_launcher.log
exit 1
