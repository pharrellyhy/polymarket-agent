# Implementation Plan: 4 Post-Design-Doc Features

## Context

All 4 phases of the original design doc are complete (142 tests, 14 MCP tools, 4 strategies, paper+live execution, risk management). This plan adds 4 new features sequentially: release/deploy, advanced order management, backtesting, and monitoring/dashboard.

## Implementation Order

1. **Release & Deploy** — foundational: version, CI, Docker. Validates all subsequent work.
2. **Advanced Order Management** — extends core trading pipeline (stop-loss, take-profit, Kelly sizing).
3. **Backtesting Framework** — replays historical data through strategies. Benefits from conditional orders.
4. **Monitoring & Dashboard** — observes everything. Most valuable once full feature set exists.

---

## Feature 1: Release & Deploy ✅ COMPLETED

### Tasks

**1.1 Version management + CLI --version** ✅
- Bumped `pyproject.toml` version `0.1.0` → `1.0.0`
- Added `__version__ = "1.0.0"` to `src/polymarket_agent/__init__.py`
- Added `--version` / `-V` eager callback to Typer app in `cli.py`
- Added classifiers, license, authors, urls to `pyproject.toml`

**1.2 Health check MCP tool** ✅
- Added `health_check()` tool to `mcp_server.py` returning version, mode, strategy count, status
- Test in `test_mcp_server.py`

**1.3 Dockerfile + docker-compose** ✅
- Created `Dockerfile` (python:3.12-slim, uv install, copy source)
- Created `docker-compose.yml` (SQLite volume, env_file, restart policy)
- Created `.dockerignore`

**1.4 systemd + env template** ✅
- Created `deploy/polymarket-agent.service`
- Created `deploy/env.example` documenting all env vars

**1.5 GitHub Actions CI** ✅
- Removed `.github/` from `.gitignore` (was line 316)
- Created `.github/workflows/ci.yml`: Python 3.12 matrix, uv sync, pytest, ruff check, ruff format --check, mypy

### Files created
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `deploy/polymarket-agent.service`
- `deploy/env.example`
- `.github/workflows/ci.yml`

### Files modified
- `pyproject.toml` — version 1.0.0, metadata, dashboard optional dep group
- `src/polymarket_agent/__init__.py` — `__version__`
- `src/polymarket_agent/cli.py` — `--version` callback
- `src/polymarket_agent/mcp_server.py` — `health_check` tool
- `.gitignore` — removed `.github/` and `deploy/` lines
- `tests/test_cli.py` — version flag tests
- `tests/test_mcp_server.py` — health check test

### Verification: 145 tests passing

---

## Feature 2: Advanced Order Management ✅ COMPLETED

### Tasks

**2.1 ConditionalOrder model + DB schema** ✅
- Create `src/polymarket_agent/orders.py` with:
  - `OrderType` enum: `stop_loss`, `take_profit`, `trailing_stop`
  - `OrderStatus` enum: `active`, `triggered`, `cancelled`
  - `ConditionalOrder` dataclass: token_id, market_id, order_type, status, trigger_price, size, high_watermark, trail_percent, parent_strategy, reason
- Add `conditional_orders` table to `db.py`:
  ```sql
  CREATE TABLE IF NOT EXISTS conditional_orders (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      triggered_at DATETIME,
      token_id TEXT NOT NULL,
      market_id TEXT NOT NULL,
      order_type TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'active',
      trigger_price REAL NOT NULL,
      size REAL NOT NULL,
      high_watermark REAL,
      trail_percent REAL,
      parent_strategy TEXT NOT NULL,
      reason TEXT NOT NULL
  )
  ```
- Add DB methods: `create_conditional_order()`, `get_active_conditional_orders()`, `update_conditional_order()`, `update_high_watermark()`

**2.2 Stop-loss + take-profit logic** ✅
- Add `check_conditional_orders()` to `orchestrator.py`:
  1. Get active conditional orders from DB
  2. Fetch current price for each token via `self._data.get_price(token_id)`
  3. Stop-loss: if bid <= trigger_price → create sell Signal, execute, mark triggered
  4. Take-profit: if bid >= trigger_price → create sell Signal, execute, mark triggered
