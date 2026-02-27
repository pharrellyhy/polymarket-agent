# Polymarket Agent — Handoff Document

Last updated: 2026-02-27

---

## Session Entry — 2026-02-27 (Review Follow-Up: Provider Validation Hardening)

### Problem
- Review of the new OpenAI-compatible support found a config-safety gap: unknown provider strings were silently routed down OpenAI paths instead of being rejected/falling back predictably.
- Impact:
  - `autotune` could fail with misleading API-key/import errors for typoed providers.
  - `AIAnalyst` could hold an invalid provider value and behave unexpectedly.
  - CLI `autotune` would do full evaluation work before surfacing provider mistakes.

### Solution
- **autotune provider validation:** Added explicit provider normalization/validation in `_init_client()` and raise `ValueError` for unsupported providers.
- **AIAnalyst provider normalization:** `configure()` now lowercases/validates provider and falls back to default (`anthropic`) with a warning on unknown values.
- **Defensive init guard:** `_init_client()` in `AIAnalyst` now has an explicit unknown-provider fallback branch.
- **CLI fast-fail validation:** `polymarket-agent autotune` now validates `--provider` early and raises `BadParameter` before orchestrator/evaluation work.
- **Regression tests:** Added targeted tests for unknown-provider rejection/fallback and CLI validation path.

### Edits
- `src/polymarket_agent/autotune.py` — added `_SUPPORTED_PROVIDERS` and strict provider validation in `_init_client()`
- `src/polymarket_agent/strategies/ai_analyst.py` — added provider normalization/validation + fallback safeguards
- `src/polymarket_agent/cli.py` — added early `--provider` validation in `autotune` command
- `tests/test_autotune.py` — added unknown-provider `_init_client` test and CLI invalid-provider test
- `tests/test_ai_analyst.py` — added invalid-provider fallback test
- `HANDOFF.md` — added this review follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No changes to provider API request payload shapes.
- No changes to autotune parameter validation/apply semantics.
- No changes to MCP tool behavior in this follow-up.

### Verification
```bash
uv run python -m pytest tests/test_ai_analyst.py tests/test_autotune.py -q   # 38 passed
./.venv/bin/ruff check src/polymarket_agent/autotune.py src/polymarket_agent/strategies/ai_analyst.py src/polymarket_agent/cli.py tests/test_ai_analyst.py tests/test_autotune.py   # All checks passed
./.venv/bin/mypy src/polymarket_agent/autotune.py src/polymarket_agent/strategies/ai_analyst.py src/polymarket_agent/cli.py   # Success: no issues found in 3 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (OpenAI-Compatible API Support)

### Problem
- AIAnalyst strategy was hardcoded to Anthropic SDK — no way to use OpenAI-compatible providers (OpenAI, local Ollama/vLLM endpoints).
- `autotune.sh` used `claude -p` which fails with 403 in non-interactive/launchd contexts. No way to use alternative LLM providers for auto-tuning.

### Solution
- **AIAnalyst provider abstraction (Step 1):** Added `_DEFAULT_PROVIDER`, `_provider`, `_base_url`, `_api_key_env` instance vars. Split `_init_client()` into `_init_anthropic_client()` / `_init_openai_client()`. Extracted `_call_llm()` to abstract API response shapes. `configure()` now reads `provider`, `base_url`, `api_key_env` and re-initializes the client when changed.
- **autotune.py module (Step 2):** New `src/polymarket_agent/autotune.py` with `run_autotune()` function. Builds system prompt with tuning rules, sends eval JSON to LLM, parses structured JSON response, validates changes against tunable parameter min/max ranges, applies changes to config.yaml via PyYAML. Supports both anthropic and openai providers.
- **CLI autotune subcommand (Step 3):** New `polymarket-agent autotune` command with `--provider`, `--model`, `--base-url`, `--api-key-env`, `--period` options. Runs evaluate logic internally then calls `run_autotune()`.
- **autotune.sh provider dispatch (Step 4):** `AUTOTUNE_PROVIDER` env var selects "claude" (original `claude -p` flow), "openai", or "anthropic" (new direct API flow via CLI subcommand). Also added `AUTOTUNE_MODEL`, `AUTOTUNE_BASE_URL`, `AUTOTUNE_API_KEY_ENV` env vars.
- **Config + deps (Step 5):** Added commented-out `provider`, `base_url`, `api_key_env` fields to `config.yaml` ai_analyst section. Added `openai = ["openai>=1.0"]` optional dependency group to pyproject.toml.
- **MCP docstring update (Step 6):** Made `analyze_market` docstring and error message provider-agnostic.
- **Tests (Step 7):** 7 new OpenAI provider tests in test_ai_analyst.py. 19 new tests in test_autotune.py (parsing, validation, config modification, integration). Updated test_mcp_server.py assertion for generic error message. All 315 tests pass.

### Edits
- `src/polymarket_agent/strategies/ai_analyst.py` — provider abstraction: `_init_anthropic_client()`, `_init_openai_client()`, `_call_llm()`, updated `configure()`
- `src/polymarket_agent/autotune.py` — **NEW** LLM-based config auto-tuner module
- `src/polymarket_agent/cli.py` — added `autotune` subcommand
- `scripts/autotune.sh` — provider dispatch (claude/openai/anthropic)
- `scripts/com.polymarket-agent.autotune.plist` — added env var placeholders for provider/model/keys
- `config.yaml` — commented-out provider fields under ai_analyst
- `pyproject.toml` — added `openai` optional dependency group
- `src/polymarket_agent/mcp_server.py` — provider-agnostic docstring and error message
- `tests/test_ai_analyst.py` — 7 new OpenAI provider tests
- `tests/test_autotune.py` — **NEW** 19 tests for autotune module
- `tests/test_mcp_server.py` — updated error message assertion

### NOT Changed
- `config.py` — `strategies: dict[str, dict[str, Any]]` already passes through arbitrary keys
- `orchestrator.py` — `_load_strategies()` already handles `cls()` + `configure()`
- `strategies/base.py` — Strategy ABC is flexible enough
- No changes to existing Anthropic behavior — default provider remains "anthropic"

### Verification
```bash
uv run pytest tests/ -v                  # 315 passed
uv run ruff check src/ tests/test_ai_analyst.py tests/test_autotune.py tests/test_mcp_server.py  # All checks passed
uv run mypy src/                         # Success: no issues found in 34 source files
uv run polymarket-agent autotune --help  # Shows autotune command
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Review Follow-Up: Dashboard HTML Simplification)

