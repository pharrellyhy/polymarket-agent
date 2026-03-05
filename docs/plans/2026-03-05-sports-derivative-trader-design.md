# SportsDerivativeTrader Strategy Design

## Overview

Trades derivative sports prediction markets (series winner, championship, MVP)
by exploiting cross-market cascade lags, bracket sum mispricing, and hierarchy
inconsistencies. Does NOT trade individual game markets.

## Real Edges

1. **Cross-market cascade lag** — game resolves, derivative markets slow to update
2. **Bracket sum mispricing** — multi-outcome probabilities don't sum to 1.0
3. **Hierarchy inconsistency** — championship price > series price (impossible)

## Architecture

`SportsDerivativeTrader` strategy in `strategies/sports_derivative_trader.py`.
Follows `DateCurveTrader` pattern (LLM client, cached detection, news wiring).

## Files

- `src/polymarket_agent/strategies/sports_derivative_trader.py` — new strategy
- `src/polymarket_agent/data/models.py` — add `SportsMarketNode`, `SportsEventGraph`
- `src/polymarket_agent/orchestrator.py` — register + wire news
- `config.yaml` — add config, remove sports from excluded
- `tests/test_sports_derivative_trader.py` — tests

See user message for full plan details.
