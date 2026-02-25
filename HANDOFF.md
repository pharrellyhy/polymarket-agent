# Polymarket Agent — Handoff Document

Last updated: 2026-02-25

---

## Project Summary

**Polymarket Agent** is a Python auto-trading pipeline for Polymarket prediction markets. It wraps the official `polymarket` CLI (v0.1.4, installed via Homebrew) into a structured system with pluggable trading strategies, paper/live execution, and planned MCP server integration for AI agents.

Inspired by Karpathy's "Build. For. Agents." thesis — CLIs are agent-native interfaces.

## Current State: Phase 1 COMPLETE

- 14 commits, 32 tests passing, ruff lint clean
- 5 mypy strict-mode errors remain (documented below, fix planned as Phase 2 Task 1)
- Code reviewed and simplified (DRY helpers extracted, boilerplate reduced)
- Live smoke test verified: `uv run polymarket-agent tick` fetches 50 markets, generates signals, executes paper trades

## Architecture

```
CLI (Typer) → Orchestrator → Data Layer (CLI wrapper + cache)
                           → Strategy Engine (pluggable ABCs)
                           → Execution Layer (Paper/Live)
                           → SQLite (trade logging)
```

## File Map

| File | Purpose | Lines |
|------|---------|-------|
| `src/polymarket_agent/cli.py` | Typer CLI: `run`, `status`, `tick` commands | ~80 |
| `src/polymarket_agent/orchestrator.py` | Main loop: fetch → analyze → execute | ~96 |
| `src/polymarket_agent/config.py` | Pydantic config from YAML | ~35 |
| `src/polymarket_agent/data/models.py` | Market, Event, OrderBook, PricePoint (Pydantic) | ~186 |
| `src/polymarket_agent/data/client.py` | CLI wrapper with TTL caching | ~107 |
| `src/polymarket_agent/data/cache.py` | In-memory TTL cache | 31 |
| `src/polymarket_agent/db.py` | SQLite trade logging | ~68 |
| `src/polymarket_agent/strategies/base.py` | Strategy ABC + Signal dataclass | 38 |
| `src/polymarket_agent/strategies/signal_trader.py` | Volume-filtered directional signals | ~80 |
| `src/polymarket_agent/execution/base.py` | Executor ABC + Portfolio + Order | ~55 |
| `src/polymarket_agent/execution/paper.py` | Paper trading with virtual USDC | ~140 |
| `config.yaml` | Default config (paper mode, $1000) | 15 |
| `pyproject.toml` | Project config (deps, ruff, mypy) | 43 |

## Key Design Decisions

1. **All data comes from `polymarket` CLI** — subprocess with `-o json`, parsed through `PolymarketData._run_cli()`. Never call subprocess directly elsewhere.
2. **Null-safe parsing** — Polymarket CLI returns `null` for optional fields. Use `data.get("field") or ""` pattern (not `data.get("field", "")`). Helper functions `_str_field()`, `_float_field()`, `_parse_json_field()` in models.py.
3. **Strategy ABC** — All strategies implement `analyze(markets, data) -> list[Signal]`. Register in `STRATEGY_REGISTRY` dict in `orchestrator.py`.
4. **Executor ABC** — `place_order(signal) -> Order | None`. Paper and Live share interface.
5. **Signal dataclass** — `strategy`, `market_id`, `token_id`, `side` (buy/sell), `confidence`, `target_price`, `size` (USDC), `reason`.

## Known Issues from Code Review

### Critical (fix in Phase 2)
1. **mypy strict fails** — 5 errors: missing `types-PyYAML` stub, wrong `type: ignore` in client.py, `str` vs `Literal` in signal_trader.py, `Any` return in execution/base.py. **Fix: Phase 2 Task 1.**
2. **OrderBook.midpoint/spread** — Returns 0.0 or negative when asks/bids empty. Should guard against missing sides.
3. **Risk config not enforced** — `RiskConfig` loaded but never checked. No position-size, daily-loss, or open-order limits.
4. **Signal.size semantics** — Ambiguous whether dollars or shares. Buy uses as cost, sell divides by price to get shares. Works algebraically but could confuse strategy authors.
5. **Database connection never closed** — No context manager, no cleanup on KeyboardInterrupt.

### Important
6. **No subprocess timeout** — `_run_cli()` can hang indefinitely if CLI blocks.
7. **Empty token_id signals** — SignalTrader emits `""` token_id when `clob_token_ids` missing.
8. **No sell-side test coverage** — Paper trader sell path untested.
9. **Config path relative to CWD** — Silently falls back to defaults if config.yaml not found.

## How to Work With This Codebase

### Setup
```bash
cd /Users/pharrelly/codebase/github/brainstorming
uv sync
```

### Run Tests
```bash
uv run pytest tests/ -v                    # all 32 tests
uv run pytest tests/ -v --cov=src/polymarket_agent  # with coverage
```

