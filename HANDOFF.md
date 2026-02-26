# Polymarket Agent — Handoff Document

Last updated: 2026-02-25

---

## Session Entry — 2026-02-25 (Phase 2 Complete: Integration Test + Final Verification)

### Problem
- Phase 2 Tasks 7–8 pending: integration test and final verification.

### Solution
- **Task 7 — Integration test:** End-to-end test running signal_trader + arbitrageur through the full orchestrator pipeline with mocked CLI data. Tests paper mode (verifies signals generated, trades executed, balance decreased) and monitor mode (verifies signals generated but zero trades). Uses `_pipeline()` context manager and `_STRATEGIES` constant for DRY setup.
- **Task 8 — Final verification:** Full test suite (63 passed), ruff check (clean), mypy strict (19 source files clean), smoke test with live data (50 markets, 9 signals, 5 trades).
- **Code review + simplification pass:** Tightened integration test assertions (paper mode balance check was always-true, monitor mode `>= 0` was trivially true). Extracted `_pipeline` context manager and `_make_analyst` helper for DRY. Added `token_id` param to aggregator test helper. Added docstrings to tests missing them.

### Edits
- `tests/test_integration.py` — **NEW** (2 integration tests with shared `_pipeline()` context manager)
- `tests/test_ai_analyst.py` — extracted `_make_analyst` helper, added docstring to rate-limit edge case test
- `tests/test_aggregator.py` — added `token_id` param to `_signal()` helper, added docstrings to new tests

### NOT Changed
- No source code changes in this batch (all production code was already complete from Tasks 1–6).
- Pre-existing ruff isort issues in test_cli.py, test_client.py, test_db.py left as-is.

### Verification
```bash
uv run pytest tests/ -v           # 63 passed
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 19 source files
uv run polymarket-agent tick      # 50 markets, 9 signals, 5 trades (live data)
```

### Branch
- Working branch: `phase2/strategy-modules` (14 commits ahead of `main`)
- Batch 3 commits:
  - `af43ce4` test: add integration test for full multi-strategy pipeline
  - `47d5d93` refactor: apply code review and simplification to batch 3

---

## Session Entry — 2026-02-25 (Phase 2 Batch 2 Review Follow-Up)

### Problem
- Review of the newly added Batch 2 code found two correctness issues:
- `aggregate_signals()` grouped by `(market_id, side)`, which merged different token IDs (for example AIAnalyst sell on Yes vs other strategy sell on No) and could deduplicate to the wrong instrument.
- `aggregate_signals()` enforced `min_strategies` using raw signal count, so duplicate signals from one strategy could incorrectly satisfy consensus.
- `AIAnalyst` only counted successful/parsible responses toward rate limiting, allowing repeated unparseable responses to exceed `max_calls_per_hour`.

### Solution
- Updated signal aggregation to group by `(market_id, token_id, side)` instead of `(market_id, side)`.
- Changed consensus enforcement to count unique strategy names per group.
- Updated `AIAnalyst` to count every attempted Claude call toward rate limiting (including parse failures/exceptions) via `finally` block.
- Added focused regression tests for both aggregation issues and the AIAnalyst rate-limit edge case.

### Edits
- `src/polymarket_agent/strategies/aggregator.py`
- `src/polymarket_agent/strategies/ai_analyst.py`
- `tests/test_aggregator.py`
- `tests/test_ai_analyst.py`

### Verification
- `uv run python -m pytest tests/test_ai_analyst.py tests/test_aggregator.py -q` (passes)

---

## Session Entry — 2026-02-25 (Phase 2 Batch 2: AIAnalyst + Aggregation + Config)

### Problem
- Phase 2 Tasks 4–6 pending: AIAnalyst strategy, signal aggregation, and config updates for new strategies.

### Solution
- **Task 4 — AIAnalyst:** New strategy that sends market questions to Claude and parses probability estimates. If the AI estimate diverges from market price by more than `min_divergence`, emits a buy or sell signal. Rate-limited via sliding-window `_call_timestamps`. Gracefully degrades when `ANTHROPIC_API_KEY` is unset or `anthropic` package is missing. Added `anthropic` runtime dependency.
- **Task 5 — Signal aggregation:** New `aggregate_signals()` function groups signals by `(market_id, token_id, side)`, deduplicates (keeps highest confidence per group), filters by `min_confidence`, and enforces `min_strategies` consensus (counts unique strategy names). Integrated into orchestrator `tick()` between strategy signal collection and execution.
- **Task 6 — Config updates:** Added `AggregationConfig` (Pydantic model with `min_confidence` and `min_strategies`) to `config.py`. Updated `config.yaml` with entries for all 4 strategies and aggregation settings. Wired orchestrator to read aggregation params from config.
- **Code review + simplification pass:** Fixed critical sell-signal semantics (sell targets Yes token, not No), tightened regex to only match [0, 1] probabilities, extracted `_mock_client` test helper, added sell/parse-failure/exception test cases.

