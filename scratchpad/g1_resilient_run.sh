#!/usr/bin/env bash
# Resilient driver for a full eval run.
#
# Why this exists: a long run on the Gemini API intermittently parks forever —
# 0% CPU, all threads sleeping in futex_do_wait, HTTPS socket to Google in
# CLOSE-WAIT. The peer goes away and the client never notices. Setting
# timeout=/max_retries= on ChatGoogleGenerativeAI did NOT fix it (the transport
# does not honour it), so the only reliable cure is an external watchdog.
#
# It also works around eval_run.py truncating its --output at start (line 638):
# each round writes a fresh part file, and the parts are concatenated at the end.
#
# Usage: g1_resilient_run.sh <final-basename> <tag> [extra eval_run args...]
#   e.g. g1_resilient_run.sh eval_results_g1_flash36_v42.jsonl flash36
#        g1_resilient_run.sh eval_results_g1_gemma26b_v42.jsonl gemma26b --llm ollama
set -u
cd ~/github_repos/legal_kg/legal-legislation-explorer || exit 1

FINAL_NAME="$1"; TAG="$2"; shift 2
EXTRA=("$@")

SCRATCH=/mnt/c/Users/CHRIS/AppData/Local/Temp/claude/C--Users-CHRIS-cluade-code-projects/de2e912e-4625-4dd1-b667-29adc786bfd6/scratchpad
LOG="$SCRATCH/g1_${TAG}.log"
PARTS_DIR="eval_history/.parts_${TAG}"
STALL_SECS=${STALL_SECS:-420}     # no new record for this long -> assume hung
# Startup (torch + e5-large + Aura connect, with its own internal retry) can take
# several minutes before the first record appears. Without a separate grace the
# watchdog kills healthy rounds that simply have not finished booting.
STARTUP_GRACE=${STARTUP_GRACE:-900}
MAX_ROUNDS=${MAX_ROUNDS:-12}

RUN_ID=$(date -u +%H%M%S)
mkdir -p "$PARTS_DIR"
: > "$LOG"

missing_ids() {
    python3 - "$PARTS_DIR" <<'PY'
import glob, json, os, sys
parts = sys.argv[1]
done = set()
for f in glob.glob(os.path.join(parts, "*.jsonl")):
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                done.add(json.loads(line)["item"]["id"])
            except Exception:
                pass
gold = json.load(open("eval_golden_set.json", encoding="utf-8"))
missing = [i["id"] for i in gold["items"] if i["id"] not in done]
print(",".join(missing))
PY
}

for round in $(seq 1 "$MAX_ROUNDS"); do
    MISS=$(missing_ids)
    if [ -z "$MISS" ]; then
        echo "== all items collected after $((round-1)) round(s) ==" >>"$LOG"
        break
    fi
    COUNT=$(awk -F, '{print NF}' <<<"$MISS")
    # Must keep the eval_history/ prefix: eval_artifact_path() basenames any
    # other relative form, which would silently drop the subdirectory and write
    # the part somewhere the watchdog is not looking.
    #
    # $RUN_ID in the name is not cosmetic: eval_run.py truncates its --output at
    # start, so a second invocation whose round numbering restarts at 1 would
    # reopen — and wipe — the parts the first invocation had already collected.
    # That happened once and cost 11 records.
    PART="${PARTS_DIR}/part_${RUN_ID}_${round}.jsonl"
    echo "== round $round · $COUNT item(s) left · $(date -u +%H:%M:%S) ==" >>"$LOG"

    PYTHONUNBUFFERED=1 .venv/bin/python3 eval_run.py \
        --item-ids "$MISS" --output "$PART" --workers 1 "${EXTRA[@]}" >>"$LOG" 2>&1 &
    RUN_PID=$!

    # Watchdog: kill the round if the part file stops growing.
    PARTFILE="$PART"
    LAST=0; IDLE=0
    while kill -0 "$RUN_PID" 2>/dev/null; do
        sleep 30
        NOW=$(wc -l < "$PARTFILE" 2>/dev/null || echo 0)
        if [ "$NOW" -gt "$LAST" ]; then LAST=$NOW; IDLE=0; else IDLE=$((IDLE + 30)); fi
        # Before the first record the process is still booting, so allow more.
        LIMIT=$STALL_SECS
        [ "$LAST" -eq 0 ] && LIMIT=$STARTUP_GRACE
        if [ "$IDLE" -ge "$LIMIT" ]; then
            echo "!! stalled ${IDLE}s at $NOW records — killing round $round" >>"$LOG"
            kill -9 "$RUN_PID" 2>/dev/null
            pkill -9 -f "eval_run.py --item-ids" 2>/dev/null
            break
        fi
    done
    wait "$RUN_PID" 2>/dev/null
    sleep 5
done

# Stitch the parts into the final artefact, newest record per id winning.
python3 - "$PARTS_DIR" "eval_history/$FINAL_NAME" <<'PY'
import glob, json, os, sys
parts, final = sys.argv[1], sys.argv[2]
best = {}
for f in sorted(glob.glob(os.path.join(parts, "*.jsonl"))):
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        best[rec["item"]["id"]] = line
gold = json.load(open("eval_golden_set.json", encoding="utf-8"))
order = [i["id"] for i in gold["items"]]
with open(final, "w", encoding="utf-8") as out:
    for i in order:
        if i in best:
            out.write(best[i] + "\n")
print(f"wrote {sum(1 for i in order if i in best)}/{len(order)} records -> {final}")
PY
tail -1 "$LOG"
