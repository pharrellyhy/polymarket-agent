# Polymarket Agent Phase 4 — Live Trading

Date: 2026-02-26

## Summary

Add live order execution via py-clob-client, enforce risk management limits in the Orchestrator, improve database lifecycle management, and fix known issues from Phase 3 code review.

## Scope

1. **LiveTrader** — new Executor implementation wrapping py-clob-client
2. **Executor factory** — Orchestrator selects PaperTrader or LiveTrader based on config mode
3. **Risk gate** — Orchestrator enforces max_position_size, max_daily_loss, max_open_orders before execution
4. **Database context manager** — proper connection cleanup
5. **Subprocess timeout** — prevent CLI hangs
6. **CLI safety** — `--live` flag required for live mode

## Design

### LiveTrader (`execution/live.py`)

Thin wrapper around `ClobClient` implementing `Executor` ABC.

- Initializes ClobClient with private key from `POLYMARKET_PRIVATE_KEY` env var
- Optional `POLYMARKET_FUNDER` for Magic/proxy wallets
- Uses limit orders (GTC) — strategies set target_price, so limit orders are natural
- Signal.size interpreted as USDC amount (consistent with PaperTrader)
- Logs all trades to SQLite via Database
- `get_portfolio()` returns real positions from ClobClient
- `cancel_order()` and `get_open_orders()` delegate to ClobClient
- Gracefully errors if py-clob-client not installed (optional dependency)

### Executor ABC Extension

Add optional methods with default implementations:

```python
def cancel_order(self, order_id: str) -> bool:
    return False

def get_open_orders(self) -> list[dict]:
    return []
```

### Executor Factory in Orchestrator

```python
def _build_executor(config, db) -> Executor:
    if config.mode == "live":
        # require POLYMARKET_PRIVATE_KEY
        return LiveTrader(private_key=..., db=db)
    return PaperTrader(starting_balance=..., db=db)
```

### Risk Gate in Orchestrator

`_check_risk(signal) -> str | None` called for each signal before execution:
- Max position size: reject if signal.size > config.risk.max_position_size
- Max daily loss: reject if cumulative daily loss >= config.risk.max_daily_loss
- Max open orders: reject if open order count >= config.risk.max_open_orders (live mode)

Daily loss calculated from DB trades within current UTC day.

### Database Context Manager

Add `__enter__`/`__exit__` to Database class. Wire cleanup in Orchestrator and CLI.

### Subprocess Timeout

Add timeout parameter (30s default) to `_run_cli()`.

### CLI Safety

`polymarket-agent run --live` required flag. Without it, live mode refuses to start.

## Tasks

1. Add py-clob-client optional dependency
2. Extend Executor ABC with cancel_order/get_open_orders defaults
3. Implement LiveTrader
4. Add executor factory to Orchestrator
5. Add risk gate to Orchestrator
6. Add Database context manager + subprocess timeout
7. Update CLI with --live safety flag
8. Tests for all new code
9. Final verification (full suite, lint, types)

## Dependencies

- `py-clob-client` — optional runtime dependency (`polymarket-agent[live]`)
- All existing deps unchanged