### Edits
- `pyproject.toml` — added `anthropic` runtime dependency
- `src/polymarket_agent/strategies/ai_analyst.py` — **NEW** (AIAnalyst strategy)
- `src/polymarket_agent/strategies/aggregator.py` — **NEW** (signal aggregation)
- `src/polymarket_agent/orchestrator.py` — added AIAnalyst to `STRATEGY_REGISTRY`, integrated `aggregate_signals()` into `tick()`
- `src/polymarket_agent/config.py` — added `AggregationConfig` model
- `config.yaml` — added market_maker, arbitrageur, ai_analyst, aggregation sections
- `tests/test_ai_analyst.py` — **NEW** (8 tests)
- `tests/test_aggregator.py` — **NEW** (7 tests)
- `tests/test_config.py` — added aggregation config assertions

---

## Session Entry — 2026-02-25 (Phase 2 Batch 1: mypy fixes + MarketMaker + Arbitrageur)

### Problem
- Phase 1 left 5 mypy strict-mode errors. Phase 2 plan calls for 3 new strategies, signal aggregation, and config updates.

### Solution
- **Task 1 — mypy fixes:** Added `types-PyYAML` dev dep, fixed type annotations across 4 files. Result: `mypy src/` → `Success: no issues found`.
- **Task 2 — MarketMaker:** Bid/ask around orderbook midpoint for liquid markets. Guards one-sided books, uses `except RuntimeError`.
- **Task 3 — Arbitrageur:** Price-sum deviation detection. Buys underpriced / sells overpriced side.
- **Code review + simplification pass:** Narrowed exception handling, added one-sided book guard, extracted helpers.

### Edits
- `pyproject.toml`, `src/polymarket_agent/data/client.py`, `src/polymarket_agent/data/models.py`, `src/polymarket_agent/strategies/signal_trader.py`, `src/polymarket_agent/execution/base.py`
- `src/polymarket_agent/strategies/market_maker.py` — **NEW**
- `src/polymarket_agent/strategies/arbitrageur.py` — **NEW**
- `src/polymarket_agent/orchestrator.py` — added to `STRATEGY_REGISTRY`
- `tests/test_market_maker.py` — **NEW** (5 tests), `tests/test_arbitrageur.py` — **NEW** (5 tests)

---

## Session Entry — 2026-02-25 (Execution Regression Fixes)

### Problem
- Refactor commit `d4c715e` introduced two behavior regressions in portfolio valuation and side validation.

### Solution
- Added regression tests, restored explicit side validation, fixed zero-price handling.

### Edits
- `src/polymarket_agent/execution/base.py`, `src/polymarket_agent/execution/paper.py`, `tests/test_paper_trader.py`

---

## Project Summary

**Polymarket Agent** is a Python auto-trading pipeline for Polymarket prediction markets. It wraps the official `polymarket` CLI (v0.1.4, installed via Homebrew) into a structured system with pluggable trading strategies, paper/live execution, and planned MCP server integration for AI agents.

## Current State: Phase 2 COMPLETE

- Branch: `phase2/strategy-modules` (14 commits ahead of `main`)
- 63 tests passing, ruff lint clean, mypy strict clean (19 source files)
- All 4 strategies implemented: SignalTrader, MarketMaker, Arbitrageur, AIAnalyst
- Signal aggregation integrated (groups by market+token+side, unique strategy consensus)
- Config updated for all strategies + aggregation
- Integration test covering paper + monitor modes
- Smoke tested with live Polymarket data

## Architecture

```
CLI (Typer) → Orchestrator → Data Layer (CLI wrapper + cache)
                           → Strategy Engine (pluggable ABCs)
                           → Signal Aggregation (dedup, confidence, consensus)
                           → Execution Layer (Paper/Live)
                           → SQLite (trade logging)
```

## File Map

| File | Purpose |
|------|---------|
| `src/polymarket_agent/cli.py` | Typer CLI: `run`, `status`, `tick` commands |
| `src/polymarket_agent/orchestrator.py` | Main loop: fetch → analyze → aggregate → execute |
| `src/polymarket_agent/config.py` | Pydantic config from YAML (incl. AggregationConfig) |
| `src/polymarket_agent/data/models.py` | Market, Event, OrderBook, PricePoint (Pydantic) |
| `src/polymarket_agent/data/client.py` | CLI wrapper with TTL caching |
| `src/polymarket_agent/data/cache.py` | In-memory TTL cache |
| `src/polymarket_agent/db.py` | SQLite trade logging |
| `src/polymarket_agent/strategies/base.py` | Strategy ABC + Signal dataclass |
| `src/polymarket_agent/strategies/signal_trader.py` | Volume-filtered directional signals |
| `src/polymarket_agent/strategies/market_maker.py` | Bid/ask around orderbook midpoint |
| `src/polymarket_agent/strategies/arbitrageur.py` | Price-sum deviation detection |
| `src/polymarket_agent/strategies/ai_analyst.py` | Claude probability estimates + divergence trading |
| `src/polymarket_agent/strategies/aggregator.py` | Signal dedup, confidence filtering, consensus |
| `src/polymarket_agent/execution/base.py` | Executor ABC + Portfolio + Order |
| `src/polymarket_agent/execution/paper.py` | Paper trading with virtual USDC |
| `config.yaml` | Default config (paper mode, $1000, 4 strategies, aggregation) |
| `pyproject.toml` | Project config (deps, ruff, mypy) |

