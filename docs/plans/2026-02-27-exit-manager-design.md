# Exit Manager Design

## Problem

The trading bot gets stuck once positions fill up. Strategies only generate entry (buy) signals. The risk gate rejects buy signals for markets where positions already exist, and no mechanism generates sell signals to close positions. Conditional orders exist but have 50% bands that rarely trigger.

## Solution: Separate Exit Strategy Layer

Add a dedicated `ExitManager` that runs alongside strategies in the orchestrator tick loop. It evaluates held positions against current market data and generates sell signals. Strategies remain entry-only.

## Exit Rules (evaluated in order, first match wins)

1. **Profit target** — sell if current bid >= entry price * 1.15 (15% gain)
2. **Stop loss** — sell if current bid <= entry price * 0.88 (12% loss)
3. **Signal reversal** — sell if the condition that originally triggered the buy no longer holds
   - signal_trader positions: sell if yes_price no longer below midpoint by price_move_threshold
   - arbitrageur positions: sell if price sum deviation back within tolerance
   - unknown strategy: skip reversal, rely on other rules
4. **Stale position** — sell if held longer than max_hold_hours (24h) with no improvement

## Orchestrator Integration

```
fetch markets → run strategies (entries) → run ExitManager (exits) → aggregate entries → execute all
```

- Exit signals bypass the "already holding position" risk gate
- Exit signals skip position sizing (sell full position at current bid)
- Exit signals skip aggregator min_strategies requirement
- Entry signals continue through normal risk/sizing/aggregation pipeline

## Config

```yaml
conditional_orders:
  default_stop_loss_pct: 0.12    # tightened from 0.5
  default_take_profit_pct: 0.15  # tightened from 0.5

exit_manager:
  enabled: true
  profit_target_pct: 0.15
  stop_loss_pct: 0.12
  signal_reversal: true
  max_hold_hours: 24
```

New `ExitManagerConfig` Pydantic model nested under `AppConfig`.

## Position Metadata

Add to PaperTrader position dicts:
- `opened_at`: ISO timestamp of first buy
- `entry_strategy`: strategy name that opened the position

Existing positions recovered from DB without these fields get defaults (opened_at = recovery time, entry_strategy = "unknown").

## Files Changed

| File | Change |
|------|--------|
| `src/polymarket_agent/strategies/exit_manager.py` | New — ExitManager class |
| `src/polymarket_agent/orchestrator.py` | Integrate ExitManager into tick loop |
| `src/polymarket_agent/config.py` | Add ExitManagerConfig model |
| `config.yaml` | Add exit_manager section, tighten conditional orders |
| `src/polymarket_agent/execution/paper.py` | Add opened_at/entry_strategy to positions |
| `tests/test_exit_manager.py` | New — unit tests for all exit rules |