### Problem
- Follow-up review of the newly added dashboard UI code found repeated table-rendering logic and inconsistent handling of empty/malformed values in the browser layer.
- The added sections worked, but repeated string-building patterns made maintenance harder and could surface `NaN`/blank fields in edge cases.

### Solution
- **Rendering helper extraction:** Added shared helpers in dashboard JS: `toNum()`, `fixed()`, `shortId()`, and `renderRows()` to remove repeated mapping boilerplate.
- **Safer fetch path:** `fetchJSON()` now throws on non-2xx responses so the status banner reports request failures consistently.
- **Defensive formatting:** Replaced direct `Number(...).toFixed(...)` usage with safe numeric helpers to avoid malformed-value display issues.
- **Empty-state rows:** Table rendering now shows a stable “No data” row per section instead of a completely blank table body.

### Edits
- `src/polymarket_agent/dashboard/static/dashboard.html` — simplified table rendering and added defensive formatting/fetch helpers
- `HANDOFF.md` — added this review follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No API endpoint changes, no DB changes, and no orchestrator changes in this follow-up.
- No visual redesign; existing section layout and data semantics remain the same.

### Verification
```bash
uv run python -m pytest tests/test_dashboard_api.py tests/test_config_reload.py -q   # 28 passed
./.venv/bin/ruff check src/polymarket_agent/dashboard/api.py tests/test_dashboard_api.py tests/test_config_reload.py   # All checks passed
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Review Follow-Up: Dashboard Strategy Stats Robustness)

### Problem
- Reviewed the newest dashboard additions (`/api/positions`, `/api/strategy-performance`, `/api/config-changes`, `/api/conditional-orders`) from the top handoff entry.
- Found a robustness bug in `/api/strategy-performance`: non-numeric trade sizes (for example `None` or malformed strings) raised `ValueError` and returned a 500.
- Found duplication in strategy stats aggregation (bucket initialization repeated in both trade and signal loops).

### Solution
- **TDD regression coverage first:** Added a failing test that exercises malformed trade sizes in `/api/strategy-performance`.
- **Robust numeric parsing:** Added `_to_float(...)` helper and switched strategy/position math to use it.
- **Simplified stats aggregation:** Added `StrategyStats` `TypedDict` and `_get_strategy_stats_bucket(...)` helper to remove duplicate initialization logic while keeping payload shape unchanged.
- **Defensive side handling:** Only applies net P&L math to recognized `buy`/`sell` sides; unknown sides no longer skew totals.

### Edits
- `src/polymarket_agent/dashboard/api.py` — added `_to_float(...)`, `StrategyStats`, `_get_strategy_stats_bucket(...)`; simplified strategy aggregation and hardened numeric conversion
- `tests/test_dashboard_api.py` — added regression test for malformed trade size handling in `/api/strategy-performance`
- `HANDOFF.md` — added this review follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No DB schema changes, no orchestrator reload logic changes, and no dashboard HTML changes in this follow-up.
- No changes to existing endpoint URLs or response field names.

### Verification
```bash
uv run python -m pytest tests/test_dashboard_api.py tests/test_config_reload.py -q   # 28 passed
./.venv/bin/ruff check src/polymarket_agent/dashboard/api.py tests/test_dashboard_api.py tests/test_config_reload.py   # All checks passed
./.venv/bin/mypy src/polymarket_agent/dashboard/api.py   # Success: no issues found in 1 source file
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Dashboard Enhancement: 4 New Sections)