## Key Design Decisions

1. **All data comes from `polymarket` CLI** — subprocess with `-o json`, parsed through `PolymarketData._run_cli()`. Never call subprocess directly elsewhere.
2. **Null-safe parsing** — Polymarket CLI returns `null` for optional fields. Helper functions `_str_field()`, `_float_field()`, `_parse_json_field()` in models.py.
3. **Strategy ABC** — All strategies implement `analyze(markets, data) -> list[Signal]`. Register in `STRATEGY_REGISTRY` dict in `orchestrator.py`.
4. **Executor ABC** — `place_order(signal) -> Order | None`. Paper and Live share interface.
5. **Signal dataclass** — `strategy`, `market_id`, `token_id`, `side` (buy/sell), `confidence`, `target_price`, `size` (USDC), `reason`.
6. **Signal aggregation** — `aggregate_signals()` groups by `(market_id, token_id, side)`, deduplicates (highest confidence wins), filters by `min_confidence`, enforces `min_strategies` consensus (unique strategy names). Runs between strategy collection and execution in `tick()`.

## Known Issues from Code Review

### Critical
1. ~~**mypy strict fails**~~ — FIXED in Task 1.
2. **OrderBook.midpoint/spread** — Returns 0.0 or negative when asks/bids empty. MarketMaker guards against it, but the model still allows it.
3. **Risk config not enforced** — `RiskConfig` loaded but never checked.
4. **Signal.size semantics** — Ambiguous whether dollars or shares.
5. **Database connection never closed** — No context manager, no cleanup on KeyboardInterrupt.

### Important
6. **No subprocess timeout** — `_run_cli()` can hang indefinitely.
7. **Empty token_id signals** — SignalTrader emits `""` token_id when `clob_token_ids` missing.
8. **No sell-side test coverage** — Paper trader sell path untested.
9. **Config path relative to CWD** — Silently falls back to defaults.
10. **MarketMaker `_max_inventory` not enforced** — Configured but unused.
11. **Arbitrageur `_min_deviation` not used** — Configured but unused.
12. **Prompt injection surface** — AIAnalyst interpolates external data directly into prompt.

## How to Work With This Codebase

### Setup
```bash
cd /Users/pharrelly/codebase/github/polymarket-agent
uv sync
```

### Run Tests
```bash
uv run pytest tests/ -v                    # all 63 tests
```

### Lint & Type Check
```bash
uv run ruff check src/                     # lint (currently clean)
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

## Phase Plan

| Phase | Status | Description |
|-------|--------|-------------|
| **1: Data + Paper Trading** | COMPLETE | CLI wrapper, models, cache, paper executor, SignalTrader, orchestrator, CLI |
| **2: Strategy Modules** | COMPLETE | MarketMaker, Arbitrageur, AIAnalyst, signal aggregation, config, integration test |
| **3: MCP Server** | Planned | Expose tools for Claude (search_markets, place_trade, etc.) |
| **4: Live Trading** | Planned | py-clob-client, wallet setup, real order execution |

## Phase 2 Implementation Plan

See `docs/plans/2026-02-25-polymarket-agent-phase2.md` for detailed TDD plan with 8 tasks:

1. ~~Fix mypy errors from Phase 1~~ DONE
2. ~~MarketMaker strategy (bid/ask around midpoint)~~ DONE
3. ~~Arbitrageur strategy (price-sum deviation detection)~~ DONE
4. ~~AIAnalyst strategy (Claude probability estimates)~~ DONE
5. ~~Signal aggregation (dedup, confidence filtering, multi-strategy consensus)~~ DONE
6. ~~Config updates for new strategies~~ DONE
7. ~~Integration test with all strategies~~ DONE
8. ~~Final verification~~ DONE

## Dependencies

**Runtime:** pydantic>=2.0, pyyaml>=6.0, typer>=0.9, anthropic>=0.84
**Dev:** pytest>=8.0, pytest-mock>=3.0, ruff>=0.4, mypy>=1.10, types-PyYAML
**External:** `polymarket` CLI (Homebrew: `brew install polymarket`)

## Design Documents

- `docs/plans/2026-02-25-polymarket-agent-trader-design.md` — Full system design (architecture, all phases)
- `docs/plans/2026-02-25-polymarket-agent-phase1.md` — Phase 1 implementation plan (COMPLETE)
- `docs/plans/2026-02-25-polymarket-agent-phase2.md` — Phase 2 implementation plan (COMPLETE)

## Verification Commands

```bash
# Everything should pass
uv run pytest tests/ -v           # 63 tests passing
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 19 source files
uv run polymarket-agent tick      # Fetches live data, paper trades
```
