#!/usr/bin/env bash
# Auto-tune script: evaluates trading performance and invokes an LLM
# to decide whether config.yaml needs adjustments.
#
# Usage:
#   bash scripts/autotune.sh              # default: claude provider, 6h period
#   AUTOTUNE_PROVIDER=openai AUTOTUNE_MODEL=gpt-4o bash scripts/autotune.sh
#
# Environment variables:
#   AUTOTUNE_PROVIDER  — "claude" (default), "openai", or "anthropic"
#   AUTOTUNE_MODEL     — model name (required for openai/anthropic providers)
#   AUTOTUNE_PERIOD    — evaluation period, e.g. "6h", "24h" (default: 6h)
#   AUTOTUNE_BASE_URL  — optional API base URL for OpenAI-compatible endpoints
#   AUTOTUNE_API_KEY_ENV — optional env var name for API key override
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

# Load env from .env if present (for launchd contexts)
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.env"
    set +a
fi

AUTOTUNE_PROVIDER="${AUTOTUNE_PROVIDER:-claude}"
AUTOTUNE_PERIOD="${AUTOTUNE_PERIOD:-6h}"

echo "Provider: $AUTOTUNE_PROVIDER"

if [ "$AUTOTUNE_PROVIDER" = "claude" ]; then
    # --- Claude Code CLI flow (original approach) ---
    if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
        echo "ERROR: ANTHROPIC_API_KEY is not set."
        echo "Set it in the environment, in $PROJECT_DIR/.env, or in the launchd plist EnvironmentVariables."
        exit 1
    fi

    EVAL=$(uv run polymarket-agent evaluate --period "$AUTOTUNE_PERIOD" --json)

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

else
    # --- Direct API flow (openai/anthropic providers) ---
    AUTOTUNE_MODEL="${AUTOTUNE_MODEL:?AUTOTUNE_MODEL must be set for $AUTOTUNE_PROVIDER provider}"

    CMD=(uv run polymarket-agent autotune --period "$AUTOTUNE_PERIOD" --provider "$AUTOTUNE_PROVIDER" --model "$AUTOTUNE_MODEL")

    if [ -n "${AUTOTUNE_BASE_URL:-}" ]; then
        CMD+=(--base-url "$AUTOTUNE_BASE_URL")
    fi

    if [ -n "${AUTOTUNE_API_KEY_ENV:-}" ]; then
        CMD+=(--api-key-env "$AUTOTUNE_API_KEY_ENV")
    fi

    echo "Running: ${CMD[*]}"
    "${CMD[@]}"
fi

echo "=== Auto-tune finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
