# Polymarket Agent — Handoff Document

Last updated: 2026-02-25

---

## Session Entry — 2026-02-25 (Phase 2 Batch 1: mypy fixes + MarketMaker + Arbitrageur)

### Problem
- Phase 1 left 5 mypy strict-mode errors (missing stubs, wrong type annotations, `Any` returns).
- Phase 2 plan calls for 3 new strategies (MarketMaker, Arbitrageur, AIAnalyst), signal aggregation, and config updates. This session implements the first batch: mypy fixes + 2 strategies.

### Solution
- **Task 1 — mypy fixes:** Added `types-PyYAML` dev dep, replaced `type: ignore` in client.py with `assert isinstance`, changed `side: str` to `side: Literal["buy", "sell"]` in signal_trader.py, added `float()` casts in execution/base.py, fixed `no-any-return` in models.py `_parse_json_field()`. Result: `mypy src/` → `Success: no issues found`.
- **Task 2 — MarketMaker:** New strategy that fetches the order book for each liquid, active market and emits a buy signal below midpoint and a sell signal above midpoint, separated by configurable spread. Includes `_clamp()` utility, `_quote_market()` helper, `_signal()` closure for DRY signal construction. Guards against one-sided orderbooks and uses `except RuntimeError` (not bare `Exception`).
- **Task 3 — Arbitrageur:** New strategy that checks whether complementary outcome prices sum to ~1.0. If the sum deviates beyond a configurable tolerance, buys the underpriced side or sells the overpriced side. Uses walrus-operator comprehension matching the SignalTrader pattern.
- **Code review + simplification pass:** Both strategies were reviewed and simplified. Key fixes: narrowed exception handling, added one-sided book guard, extracted helpers, tightened test assertions, fixed isort ordering.

### Edits
- `pyproject.toml` — added `types-PyYAML` to dev deps
- `src/polymarket_agent/data/client.py` — replaced `type: ignore` with `assert isinstance`
- `src/polymarket_agent/data/models.py` — fixed `no-any-return` in `_parse_json_field()`
- `src/polymarket_agent/strategies/signal_trader.py` — `str` → `Literal["buy", "sell"]`
- `src/polymarket_agent/execution/base.py` — `float()` casts in `_position_value()`
- `src/polymarket_agent/strategies/market_maker.py` — **NEW** (MarketMaker strategy)
- `src/polymarket_agent/strategies/arbitrageur.py` — **NEW** (Arbitrageur strategy)
- `src/polymarket_agent/orchestrator.py` — added MarketMaker + Arbitrageur to `STRATEGY_REGISTRY`, fixed isort
- `tests/test_market_maker.py` — **NEW** (4 tests)
- `tests/test_arbitrageur.py` — **NEW** (4 tests)

### NOT Changed
- No changes to data layer, execution layer, or existing strategy logic.
- No config.yaml updates yet (Task 6 pending).
- No signal aggregation yet (Task 5 pending).
- AIAnalyst not yet implemented (Task 4 pending).
- Pre-existing ruff isort issues in test_cli.py, test_client.py, test_db.py left as-is.

### What Remains (Phase 2 Tasks 4–8)
- **Task 4:** AIAnalyst strategy (Claude probability estimates, anthropic SDK dep)
- **Task 5:** Signal aggregation (dedup, confidence filtering, min_strategies consensus)
- **Task 6:** Config updates (AggregationConfig, config.yaml entries for new strategies)
- **Task 7:** Integration test (full pipeline with all strategies, mocked CLI)
- **Task 8:** Final verification (full test suite, ruff, mypy, smoke test)

See `docs/plans/2026-02-25-polymarket-agent-phase2.md` for detailed implementation steps.

### Verification
```bash
uv run mypy src/                  # Success: no issues found in 17 source files
uv run ruff check src/            # All checks passed
uv run pytest tests/ -v           # 42 passed
```

### Branch
- Working branch: `phase2/strategy-modules` (4 commits ahead of `main`)
- Commits:
  - `dbf065b` fix: resolve mypy type errors from Phase 1
  - `2b8e9b5` feat: add MarketMaker strategy
  - `556654d` feat: add Arbitrageur strategy
  - `79f280b` refactor: apply code review and simplification to batch 1

---

## Session Entry — 2026-02-25 (Execution Regression Fixes)

### Problem
- Refactor commit `d4c715e` introduced two behavior regressions:
- `Portfolio.total_value` treated `current_price=0.0` as missing and fell back to `avg_price`, overstating portfolio value.
- `PaperTrader.place_order()` routed any non-`"buy"` signal side to sell execution, so malformed sides (for example `"hold"`) could execute unintended sells.
- Additional review of other recent local commits (`AGENTS.md`, `docs/plans/2026-02-25-polymarket-agent-phase2.md`) found no code-impacting issues.
- `HANDOFF.md` had a setup path typo (`.../github/brainstorming`).

### Solution
- Added targeted regression tests in `tests/test_paper_trader.py` for zero-price valuation and invalid signal side handling.
- Restored explicit side validation in `PaperTrader.place_order()` and log/skip unsupported sides.
- Updated portfolio valuation helper to preserve valid `0.0` prices.
- Corrected the setup path in this document.

### Edits
- `src/polymarket_agent/execution/base.py`
- `src/polymarket_agent/execution/paper.py`
- `tests/test_paper_trader.py`
- `HANDOFF.md`

### NOT Changed
- No strategy logic changes.
- No Polymarket CLI wrapper/data parsing changes.
- No mypy Phase 2 fixes (still pending).