- Call at start of `tick()`, before strategy analysis

**2.3 Trailing stop logic** ✅
- In `check_conditional_orders()`: for trailing stops, update high_watermark when price rises
- Trigger when price drops below `high_watermark * (1 - trail_percent)`

**2.4 Kelly criterion position sizing** ✅
- Create `src/polymarket_agent/position_sizing.py`:
  - `PositionSizer` class with methods: `kelly_size()`, `fractional_kelly_size()`, `fixed_size()`
  - Kelly formula: `f* = (bp - q) / b` where b=odds, p=confidence, q=1-p
  - `compute_size(signal, portfolio) -> float` returns clamped USDC size
- Integrate into `orchestrator.py`: wrap signal.size through sizer before execution

**2.5 Config + MCP integration** ✅
- Add to `config.py`:
  ```python
  class ConditionalOrderConfig(BaseModel):
      enabled: bool = False
      default_stop_loss_pct: float = 0.10
      default_take_profit_pct: float = 0.20
      trailing_stop_enabled: bool = False
      trailing_stop_pct: float = 0.05

  class PositionSizingConfig(BaseModel):
      method: Literal["fixed", "kelly", "fractional_kelly"] = "fixed"
      kelly_fraction: float = 0.25
      max_bet_pct: float = 0.10
  ```
- Add both to `AppConfig`
- Add MCP tools: `get_conditional_orders`, `cancel_conditional_order`, `create_conditional_order`
- Add optional `stop_loss: float | None = None` and `take_profit: float | None = None` to `Signal` dataclass

### Files to create
- `src/polymarket_agent/orders.py`
- `src/polymarket_agent/position_sizing.py`
- `tests/test_orders.py`
- `tests/test_position_sizing.py`

### Files to modify
- `src/polymarket_agent/db.py` — new table + 4 methods
- `src/polymarket_agent/orchestrator.py` — `check_conditional_orders()`, position sizer integration
- `src/polymarket_agent/config.py` — 2 new config models
- `src/polymarket_agent/strategies/base.py` — optional stop_loss/take_profit on Signal
- `src/polymarket_agent/mcp_server.py` — 3 new MCP tools
- `config.yaml` — new sections
- `tests/test_mcp_server.py` — conditional order MCP tests

### New deps: None
### New config: `conditional_orders:` + `position_sizing:` sections in config.yaml
### Estimated new tests: ~20

### Verification: 182 tests passing, ruff clean, mypy clean

---

## Feature 3: Backtesting Framework ✅ COMPLETED

### Tasks

**3.1 DataProvider protocol** ✅
- Created `src/polymarket_agent/data/provider.py` with `DataProvider` Protocol (4 methods: `get_active_markets`, `get_orderbook`, `get_price`, `get_price_history`)
- `PolymarketData` satisfies this via structural typing (no changes needed)
- Updated `orchestrator.py`: `self._data: DataProvider` type hint + optional `data_provider` param in `__init__()`
- Updated `Strategy.analyze()` type hint: `data: DataProvider` (from `Any`) in base + all 4 strategy implementations

**3.2 HistoricalDataProvider** ✅
- Created `src/polymarket_agent/backtest/historical.py`:
  - Loads CSV files with columns: timestamp, market_id, question, yes_price, volume, token_id
  - Maintains time cursor (current timestamp)
  - `get_active_markets()` returns markets at current time step
  - `get_orderbook()` synthesizes orderbook from price (bid = price - spread/2, ask = price + spread/2)
  - `advance(timestamp)` moves cursor forward
  - Handles malformed rows gracefully, multiple CSV files

**3.3 BacktestEngine** ✅
- Created `src/polymarket_agent/backtest/engine.py`:
  - Takes: config, strategy list, data provider
  - `run(start, end)` supports optional timestamp filtering
  - Uses `PaperTrader` for execution with temp DB in `TemporaryDirectory`
  - Loops through time steps, running strategy+aggregation+execution pipeline
  - Collects portfolio snapshots at each step
  - Returns `BacktestResult` with metrics, snapshots, and trade log

