#!/usr/bin/env bash
# Auto-tune script: evaluates trading performance and invokes Claude Code
# to decide whether config.yaml needs adjustments.
#
# Usage:
#   bash scripts/autotune.sh              # default 6h period
#   AUTOTUNE_PERIOD=24h bash scripts/autotune.sh
#
# Install as launchd job (runs every 6 hours):
#   cp scripts/com.polymarket-agent.autotune.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.polymarket-agent.autotune.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs/autotune"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$LOG_DIR/autotune-$TIMESTAMP.log"

mkdir -p "$LOG_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Auto-tune started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

cd "$PROJECT_DIR"

EVAL=$(uv run polymarket-agent evaluate --period "${AUTOTUNE_PERIOD:-6h}" --json)

if [ -z "$EVAL" ]; then
    echo "ERROR: evaluate returned empty output"
    exit 1
fi

echo "Evaluation complete. Invoking Claude Code for analysis..."

claude -p "You are an auto-tuning agent for a Polymarket trading bot.

Analyze this evaluation output and decide whether to edit config.yaml to improve performance.

RULES:
1. NEVER change the 'mode' field — the trading loop will reject mode changes.
2. Adjust at most 2-3 parameters per tuning session to avoid instability.
3. Respect the min/max ranges in tunable_parameters.
4. If performance is acceptable (positive return, Sharpe > 0.5, win rate > 45%), make no changes.
5. If you make changes, add a YAML comment with the date and reason.
6. Only edit the config file at the path specified in config_file_path.

EVALUATION DATA:
$EVAL" \
    --allowedTools "Read,Edit,Write" \
    --max-turns 10

echo "=== Auto-tune finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