### Lint & Type Check
```bash
uv run ruff check src/                     # lint (currently clean)
uv run ruff format src/                    # format
uv run mypy src/                           # type check (5 errors, fix in Phase 2)
```

### Smoke Test
```bash
uv run polymarket-agent tick               # single trading cycle with live data
uv run polymarket-agent status             # portfolio view
uv run polymarket-agent run                # continuous loop (Ctrl+C to stop)
```

### Adding a New Strategy
1. Create `src/polymarket_agent/strategies/<name>.py` implementing `Strategy` ABC
2. Add to `STRATEGY_REGISTRY` in `orchestrator.py`
3. Add config block in `config.yaml` under `strategies:`
4. Write tests in `tests/test_<name>.py`

### Mocking CLI in Tests
```python
def _mock_run(args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=MOCK_JSON, stderr="")

mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
```

## Phase Plan

| Phase | Status | Description |
|-------|--------|-------------|
| **1: Data + Paper Trading** | COMPLETE | CLI wrapper, models, cache, paper executor, SignalTrader, orchestrator, CLI |
| **2: Strategy Modules** | NEXT | MarketMaker, Arbitrageur, AIAnalyst, signal aggregation, mypy fixes |
| **3: MCP Server** | Planned | Expose tools for Claude (search_markets, place_trade, etc.) |
| **4: Live Trading** | Planned | py-clob-client, wallet setup, real order execution |

## Phase 2 Implementation Plan

See `docs/plans/2026-02-25-polymarket-agent-phase2.md` for detailed TDD plan with 8 tasks:

1. Fix mypy errors from Phase 1
2. MarketMaker strategy (bid/ask around midpoint)
3. Arbitrageur strategy (price-sum deviation detection)
4. AIAnalyst strategy (Claude probability estimates)
5. Signal aggregation (dedup, confidence filtering, multi-strategy consensus)
6. Config updates for new strategies
7. Integration test with all strategies
8. Final verification

## Code Simplifications Applied

The code simplifier made the following changes (all tests still pass):

1. **models.py** — Extracted `_parse_json_field()`, `_str_field()`, `_float_field()` helpers, eliminating ~15 lines of repetitive parsing in `Market.from_cli()` and `Event.from_cli()`
2. **client.py** — Removed redundant `cache` parameter from `_run_cli_cached()` (always `self._cache`)
3. **signal_trader.py** — Replaced loop-with-append with walrus-operator comprehension
4. **db.py** — Used `dataclasses.astuple(trade)` in `record_trade()`, unified SQL query builder in `get_trades()`
5. **paper.py** — Removed unreachable `return None` after exhaustive buy/sell branches
6. **base.py** — Extracted `Portfolio._position_value()` static method
7. **orchestrator.py** — Inlined variables, used `sum()` generator for trade counting
8. **cli.py** — Extracted `_build_orchestrator()` helper, removed duplicate setup
9. **config.py** — Used `Path.read_text()` instead of `with open()`
10. **Tests** — Extracted pytest fixtures in test_db.py, test_paper_trader.py, test_config.py

## Dependencies

**Runtime:** pydantic>=2.0, pyyaml>=6.0, typer>=0.9
**Dev:** pytest>=8.0, pytest-mock>=3.0, ruff>=0.4, mypy>=1.10
**External:** `polymarket` CLI (Homebrew: `brew install polymarket`)
**Phase 2 adds:** anthropic SDK (for AIAnalyst)

## Design Documents

- `docs/plans/2026-02-25-polymarket-agent-trader-design.md` — Full system design (architecture, all phases)
- `docs/plans/2026-02-25-polymarket-agent-phase1.md` — Phase 1 implementation plan (COMPLETE)
- `docs/plans/2026-02-25-polymarket-agent-phase2.md` — Phase 2 implementation plan (NEXT)

## Git Log (Phase 1)

```
133b1e5 fix: handle null values in CLI JSON for optional Market/Event fields
460d1da feat: add CLI with run, status, and tick commands
b89112b feat: add orchestrator loop
906d044 feat: add paper trading executor
60a92b3 feat: add SignalTrader strategy
da7cb4f feat: add Strategy ABC and Signal dataclass
6e5e801 feat: add SQLite database for trade logging
3c1ed05 feat: add PolymarketData CLI wrapper client
80ff6d7 feat: add config loading with Pydantic validation
3725634 feat: add TTL cache for data layer
c33d616 feat: add Pydantic models for Market, Event, OrderBook
90fcae0 feat: scaffold polymarket-agent project
fc01e2b docs: add CLAUDE.md and Phase 1 implementation plan
c999a6a Add design doc for Polymarket agent trader pipeline
```

## Verification Commands

```bash
# Everything should pass
uv run pytest tests/ -v           # 32 tests passing
uv run ruff check src/            # All checks passed
uv run polymarket-agent tick      # Fetches live data, paper trades

# Known failures (fix in Phase 2)
uv run mypy src/                  # 5 errors (documented above)
```
