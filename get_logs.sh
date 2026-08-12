#!/usr/bin/env bash
# get_logs.sh — read Cloud Run logs for the Maskinrummet service.
#
# The deployed service is `legal-graph-rag` in europe-west1 (see
# whitepapers/gcp_persistence_handover.md). Defaults below match it; override
# any of them via environment variables if you point at another deployment.
#
#   ./get_logs.sh                  # last 50 entries from the past hour
#   ./get_logs.sh -n 200 -s 6h     # last 200 entries from the past 6 hours
#   ./get_logs.sh -e               # errors and worse only
#   ./get_logs.sh -f               # follow (live tail)
#   ./get_logs.sh -r               # raw JSON, for jq
#
set -euo pipefail

SERVICE="${SERVICE:-legal-graph-rag}"
REGION="${REGION:-europe-west1}"
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"

LIMIT=50
FRESHNESS=1h
SEVERITY=""
FOLLOW=0
RAW=0

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \?//'
    exit "${1:-0}"
}

while getopts "n:s:S:efrh" opt; do
    case "$opt" in
        n) LIMIT="$OPTARG" ;;
        s) FRESHNESS="$OPTARG" ;;
        S) SEVERITY="$OPTARG" ;;
        e) SEVERITY="ERROR" ;;
        f) FOLLOW=1 ;;
        r) RAW=1 ;;
        h) usage 0 ;;
        *) usage 2 ;;
    esac
done

if [[ -z "$PROJECT" ]]; then
    echo "get_logs.sh: no GCP project set — pass PROJECT=… or run 'gcloud config set project …'" >&2
    exit 1
fi

# Live tail is a different subcommand entirely: it streams rather than querying,
# so the limit/freshness flags do not apply to it.
if [[ "$FOLLOW" -eq 1 ]]; then
    echo "→ tailing $SERVICE ($REGION, $PROJECT) — Ctrl-C to stop" >&2
    exec gcloud beta run services logs tail "$SERVICE" \
        --project="$PROJECT" \
        --region="$REGION"
fi

FILTER="resource.type=\"cloud_run_revision\"
AND resource.labels.service_name=\"$SERVICE\"
AND resource.labels.location=\"$REGION\""

if [[ -n "$SEVERITY" ]]; then
    FILTER="$FILTER
AND severity>=$SEVERITY"
fi

echo "→ $SERVICE ($REGION, $PROJECT) · last $LIMIT entries · freshness $FRESHNESS${SEVERITY:+ · severity >= $SEVERITY}" >&2

if [[ "$RAW" -eq 1 ]]; then
    FORMAT="json"
else
    # One resource.type covers three payload shapes: uvicorn/print output lands
    # in textPayload, structured logs in jsonPayload.message, and Cloud Run's own
    # audit/system events (failed deploys, missing secrets) in
    # protoPayload.status.message. Emit all three, plus the log stream name, or
    # whole classes of entry render as blank lines.
    FORMAT="value(timestamp.date('%Y-%m-%d %H:%M:%S', tz='LOCAL'), severity, logName.basename(), textPayload, jsonPayload.message, protoPayload.status.message)"
fi

exec gcloud logging read "$FILTER" \
    --project="$PROJECT" \
    --limit="$LIMIT" \
    --freshness="$FRESHNESS" \
    --order=desc \
    --format="$FORMAT"
