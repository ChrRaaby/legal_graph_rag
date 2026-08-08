#!/usr/bin/env bash
# Reproducibility discriminating test (Fable review item 4, user-approved):
# run the OFF config (guard off => NO classifier calls at all) TWICE in one
# night and compare the two runs' answer bytes with each other. If ~47% like
# the ON-vs-OFF pair, the "classifier sharing perturbs Ollama" hypothesis dies;
# if ~84% like the C2 cells, it survives.
cd ~/github_repos/legal_kg/legal-legislation-explorer || exit 1
export LLM_PROVIDER=ollama OLLAMA_MODEL=gemma4:26b PYTHONUNBUFFERED=1 F_SCOPE_GUARD=off
LOG=/tmp/f3_repro.log
echo "start $(date -Is)" > "$LOG"
curl -s --max-time 600 http://172.21.64.1:11434/api/generate \
  -d '{"model":"gemma4:26b","prompt":"hej","stream":false,"options":{"num_predict":1}}' >/dev/null 2>&1
for n in 1 2; do
  echo "--- run $n $(date -Is) ---" >> "$LOG"
  .venv/bin/python3 eval_run.py --llm ollama --workers 1 \
    --output "eval_results_f3_repro_off$n.jsonl" >> "$LOG" 2>&1
  echo "run $n exit=$? $(date -Is)" >> "$LOG"
done
echo "done $(date -Is)" >> "$LOG"