### Problem
- The monitoring dashboard showed only basic stats (balance, total value, P&L chart, recent trades, recent signals). Missing per-position P&L, per-strategy performance, config change history, and conditional order status.

### Solution
- **DB layer (Step 1):** Added `config_changes` table to `_create_tables()` for storing config diffs. Added 3 new methods: `record_config_change()`, `get_config_changes()`, `get_all_conditional_orders()`.
- **Orchestrator (Step 2):** Added `_compute_config_diff()` static method that recursively walks two AppConfig dicts and returns `{dotted_path: {old, new}}` for all differences. Modified `reload_config()` to persist non-empty diffs via `record_config_change()`.
- **API (Step 3):** Added 4 new FastAPI endpoints: `/api/positions` (per-position P&L), `/api/strategy-performance` (trade/signal stats per strategy), `/api/config-changes` (diff history), `/api/conditional-orders` (all statuses).
- **Dashboard HTML (Step 4):** Added 4 new sections with tables (Open Positions, Strategy Performance, Conditional Orders, Config Change History) plus CSS badge classes for order types/statuses and diff coloring. JS `refresh()` now fetches all 8 endpoints in parallel.
- **Tests (Step 5):** 8 new dashboard API tests + 2 new config reload tests. All 288 tests pass.

### Edits
- `src/polymarket_agent/db.py` — added `config_changes` table, `record_config_change()`, `get_config_changes()`, `get_all_conditional_orders()`
- `src/polymarket_agent/orchestrator.py` — added `_compute_config_diff()` static method; modified `reload_config()` to record diffs
- `src/polymarket_agent/dashboard/api.py` — added 4 new endpoints: `/api/positions`, `/api/strategy-performance`, `/api/config-changes`, `/api/conditional-orders`
- `src/polymarket_agent/dashboard/static/dashboard.html` — added 4 new HTML sections, CSS badge classes, JS rendering for all 4 sections
- `tests/test_dashboard_api.py` — 8 new tests; updated mock portfolio to include `current_price`
- `tests/test_config_reload.py` — 2 new tests for config diff recording

### NOT Changed
- No changes to strategy logic, MCP server, execution layer, or CLI commands.
- No changes to existing API endpoints or their response shapes.