**3.4 Performance metrics** ✅
- Created `src/polymarket_agent/backtest/metrics.py`:
  - `BacktestMetrics` dataclass: total_return, sharpe_ratio, max_drawdown, win_rate, profit_factor, total_trades
  - `compute_metrics(trades, snapshots, initial_balance) -> BacktestMetrics`
  - Annualized Sharpe ratio, per-round-trip win/loss tracking

**3.5 CLI command + MCP tool** ✅
- Added `polymarket-agent backtest` command: positional `data_dir`, `--start`, `--end`, `--output`, `--trades`
- Added `run_backtest` MCP tool
- Updated README with backtest docs, project structure, roadmap

### Files created
- `src/polymarket_agent/data/provider.py`
- `src/polymarket_agent/backtest/__init__.py`
- `src/polymarket_agent/backtest/historical.py`
- `src/polymarket_agent/backtest/engine.py`
- `src/polymarket_agent/backtest/metrics.py`
- `tests/test_backtest_historical.py` — 13 tests
- `tests/test_backtest_engine.py` — 10 tests
- `tests/test_backtest_metrics.py` — 13 tests

### Files modified
- `src/polymarket_agent/orchestrator.py` — `data_provider` param, `DataProvider` type hints
- `src/polymarket_agent/strategies/base.py` — `data: DataProvider` type hint
- `src/polymarket_agent/strategies/signal_trader.py` — `DataProvider` type hint
- `src/polymarket_agent/strategies/market_maker.py` — `DataProvider` type hint
- `src/polymarket_agent/strategies/arbitrageur.py` — `DataProvider` type hint
- `src/polymarket_agent/strategies/ai_analyst.py` — `DataProvider` type hint
- `src/polymarket_agent/cli.py` — `backtest` command
- `src/polymarket_agent/mcp_server.py` — `run_backtest` tool + cast for data field
- `config.yaml` — `backtest:` section
- `README.md` — backtest docs, project structure, roadmap

### New deps: None (stdlib csv, math, tempfile)
### New config: `backtest: { default_spread: 0.02, snapshot_interval: 86400 }`

### Verification: 217 tests passing, ruff clean, mypy clean

---

## Feature 4: Monitoring & Dashboard ✅ COMPLETED

### Tasks

**4.1 Structured JSON logging** ✅
- Created `src/polymarket_agent/monitoring/logging.py`:
  - `JSONFormatter` for stdlib logging — outputs single-line JSON with timestamp, level, logger, message, exception, extra_data
  - `setup_structured_logging(log_file: Path | None)` — configures root logger with console + optional file handler
- Integrated via `_setup_logging()` helper in CLI `run` and `tick` commands
- Controlled by `monitoring.structured_logging` and `monitoring.log_file` config

**4.2 Signal + portfolio snapshot DB tables** ✅
- Added `signal_log` table to `db.py` (id, timestamp, strategy, market_id, token_id, side, confidence, size, status)
- Added `portfolio_snapshots` table (id, timestamp, balance, total_value, positions_json)
- Added DB methods: `record_signal()`, `get_signal_log(strategy, limit)`, `record_portfolio_snapshot()`, `get_portfolio_snapshots(limit)`
- Enabled `check_same_thread=False` on SQLite connection for FastAPI thread pool compatibility

**4.3 Alert hook system** ✅
- Created `src/polymarket_agent/monitoring/alerts.py`:
  - `AlertSink` ABC with `send(message)` method
  - `ConsoleAlertSink` — logs alerts via `logger.warning`
  - `WebhookAlertSink` — POSTs JSON to a URL via stdlib `urllib.request`
  - `AlertManager` — dispatches to registered sinks, continues on individual sink failure
- Hooked into orchestrator: fires alerts on trade execution with strategy/side/size/market details
- `_build_alert_manager()` auto-registers ConsoleAlertSink + any webhook URLs from config

