#!/usr/bin/env bash
# Full v4.2 golden set (69 items) on the local 4090.
#   agent      = gemma4:26b via Ollama
#   classifier = gemma4:26b via Ollama (SAME model, so it stays resident in VRAM;
#                a second model would force a swap on every gated turn)
# workers=1 per the backlog's Ollama rule; PYTHONUNBUFFERED per the traps index
# (python block-buffers stdout to a file, which makes progress checks look dead).
cd ~/github_repos/legal_kg/legal-legislation-explorer || exit 1

export LLM_PROVIDER=ollama
export OLLAMA_MODEL=gemma4:26b
export SCOPE_CLASSIFIER_MODEL=ollama:gemma4:26b
export PYTHONUNBUFFERED=1
export F_SCOPE_GUARD=on

OUT=eval_results_f2_gemma_v42.jsonl
LOG=/tmp/f2_gemma_full.log

echo "start $(date -Is)" > "$LOG"
echo "agent=$OLLAMA_MODEL classifier=$SCOPE_CLASSIFIER_MODEL guard=$F_SCOPE_GUARD" >> "$LOG"

# warm the model so the first item does not pay the load
curl -s --max-time 600 http://172.21.64.1:11434/api/generate \
  -d '{"model":"gemma4:26b","prompt":"hej","stream":false,"options":{"num_predict":1}}' \
  >/dev/null 2>&1

.venv/bin/python3 eval_run.py --llm ollama --workers 1 --output "$OUT" >> "$LOG" 2>&1
echo "exit=$? end=$(date -Is)" >> "$LOG"