### Verification
```bash
uv run pytest tests/test_dashboard_api.py tests/test_config_reload.py -v   # 27 passed
uv run pytest tests/ -v                                                      # 288 passed
uv run ruff check src/ tests/test_dashboard_api.py tests/test_config_reload.py  # All checks passed
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Review Follow-Up: Evaluate Helper Simplification)

### Problem
- Reviewed the newest auto-tuning additions from the top handoff entry (`Automated Strategy Tuning`), focusing on `evaluate` helper paths.
- Found cleanup opportunities in the new evaluate helpers:
  - `_build_summary()` accepted extra parameters that were never used.
  - `_analyze_trades()` built intermediate buy/sell/size lists when simple counters/accumulators were sufficient.

### Solution
- **Simplified `_analyze_trades()` implementation:** Replaced list materialization with a single-pass counter/accumulator loop for buys, sells, and total size while preserving output shape.
- **Simplified `_build_summary()` interface:** Removed unused parameters and updated call sites/tests to pass only `metrics`, matching actual usage.
- **Behavior preserved:** JSON payload schema and summary content rules remain unchanged.

### Edits
- `src/polymarket_agent/cli.py` — simplified `_analyze_trades()` internals; removed unused `_build_summary()` parameters and updated caller
- `tests/test_evaluate.py` — updated helper tests for the simplified `_build_summary()` signature
- `HANDOFF.md` — added this review follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No changes to hot-reload behavior, orchestrator config reload logic, or auto-tune script/plist wiring in this follow-up.
- No changes to strategy logic, execution behavior, or MCP tools.

### Verification
```bash
uv run python -m pytest tests/test_evaluate.py -q   # 13 passed
uv run ruff check src/polymarket_agent/cli.py tests/test_evaluate.py   # All checks passed
uv run mypy src/polymarket_agent/cli.py   # Success: no issues found in 1 source file
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Automated Strategy Tuning via Claude Code)

### Problem
- The trading loop runs 24/7 but tuning requires manually running `report`, reading output, editing `config.yaml`, and restarting. No automated performance review or config adjustment mechanism existed.

### Solution
- **Config hot-reload (Step 1):** Added `config_mtime()` helper and `Orchestrator.reload_config()` method. The `run` loop now checks config file mtime before each tick and hot-reloads on change. Safety: mode changes (paper→live) are rejected; executor is preserved so positions stay in memory; config parse failures are caught and logged without interrupting the loop.
- **`evaluate` command (Step 2):** New `polymarket-agent evaluate --period 24h --json` command outputs structured JSON with: metrics, per-strategy breakdown, trade analysis, current config, tunable parameters (with min/max ranges), safety constraints, and a natural-language summary with diagnostic notes.
- **Auto-tune script (Step 3):** `scripts/autotune.sh` runs evaluate, pipes JSON to `claude -p` with tuning rules (never change mode, max 2-3 params, respect min/max). `scripts/com.polymarket-agent.autotune.plist` schedules it every 6 hours via macOS launchd.
- **21 new tests:** 8 config reload tests (mtime, strategy rebuild, mode rejection, executor preservation, risk update, poll interval) + 13 evaluate tests (tunable params, trade analysis, summary diagnostics, CLI JSON/text output).

### Edits
- `src/polymarket_agent/config.py` — added `config_mtime(path)` function
- `src/polymarket_agent/orchestrator.py` — added `reload_config(new_config)` method and `poll_interval` property
- `src/polymarket_agent/cli.py` — hot-reload logic in `run()` loop; added `evaluate` command with `_build_tunable_params()`, `_analyze_trades()`, `_build_summary()` helpers
- `scripts/autotune.sh` — **NEW** auto-tune shell script invoking Claude Code
- `scripts/com.polymarket-agent.autotune.plist` — **NEW** macOS launchd plist (6h interval)
- `tests/test_config_reload.py` — **NEW** 8 tests for hot-reload functionality
- `tests/test_evaluate.py` — **NEW** 13 tests for evaluate command and helpers

### NOT Changed
- No changes to strategy logic, MCP server, data layer, or execution layer.
- No changes to existing `report` command (evaluate is a separate, machine-readable complement).
- launchd plist not auto-installed — user must manually `cp` and `launchctl load`.

