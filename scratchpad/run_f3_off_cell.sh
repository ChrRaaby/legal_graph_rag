#!/usr/bin/env bash
# F3 OFF cell — identical to the F2 ON cell except F_SCOPE_GUARD=off.
#
# The ON cell is tonight's eval_results_f2_gemma_v42.jsonl: same model, same
# night, same app.py, separate process. Only cosmetic notes/tags were added to
# two items afterwards (no effect on answers or scoring), so it is a valid
# matched cell and re-running it would burn ~50 min of GPU for nothing.
cd ~/github_repos/legal_kg/legal-legislation-explorer || exit 1

export LLM_PROVIDER=ollama
export OLLAMA_MODEL=gemma4:26b
export PYTHONUNBUFFERED=1
export F_SCOPE_GUARD=off          # the treatment being removed
# SCOPE_CLASSIFIER_MODEL is irrelevant here: the classifier is never called.

OUT=eval_results_f3_gemma_v42_off.jsonl
LOG=/tmp/f3_off.log

echo "start $(date -Is)  guard=OFF agent=$OLLAMA_MODEL" > "$LOG"
curl -s --max-time 600 http://172.21.64.1:11434/api/generate \
  -d '{"model":"gemma4:26b","prompt":"hej","stream":false,"options":{"num_predict":1}}' \
  >/dev/null 2>&1
.venv/bin/python3 eval_run.py --llm ollama --workers 1 --output "$OUT" >> "$LOG" 2>&1
echo "exit=$? end=$(date -Is)" >> "$LOG"
