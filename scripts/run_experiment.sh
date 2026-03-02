#!/bin/bash
# Run a timed paper trading experiment
# Usage: ./scripts/run_experiment.sh <experiment_id> <duration_seconds>

set -e

EXP_ID="${1:?Usage: run_experiment.sh <experiment_id> <duration_seconds>}"
DURATION="${2:-1800}"  # default 30 min

echo "=== Starting Experiment ${EXP_ID} (${DURATION}s) ==="
echo "Start time: $(date)"

# Record pre-experiment trade count
PRE_TRADES=$(sqlite3 polymarket_agent.db "SELECT COUNT(*) FROM trades;")
PRE_SIGNALS=$(sqlite3 polymarket_agent.db "SELECT COUNT(*) FROM signal_log;")
echo "Pre-experiment: ${PRE_TRADES} trades, ${PRE_SIGNALS} signals"

# Start agent
uv run polymarket-agent run > "logs/exp_${EXP_ID}.log" 2>&1 &
AGENT_PID=$!
echo "Agent PID: ${AGENT_PID}"

# Wait for duration
sleep "${DURATION}"

# Stop agent
kill "${AGENT_PID}" 2>/dev/null
wait "${AGENT_PID}" 2>/dev/null || true

echo "=== Experiment ${EXP_ID} Complete ==="
echo "End time: $(date)"

# Post-experiment metrics
POST_TRADES=$(sqlite3 polymarket_agent.db "SELECT COUNT(*) FROM trades;")
POST_SIGNALS=$(sqlite3 polymarket_agent.db "SELECT COUNT(*) FROM signal_log;")
NEW_TRADES=$((POST_TRADES - PRE_TRADES))
NEW_SIGNALS=$((POST_SIGNALS - PRE_SIGNALS))
echo "New trades: ${NEW_TRADES}, New signals: ${NEW_SIGNALS}"

# Strategy breakdown for this period
echo ""
echo "Signal breakdown (last ${DURATION}s):"
sqlite3 polymarket_agent.db "SELECT strategy, COUNT(*) FROM signal_log WHERE timestamp > datetime('now', '-$((DURATION / 60)) minutes') GROUP BY strategy;"

echo ""
echo "Trade breakdown (last ${DURATION}s):"
sqlite3 polymarket_agent.db "SELECT strategy, side, COUNT(*), ROUND(SUM(size), 2) FROM trades WHERE timestamp > datetime('now', '-$((DURATION / 60)) minutes') GROUP BY strategy, side;"

# Error check
echo ""
ERRORS=$(grep -ci "error\|exception\|failed" "logs/exp_${EXP_ID}.log" 2>/dev/null || echo "0")
echo "Errors in log: ${ERRORS}"

# Save evaluation
uv run polymarket-agent evaluate --period 30m > "results/exp_${EXP_ID}.json" 2>&1
echo "Evaluation saved to results/exp_${EXP_ID}.json"

# Human-readable report
echo ""
echo "=== Report ==="
uv run polymarket-agent report --period 30m 2>&1