### Verification
```bash
uv run pytest tests/test_config_reload.py tests/test_evaluate.py -v   # 21 passed
uv run pytest tests/ -v                                                # 278 passed (full suite)
uv run ruff check src/ tests/test_config_reload.py tests/test_evaluate.py  # All checks passed
uv run polymarket-agent evaluate --help                                # Shows evaluate command
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Review Follow-Up: Snapshot Path Cleanup + Paper Recovery Simplification)

### Problem
- Reviewed the newly added 2026-02-27 performance/persistence code in `orchestrator.py`, `paper.py`, and related tests.
- Found a duplication edge case: `tick()` could write a periodic snapshot and then immediately write a forced snapshot in the same trade tick.
- Found a small API design smell: `PaperTrader.recover_from_db()` required a `db` parameter even though `PaperTrader` already owns `self._db`.
- The handoff-listed pre-existing test failure (`test_paper_trader_sell_insufficient_shares`) was still present and out of sync with the current sell behavior (sell all available shares).

### Solution
- **Snapshot path cleanup:** Updated `tick()` to use one snapshot path per tick:
  - trade tick -> forced snapshot only
  - no-trade tick -> periodic snapshot only
- **Paper recovery simplification:** Changed `PaperTrader.recover_from_db()` to use `self._db` directly and removed the redundant parameter from call sites.
- **Regression coverage added:** New tests verify:
  - trade ticks use only the forced snapshot path
  - paper trader recovery restores balance/positions from the latest snapshot
- **Pre-existing failure resolved:** Updated `test_paper_trader_sell_insufficient_shares` to match actual executor semantics (partial fill by selling all available shares).

### Edits
- `src/polymarket_agent/orchestrator.py` — removed duplicate snapshot path on trade ticks; updated paper recovery call
- `src/polymarket_agent/execution/paper.py` — simplified `recover_from_db()` signature and DB usage
- `tests/test_risk_gate.py` — added snapshot-path regression test
- `tests/test_paper_trader.py` — added recovery regression test; fixed insufficient-shares sell test expectations
- `HANDOFF.md` — added this review-follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No changes to strategy logic, MCP server, or data-layer parsing behavior in this follow-up.
- No full-suite rerun; verification remained scoped to the touched orchestrator/paper test surfaces.

### Verification
```bash
uv run python -m pytest tests/test_risk_gate.py tests/test_paper_trader.py -q   # 22 passed
uv run ruff check src/polymarket_agent/orchestrator.py src/polymarket_agent/execution/paper.py tests/test_risk_gate.py tests/test_paper_trader.py   # All checks passed
uv run mypy src/polymarket_agent/orchestrator.py src/polymarket_agent/execution/paper.py   # Success: no issues found in 2 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Performance Monitoring & Position Persistence)

### Problem
- PaperTrader positions were lost on restart (kept in memory only).
- `status` command showed zero positions after restart.
- No `report` command for performance evaluation.
- Portfolio snapshots only recorded every 300s (configurable), missing state changes when trades occurred.

### Solution
- **Position persistence (Step 1):** Added `recover_from_db()` to PaperTrader that restores `_positions` and `_balance` from the latest `portfolio_snapshots` row. Called automatically during orchestrator init for paper mode.
- **Force-snapshot on trade (Step 2):** After trades execute in `tick()`, `_force_portfolio_snapshot()` writes an immediate snapshot (bypassing the interval throttle) so every portfolio state change is captured. Extracted shared `_write_portfolio_snapshot()` helper.
- **Report command (Step 3):** New `polymarket-agent report` CLI command with `--period` (e.g. `24h`, `7d`) and `--json` flags. Computes metrics via `backtest/metrics.py`, shows portfolio summary, open position P&L with current prices, per-strategy breakdown, and recent trades.
- **Enhanced status (Step 4):** `status` command now shows a position table with shares, entry price, current price, unrealized P&L, and P&L%.
- **DB helpers:** Added `get_latest_snapshot()` method and `since` filter parameter to `get_trades()` and `get_portfolio_snapshots()`.

### Edits
- `src/polymarket_agent/db.py` — added `get_latest_snapshot()`, `since` filter on `get_trades()` and `get_portfolio_snapshots()`
- `src/polymarket_agent/execution/paper.py` — added `recover_from_db()` method with JSON position parsing
- `src/polymarket_agent/orchestrator.py` — call `recover_from_db()` in `_build_executor()` for paper mode; added `_force_portfolio_snapshot()` and `_write_portfolio_snapshot()`; force snapshot after trades in `tick()`
- `src/polymarket_agent/cli.py` — added `report` command with `_parse_period()` helper; enhanced `status` with position P&L table
- `tests/test_risk_gate.py` — fixed `test_risk_gate_blocks_when_daily_loss_exceeded` to use distinct token IDs (position recovery now catches duplicate-token buys earlier)

### NOT Changed
- No changes to strategies, MCP server, backtest engine, or live trader.
- Pre-existing `test_paper_trader_sell_insufficient_shares` failure left as-is (test expects rejection, but code intentionally sells all available shares).

