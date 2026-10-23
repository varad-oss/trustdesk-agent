#!/usr/bin/env bash
# Run (or resume) an eval in a detached `screen` session that survives closing the terminal or Claude,
# keeps the Mac awake with `caffeinate`, and regenerates docs/EVAL_REPORT.md when the run finishes.
#
#   scripts/eval_detached.sh RUN_ID LEVELS TRIALS      e.g. scripts/eval_detached.sh full-20261023 0,1,2,3 3
#
# Watch:   screen -r trustdesk-eval      (detach again with Ctrl-A then D)
# Log:     tail -f data/eval-RUN_ID.log
# Stop:    screen -S trustdesk-eval -X quit   (finished episodes are kept; rerun this script to resume)
set -euo pipefail
RUN_ID=${1:?run id}
LEVELS=${2:-0,1,2,3}
TRIALS=${3:-3}
cd "$(dirname "$0")/.."
mkdir -p data
LOG="data/eval-${RUN_ID}.log"
if screen -list | grep -q "trustdesk-eval"; then
  echo "A trustdesk-eval screen session is already running (screen -r trustdesk-eval)." >&2
  exit 1
fi
# -i: no idle sleep, -s: no system sleep on AC power. Closing the lid can still sleep the Mac.
screen -dmS trustdesk-eval caffeinate -is bash -c "
  uv run trustdesk eval run --levels '$LEVELS' --trials '$TRIALS' --concurrency 1 --reported --run-id '$RUN_ID' --resume >> '$LOG' 2>&1
  status=\$?
  echo \"eval exited with status \$status at \$(date)\" >> '$LOG'
  if [ \$status -eq 0 ]; then uv run trustdesk eval report --run-id '$RUN_ID' >> '$LOG' 2>&1; fi
"
echo "Started in screen session 'trustdesk-eval'. Log: $LOG"
