# Plan: Focused Trading on Specific Events

## Context

The agent trades on ALL active markets (up to 50). This plan adds the ability to focus on specific events via config, plus a `research` CLI command for deep bracket market analysis.

## Changes

1. **FocusConfig** (`config.py`) — new Pydantic model with `enabled`, `search_queries`, `market_ids`, `market_slugs`. Added to `AppConfig`. OR-logic filter.

2. **Focus filter** (`orchestrator.py`) — `_apply_focus_filter()` applied in `tick()` and `generate_signals()`. Returns markets unchanged when disabled.

3. **Bracket context** (`ai_analyst.py`) — `_evaluate()` accepts `all_markets`, detects sibling brackets by shared 30-char question prefix, appends bracket distribution table to LLM prompt.

4. **`research` command** (`cli.py`) — interactive event picker with bracket grouping, displays price/volume/liquidity table, technical indicators, and AI probability estimates.

5. **config.yaml** — `focus.enabled: true`, `search_queries: ["Elon Musk"]`

## Files Modified

- `src/polymarket_agent/config.py`
- `src/polymarket_agent/orchestrator.py`
- `src/polymarket_agent/strategies/ai_analyst.py`
- `src/polymarket_agent/cli.py`
- `config.yaml`