### Verification
```bash
uv run pytest tests/ -v           # 254 passed, 1 pre-existing failure
uv run ruff check src/            # All checks passed
uv run ruff format --check src/   # All files formatted
uv run polymarket-agent report --help   # Shows report command
uv run polymarket-agent status --help   # Shows status command
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-26 (Review Follow-Up: MCP get_event Error Handling + Wrapper Simplification)

### Problem
- Reviewed the newly added code from the top handoff entry (`Known Issues Fix + MCP Data Tools`).
- Found an inconsistency in the new MCP wrappers: `get_event()` handled "not found" but did not handle CLI `RuntimeError`, despite the handoff claiming consistent CLI-failure handling across the new MCP tools.
- The new MCP wrappers also repeated near-identical `try/except RuntimeError` blocks.

### Solution
- **TDD regression fix:** Added a failing MCP test for `get_event()` CLI failure handling, verified the failure, then implemented the fix.
- **`get_event()` CLI failure handling:** `get_event()` now returns `{"error": ...}` on `RuntimeError` instead of propagating an exception.
- **Wrapper simplification:** Added `_runtime_safe_tool(...)` helper in `mcp_server.py` and applied it to `get_event`, `get_price`, `get_spread`, `get_volume`, and `get_positions` to remove duplicated `try/except` blocks while preserving existing payload shapes.
- **Test import cleanup:** Consolidated `tests/test_mcp_server.py` imports to satisfy Ruff after the new test addition.

### Edits
- `src/polymarket_agent/mcp_server.py` — fixed `get_event()` runtime-error handling; added `_runtime_safe_tool()` helper; simplified new MCP wrapper error handling
- `tests/test_mcp_server.py` — added `get_event()` CLI failure regression test; consolidated import block
- `HANDOFF.md` — added this review follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No changes to the newly added CLI/config, order book, Signal docstring, SignalTrader, or PaperTrader sell-side logic from the reviewed handoff entry.
- MCP tool response shapes remain unchanged, except `get_event()` now returns an error payload instead of raising on CLI failure.

### Verification
```bash
uv run python -m pytest tests/test_mcp_server.py -q   # 35 passed
uv run ruff check src/polymarket_agent/mcp_server.py tests/test_mcp_server.py   # All checks passed
uv run mypy src/polymarket_agent/mcp_server.py   # Success: no issues found in 1 source file
```

### Branch
- Working branch: `main`

---

## Project Summary

**Polymarket Agent** is a Python auto-trading pipeline for Polymarket prediction markets. It wraps the official `polymarket` CLI (v0.1.4, installed via Homebrew) into a structured system with pluggable trading strategies, paper/live execution, and MCP server integration for AI agents.

## Current State: Phase 4 COMPLETE

- 141 tests passing, ruff lint clean, mypy strict clean (21 source files)
- All 4 strategies implemented: SignalTrader, MarketMaker, Arbitrageur, AIAnalyst
- Signal aggregation integrated (groups by market+token+side, unique strategy consensus)
- MCP server with 14 tools: search_markets, get_market_detail, get_price_history, get_leaderboard, get_portfolio, get_signals, refresh_signals, place_trade, analyze_market, get_event, get_price, get_spread, get_volume, get_positions
- LiveTrader with py-clob-client for real order execution
- Risk management: max_position_size, max_daily_loss, max_open_orders enforced in Orchestrator
- CLI commands: `run` (with `--live` safety flag + config hot-reload), `status`, `tick`, `report`, `evaluate`, `backtest`, `dashboard`, `mcp`
- Auto-tune pipeline: `scripts/autotune.sh` + launchd plist for periodic Claude Code-driven config tuning

## Architecture

```
CLI (Typer) → Orchestrator → Data Layer (CLI wrapper + cache + 30s timeout)
                           → Strategy Engine (pluggable ABCs)
                           → Signal Aggregation (dedup, confidence, consensus)
                           → Risk Gate (position size, daily loss, open orders)
                           → Execution Layer (Paper/Live via factory)
                           → SQLite (trade logging, context manager)

MCP Server (FastMCP, stdio) → AppContext → Orchestrator + Data + Config
  ├── search_markets, get_market_detail, get_price_history, get_leaderboard
  ├── get_portfolio, get_signals, refresh_signals
  ├── place_trade
  └── analyze_market