**4.4 HTTP API (FastAPI)** ✅
- Created `src/polymarket_agent/dashboard/api.py`:
  - `create_app(db, get_portfolio, get_recent_trades)` factory function
  - `GET /api/health` — version + status
  - `GET /api/portfolio` — balance, total_value, positions
  - `GET /api/trades?limit=50` — recent trades from DB
  - `GET /api/signals?strategy=X&limit=100` — signal log with optional strategy filter
  - `GET /api/snapshots?limit=100` — portfolio snapshots with parsed positions JSON
  - `GET /` — serves static dashboard HTML
- Optional deps: `fastapi>=0.110`, `uvicorn>=0.29` (already in pyproject.toml `[dashboard]` group)

**4.5 Static HTML dashboard** ✅
- Created `src/polymarket_agent/dashboard/static/dashboard.html`:
  - Dark theme single-page app with Chart.js (CDN) for P&L chart
  - Cards: balance, total value, positions count, signal count
  - Portfolio value line chart from snapshots
  - Recent trades table with buy/sell badges
  - Recent signals table with strategy, confidence, status
  - Auto-refreshes every 15 seconds

**4.6 CLI command + MCP tools** ✅
- Added `polymarket-agent dashboard [--host] [--port]` command with uvicorn server
- Added 3 MCP tools:
  - `get_signal_log(strategy, limit)` — logged signals from DB
  - `get_portfolio_snapshots(limit)` — portfolio value snapshots
  - `get_strategy_performance()` — per-strategy summary (signals generated, executed, trade volume)
- Orchestrator integration:
  - `_record_signal()` logs every signal (generated/executed/rejected) to signal_log table
  - `_record_portfolio_snapshot()` saves balance + positions after each tick
  - Alert fires on every trade execution
- `MonitoringConfig` already existed in `config.py` from Feature 2 scaffolding
- Added `monitoring:` section to `config.yaml`

### Files created
- `src/polymarket_agent/monitoring/__init__.py`
- `src/polymarket_agent/monitoring/logging.py`
- `src/polymarket_agent/monitoring/alerts.py`
- `src/polymarket_agent/dashboard/__init__.py`
- `src/polymarket_agent/dashboard/api.py`
- `src/polymarket_agent/dashboard/static/dashboard.html`
- `tests/test_monitoring_logging.py` — 8 tests
- `tests/test_monitoring_alerts.py` — 7 tests
- `tests/test_monitoring_db.py` — 8 tests
- `tests/test_dashboard_api.py` — 9 tests

### Files modified
- `src/polymarket_agent/db.py` — 2 new tables + 4 methods, `check_same_thread=False`
- `src/polymarket_agent/orchestrator.py` — signal logging, portfolio snapshots, alert hooks, `_build_alert_manager()`
- `src/polymarket_agent/cli.py` — `dashboard` command, `_setup_logging()` helper
- `src/polymarket_agent/mcp_server.py` — 3 new MCP tools
- `config.yaml` — `monitoring:` section
- `tests/test_mcp_server.py` — monitoring MCP tool tests

### New deps: `fastapi>=0.110`, `uvicorn>=0.29` (optional group `dashboard`, already in pyproject.toml)
### New config: `monitoring: { structured_logging, log_file, alert_webhooks, snapshot_interval, dashboard_host, dashboard_port }`

### Verification: 255 tests passing, ruff clean, mypy clean

---

## Summary

| Feature | New files | Modified files | New tests | New MCP tools | Status |
|---------|-----------|----------------|-----------|---------------|--------|
| Release & Deploy | 6 | 7 | 3 | 1 (health_check) | ✅ Done |
| Order Management | 4 | 7 | 37 | 3 | ✅ Done |
| Backtesting | 8 | 10 | 35 | 1 (run_backtest) | ✅ Done |
| Monitoring | 10 | 6 | 38 | 3 | ✅ Done |
| **Total** | **28** | — | **113** | **8** | |

Final test count: **255 tests** passing
Final MCP tool count: **22 tools**