### Verification
- `uv run python -m ruff check src/polymarket_agent/execution/base.py src/polymarket_agent/execution/paper.py tests/test_paper_trader.py` (passes)
- `uv run python -m pytest tests/test_paper_trader.py -q` (passes: 6)
- `uv run python -m pytest tests/test_db.py -q` (passes: 3)
- Red step verified first: both new tests failed before patching, then passed after patch.

---

## Project Summary

**Polymarket Agent** is a Python auto-trading pipeline for Polymarket prediction markets. It wraps the official `polymarket` CLI (v0.1.4, installed via Homebrew) into a structured system with pluggable trading strategies, paper/live execution, and planned MCP server integration for AI agents.

Inspired by Karpathy's "Build. For. Agents." thesis — CLIs are agent-native interfaces.

## Current State: Phase 2 IN PROGRESS (Batch 1 complete)

- Branch: `phase2/strategy-modules` (4 commits ahead of `main`)
- 42 tests passing, ruff lint clean, mypy strict clean
- MarketMaker + Arbitrageur strategies implemented, reviewed, simplified
- Tasks 4–8 remain (AIAnalyst, signal aggregation, config, integration test, verification)

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
| `src/polymarket_agent/orchestrator.py` | Main loop: fetch → analyze → execute | ~100 |
| `src/polymarket_agent/config.py` | Pydantic config from YAML | ~35 |
| `src/polymarket_agent/data/models.py` | Market, Event, OrderBook, PricePoint (Pydantic) | ~186 |
| `src/polymarket_agent/data/client.py` | CLI wrapper with TTL caching | ~107 |
| `src/polymarket_agent/data/cache.py` | In-memory TTL cache | 31 |
| `src/polymarket_agent/db.py` | SQLite trade logging | ~68 |
| `src/polymarket_agent/strategies/base.py` | Strategy ABC + Signal dataclass | 38 |
| `src/polymarket_agent/strategies/signal_trader.py` | Volume-filtered directional signals | ~80 |
| `src/polymarket_agent/strategies/market_maker.py` | Bid/ask around orderbook midpoint | ~92 |
| `src/polymarket_agent/strategies/arbitrageur.py` | Price-sum deviation detection | ~75 |
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
1. ~~**mypy strict fails**~~ — FIXED in Task 1.
2. **OrderBook.midpoint/spread** — Returns 0.0 or negative when asks/bids empty. MarketMaker now guards against one-sided books, but the underlying model still allows it.
3. **Risk config not enforced** — `RiskConfig` loaded but never checked. No position-size, daily-loss, or open-order limits.
4. **Signal.size semantics** — Ambiguous whether dollars or shares. Buy uses as cost, sell divides by price to get shares.
5. **Database connection never closed** — No context manager, no cleanup on KeyboardInterrupt.

### Important
6. **No subprocess timeout** — `_run_cli()` can hang indefinitely if CLI blocks.
7. **Empty token_id signals** — SignalTrader emits `""` token_id when `clob_token_ids` missing.
8. **No sell-side test coverage** — Paper trader sell path untested.
9. **Config path relative to CWD** — Silently falls back to defaults if config.yaml not found.

### From Phase 2 Code Review
10. **MarketMaker `_max_inventory` not enforced** — Parameter is configured but never checked against portfolio state.
11. **Arbitrageur `_min_deviation` not used** — Loaded from config but not referenced in `_check_price_sum()`. Reserved for future per-outcome deviation filtering.

## How to Work With This Codebase

### Setup
```bash
cd /Users/pharrelly/codebase/github/polymarket-agent
uv sync
```

### Run Tests
```bash
uv run pytest tests/ -v                    # all 42 tests
uv run pytest tests/ -v --cov=src/polymarket_agent  # with coverage
```

### Lint & Type Check
```bash
uv run ruff check src/                     # lint (currently clean)
uv run ruff format src/                    # format
uv run mypy src/                           # type check (currently clean)
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
| **2: Strategy Modules** | IN PROGRESS | MarketMaker, Arbitrageur done; AIAnalyst, signal aggregation, config pending |
| **3: MCP Server** | Planned | Expose tools for Claude (search_markets, place_trade, etc.) |
| **4: Live Trading** | Planned | py-clob-client, wallet setup, real order execution |

## Phase 2 Implementation Plan

See `docs/plans/2026-02-25-polymarket-agent-phase2.md` for detailed TDD plan with 8 tasks:

1. ~~Fix mypy errors from Phase 1~~ DONE
2. ~~MarketMaker strategy (bid/ask around midpoint)~~ DONE
3. ~~Arbitrageur strategy (price-sum deviation detection)~~ DONE
4. AIAnalyst strategy (Claude probability estimates) — NEXT
5. Signal aggregation (dedup, confidence filtering, multi-strategy consensus)
6. Config updates for new strategies
7. Integration test with all strategies
8. Final verification

## Dependencies

**Runtime:** pydantic>=2.0, pyyaml>=6.0, typer>=0.9
**Dev:** pytest>=8.0, pytest-mock>=3.0, ruff>=0.4, mypy>=1.10, types-PyYAML
**External:** `polymarket` CLI (Homebrew: `brew install polymarket`)
**Phase 2 adds:** anthropic SDK (for AIAnalyst — Task 4)

## Design Documents

- `docs/plans/2026-02-25-polymarket-agent-trader-design.md` — Full system design (architecture, all phases)
- `docs/plans/2026-02-25-polymarket-agent-phase1.md` — Phase 1 implementation plan (COMPLETE)
- `docs/plans/2026-02-25-polymarket-agent-phase2.md` — Phase 2 implementation plan (IN PROGRESS)

## Verification Commands

```bash
# Everything should pass
uv run pytest tests/ -v           # 42 tests passing
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found
uv run polymarket-agent tick      # Fetches live data, paper trades
```