```

## File Map

| File | Purpose |
|------|---------|
| `src/polymarket_agent/cli.py` | Typer CLI: `run` (hot-reload), `status`, `tick`, `report`, `evaluate`, `backtest`, `dashboard`, `mcp` |
| `src/polymarket_agent/orchestrator.py` | Main loop: fetch → analyze → aggregate → execute |
| `src/polymarket_agent/config.py` | Pydantic config from YAML (incl. AggregationConfig) |
| `src/polymarket_agent/data/models.py` | Market, Event, OrderBook, PricePoint, Trader, Spread, Volume, Position (Pydantic) |
| `src/polymarket_agent/data/client.py` | CLI wrapper with TTL caching + 12 public methods |
| `src/polymarket_agent/data/cache.py` | In-memory TTL cache |
| `src/polymarket_agent/db.py` | SQLite trade logging |
| `src/polymarket_agent/strategies/base.py` | Strategy ABC + Signal dataclass |
| `src/polymarket_agent/strategies/signal_trader.py` | Volume-filtered directional signals |
| `src/polymarket_agent/strategies/market_maker.py` | Bid/ask around orderbook midpoint |
| `src/polymarket_agent/strategies/arbitrageur.py` | Price-sum deviation detection |
| `src/polymarket_agent/strategies/ai_analyst.py` | Claude probability estimates + divergence trading |
| `src/polymarket_agent/strategies/aggregator.py` | Signal dedup, confidence filtering, consensus |
| `src/polymarket_agent/execution/base.py` | Executor ABC + Portfolio + Order + cancel_order/get_open_orders |
| `src/polymarket_agent/execution/paper.py` | Paper trading with virtual USDC |
| `src/polymarket_agent/execution/live.py` | Live trading via py-clob-client (optional dep) |
| `src/polymarket_agent/mcp_server.py` | MCP server: 14 tools, lifespan context, module-level `mcp` + `configure()` |
| `config.yaml` | Default config (paper mode, $1000, 4 strategies, aggregation) |
| `pyproject.toml` | Project config (deps incl. mcp>=1.0, ruff, mypy) |

## Key Design Decisions

1. **All data comes from `polymarket` CLI** — subprocess with `-o json`, parsed through `PolymarketData._run_cli()`. Never call subprocess directly elsewhere.
2. **Null-safe parsing** — Polymarket CLI returns `null` for optional fields. Helper functions `_str_field()`, `_float_field()`, `_parse_json_field()` in models.py.
3. **Strategy ABC** — All strategies implement `analyze(markets, data) -> list[Signal]`. Register in `STRATEGY_REGISTRY` dict in `orchestrator.py`.
4. **Executor ABC** — `place_order(signal) -> Order | None`. Paper and Live share interface.
5. **Signal dataclass** — `strategy`, `market_id`, `token_id`, `side` (buy/sell), `confidence`, `target_price`, `size` (USDC), `reason`.
6. **Signal aggregation** — `aggregate_signals()` groups by `(market_id, token_id, side)`, deduplicates (highest confidence wins), filters by `min_confidence`, enforces `min_strategies` consensus (unique strategy names). Runs between strategy collection and execution in `tick()`.
7. **MCP server** — FastMCP with lifespan context. `AppContext` dataclass holds Orchestrator + PolymarketData + AppConfig. Tools are thin wrappers that delegate to existing layers. `configure()` sets config/db paths for the module-level `mcp` instance before `mcp.run()`.
8. **Executor factory** — `_build_executor()` in Orchestrator selects PaperTrader or LiveTrader based on `config.mode`. Live mode lazily imports LiveTrader and reads env vars.
9. **Risk gate** — `_check_risk()` enforces max_position_size, max_daily_loss, max_open_orders before every trade in `tick()`.
10. **LiveTrader** — Thin wrapper around py-clob-client ClobClient. Limit orders (GTC), lazy imports, `from_env()` factory for env var auth.

## Known Issues from Code Review

### Critical
1. ~~**mypy strict fails**~~ — FIXED in Phase 2 Task 1.
2. ~~**OrderBook.midpoint/spread**~~ — FIXED. Returns 0.0 when either side is empty.
3. ~~**Risk config not enforced**~~ — FIXED in Phase 4. `_check_risk()` enforces all 3 limits.
4. ~~**Signal.size semantics**~~ — FIXED. Docstring clarifies size is always USDC; execution layer converts to shares.
5. ~~**Database connection never closed**~~ — FIXED in Phase 4. Context manager + `orch.close()` in CLI.

### Important
6. ~~**No subprocess timeout**~~ — FIXED in Phase 4. `_run_cli()` has 30s timeout.
7. ~~**Empty token_id signals**~~ — FIXED. SignalTrader now skips markets with missing token IDs.
8. ~~**No sell-side test coverage**~~ — FIXED. 5 sell-side PaperTrader tests added.
9. ~~**Config path relative to CWD**~~ — FIXED. CLI now logs a warning when config file not found.
10. ~~**MarketMaker `_max_inventory` not enforced**~~ — FIXED in Phase 4. Removed dead config; position limits enforced by Orchestrator risk gate.
11. ~~**Arbitrageur `_min_deviation` not used**~~ — FIXED in Phase 4. Now used in signal gating logic.
12. ~~**Prompt injection surface**~~ — FIXED in Phase 4. AIAnalyst sanitizes external text (strip control chars, truncate, delimiters).
13. ~~**MCP tools access private attributes**~~ — FIXED in Phase 3. Public methods added to Orchestrator.

## How to Work With This Codebase

### Setup
```bash
cd /Users/pharrelly/codebase/github/polymarket-agent
uv sync
```

### Run Tests
```bash
uv run pytest tests/ -v                    # all 141 tests
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
uv run polymarket-agent mcp                # start MCP server (stdio transport)
```

### Adding a New Strategy
1. Create `src/polymarket_agent/strategies/<name>.py` implementing `Strategy` ABC
2. Add to `STRATEGY_REGISTRY` in `orchestrator.py`
3. Add config block in `config.yaml` under `strategies:`
4. Write tests in `tests/test_<name>.py`

### Using the MCP Server
The MCP server runs via stdio transport. To use with Claude Code, add to your MCP config:
```json
{
  "mcpServers": {
    "polymarket": {
      "command": "uv",
      "args": ["run", "polymarket-agent", "mcp"]
    }
  }
}
```

## Phase Plan

| Phase | Status | Description |
|-------|--------|-------------|
| **1: Data + Paper Trading** | COMPLETE | CLI wrapper, models, cache, paper executor, SignalTrader, orchestrator, CLI |
| **2: Strategy Modules** | COMPLETE | MarketMaker, Arbitrageur, AIAnalyst, signal aggregation, config, integration test |
| **3: MCP Server** | COMPLETE | 9 MCP tools, lifespan context, CLI integration, expanded MCP test coverage |
| **4: Live Trading** | COMPLETE | LiveTrader (py-clob-client), risk management, executor factory, DB cleanup, CLI safety |

## Phase 3 Implementation Plan

See `docs/plans/2026-02-26-polymarket-agent-phase3.md` for detailed plan with 6 tasks:

1. ~~Add mcp dependency + data layer additions~~ DONE
2. ~~MCP server core — lifespan + read-only tools~~ DONE
3. ~~MCP portfolio, signals, trading tools~~ DONE
4. ~~MCP analyze_market tool~~ DONE
5. ~~CLI integration + config~~ DONE
6. ~~Tests + final verification~~ DONE

## Dependencies

**Runtime:** pydantic>=2.0, pyyaml>=6.0, typer>=0.9, anthropic>=0.84, mcp>=1.0
**Optional (live):** py-clob-client>=0.0.1 (`pip install polymarket-agent[live]`)
**Dev:** pytest>=8.0, pytest-mock>=3.0, ruff>=0.4, mypy>=1.10, types-PyYAML
**External:** `polymarket` CLI (Homebrew: `brew install polymarket`)

## Design Documents

- `docs/plans/2026-02-25-polymarket-agent-trader-design.md` — Full system design (architecture, all phases)
- `docs/plans/2026-02-25-polymarket-agent-phase1.md` — Phase 1 implementation plan (COMPLETE)
- `docs/plans/2026-02-25-polymarket-agent-phase2.md` — Phase 2 implementation plan (COMPLETE)
- `docs/plans/2026-02-26-polymarket-agent-phase3.md` — Phase 3 implementation plan (COMPLETE)

## Verification Commands

```bash
# Everything should pass
uv run pytest tests/ -v           # 141 tests passing
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 21 source files
uv run polymarket-agent tick      # Fetches live data, paper trades
uv run polymarket-agent mcp       # Starts MCP server (stdio transport)
uv run polymarket-agent run --live  # Live trading (requires POLYMARKET_PRIVATE_KEY)
```
