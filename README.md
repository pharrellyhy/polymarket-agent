# Polymarket Agent

Agent-friendly auto-trading pipeline for Polymarket prediction markets.

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![Status](https://img.shields.io/badge/v1.0.0-green)

## Features

- **Typed Python API** — wraps the Polymarket CLI into Pydantic v2 models (Market, Event, OrderBook, Price)
- **Pluggable strategy engine** — SignalTrader, MarketMaker, Arbitrageur, TechnicalAnalyst, and AIAnalyst (Anthropic/OpenAI) modules
- **Technical analysis** — EMA crossover, RSI/Stochastic RSI, Bollinger Band squeeze detection from price history
- **News intelligence** — Google News RSS (free) or Tavily search for real-world context, with TTL caching and rate limiting
- **Paper trading** — simulated order fills against real order-book data, logged to SQLite
- **Live trading** — real order placement via py-clob-client with private key signing
- **MCP server** — 22 tools for AI agent integration (market data, trading, signals, conditional orders, backtesting, monitoring)
- **Conditional orders** — stop-loss, take-profit, and trailing stop with auto-creation
- **Position sizing** — Kelly criterion, fractional Kelly, and fixed sizing methods
- **Backtesting** — historical CSV replay, performance metrics (Sharpe, drawdown, win rate, profit factor)
- **Automated strategy tuning** — periodic evaluation + LLM-driven config adjustments (Anthropic, OpenAI, or local endpoints) with config hot-reload
- **Monitoring & Dashboard** — structured JSON logging, alert webhooks, signal/portfolio tracking, web dashboard
- **CLI interface** — `run`, `tick`, `status`, `report`, `evaluate`, `autotune`, `backtest`, `dashboard`, `mcp` commands
- **TTL cache** — in-memory per-key cache with configurable expiration on market data
- **YAML configuration** — mode selection, strategy params, risk limits, and order management in `config.yaml`
- **Signal aggregation** — deduplication, confidence filtering, and cross-strategy consensus

## Architecture

```mermaid
flowchart LR
    CLI["polymarket CLI<br/>(Homebrew)"]
    Data["PolymarketData<br/>+ TTLCache"]
    News["News Provider<br/>(Google RSS / Tavily)"]
    Strat["Strategy Engine<br/>(Multiple Strategies)"]
    Exec["Executor<br/>(PaperTrader)"]
    DB[(SQLite)]
    Orch["Orchestrator"]
    Config["config.yaml"]
    Tune["Auto-Tune<br/>(launchd, 6h)<br/>Anthropic / OpenAI"]

    CLI -->|JSON| Data
    Orch --> Data
    Orch --> Strat
    Orch --> News
    Orch --> Exec
    Data --> Strat
    News -->|Headlines| Strat
    Strat -->|Signals| Exec
    Exec --> DB
    Config -->|hot-reload| Orch
    Tune -->|evaluate + edit| Config
```

The **Orchestrator** drives a fetch → analyze → execute cycle each tick, hot-reloading `config.yaml` when it detects changes. The **Data Layer** shells out to the `polymarket` CLI with `-o json` and parses responses into Pydantic models. **Strategies** consume market data and emit `Signal` objects — the TechnicalAnalyst uses price history for indicator-based signals, while the AIAnalyst enriches its LLM prompt with both technical analysis and recent news headlines. The **News Provider** fetches headlines via Google News RSS (free) or Tavily, with caching and rate limiting. The **Executor** fills orders (paper or live) and persists trades to SQLite. The **Auto-Tune** loop periodically evaluates performance and uses an LLM (Anthropic, OpenAI, or local endpoints) to adjust strategy parameters.

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [Polymarket CLI](https://github.com/Polymarket/polymarket-cli) (`brew install polymarket`)

### Install & run

```bash
git clone https://github.com/<your-username>/polymarket-agent.git
cd polymarket-agent
uv sync
```

Run a single trading tick:

```bash
uv run polymarket-agent tick
```

```
Markets: 42, Signals: 3, Trades: 2
Portfolio: $985.50 cash, $1002.30 total
```

## Usage

### Core Trading Commands

```bash
# Continuous trading loop (polls every 60s by default)
uv run polymarket-agent run

# Single fetch-analyze-execute cycle
uv run polymarket-agent tick

# Portfolio snapshot
uv run polymarket-agent status
```

All commands accept `--config` / `-c` (path to config file) and `--db` (path to SQLite database).

### Live Trading

Live mode places real orders on Polymarket via [`py-clob-client`](https://github.com/Polymarket/py-clob-client). It requires an Ethereum private key and explicit opt-in via the `--live` flag.

```bash
# Install the live trading dependency
pip install polymarket-agent[live]

# Set config to live mode
# config.yaml: mode: live

# Run with explicit --live flag (required safety gate)
export POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY
uv run polymarket-agent run --live
uv run polymarket-agent tick --live
```

Orders are placed as GTC (Good-Til-Cancelled) limit orders on the CLOB. If you use a Magic/proxy wallet, also set `POLYMARKET_FUNDER`.

### Strategies

Five pluggable strategies are available, each enabled independently in `config.yaml`:

**SignalTrader** — directional signals based on volume and price deviation from 0.50.

```yaml
strategies:
  signal_trader:
    enabled: true
    volume_threshold: 10000   # min 24h volume to consider a market
    price_move_threshold: 0.05 # min distance from 0.50 to emit a signal
```

Buys when the Yes price is below midpoint by more than the threshold, sells when above. Signal size is 1% of 24h volume. Confidence scales linearly with distance from midpoint.

**MarketMaker** — quotes bid/ask around the order book midpoint.

```yaml
strategies:
  market_maker:
    enabled: false
    spread: 0.05        # total spread width
    min_liquidity: 1000  # skip illiquid markets
    order_size: 50       # USDC per side
```

Fetches the live order book for each market, then emits a buy signal at `midpoint - spread/2` and a sell signal at `midpoint + spread/2`. Skips markets below `min_liquidity`.

**Arbitrageur** — exploits pricing inconsistencies (complementary outcome prices should sum to ~1.0).

```yaml
strategies:
  arbitrageur:
    enabled: true
    price_sum_tolerance: 0.02  # ignore deviations below this
    min_deviation: 0.03        # minimum deviation to trade
    order_size: 25             # USDC per trade
```

When the sum of outcome prices deviates from 1.0 beyond tolerance, buys the underpriced side (if sum < 1.0) or sells the overpriced side (if sum > 1.0). Confidence scales with deviation magnitude.

**TechnicalAnalyst** — rule-based signals from price history indicators (EMA crossover, RSI, Bollinger squeeze).

```yaml
strategies:
  technical_analyst:
    enabled: true
    ema_fast_period: 8       # fast EMA period (shorter than stock 12 — prediction markets are shorter-lived)
    ema_slow_period: 21      # slow EMA period
    rsi_period: 14           # RSI lookback period
    history_interval: "1w"   # price history lookback window
    history_fidelity: 60     # data points per interval
    order_size: 25.0         # USDC per trade
```

Fetches price history for each market and computes EMA crossover, RSI (with Stochastic RSI), and Bollinger Band squeeze indicators. Generates buy signals on bullish EMA crossover with RSI not overbought and squeeze confirmation; sell signals on bearish crossover with RSI not oversold. Confidence is a weighted blend: EMA divergence (40%), RSI extremity (30%), squeeze confirmation (30%). Skips markets with fewer than 21 data points or prices near 0 or 1.

**AIAnalyst** — uses an LLM to estimate market probabilities, enriched with technical analysis and news context.

```yaml
strategies:
  ai_analyst:
    enabled: false
    model: claude-sonnet-4-6
    max_calls_per_hour: 20   # rate limit
    min_divergence: 0.15     # min difference between AI estimate and market price
    order_size: 25.0         # USDC per trade
    # provider: anthropic    # anthropic (default) or openai
    # base_url: http://localhost:11434/v1  # for local/custom endpoints
    # api_key_env: OPENAI_API_KEY          # env var name for API key
```

Sends each market's question and description to the configured LLM provider, parses a probability from the response. If the estimate diverges from the market price by more than `min_divergence`, generates a buy or sell signal. The prompt is enriched with optional context when available:

- **Technical analysis** — price trend, EMA crossover direction, RSI reading, and volatility state (computed from price history)
- **Recent news** — up to 5 headlines relevant to the market question (fetched via the configured news provider)

Both sections are optional — the strategy gracefully degrades if price history or news is unavailable. Supports both Anthropic (default) and OpenAI-compatible providers, including local models served via Ollama or vLLM. Requires the appropriate API key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`). Gracefully disabled when the key is not set.

**Signal Aggregation** — all strategy signals pass through an aggregation step before execution:

```yaml
aggregation:
  min_confidence: 0.3    # drop signals below this threshold
  min_strategies: 2      # require N strategies to agree on a market (e.g. AI + Technical)
```

Signals are deduplicated per market, filtered by confidence, and optionally require cross-strategy consensus. With `min_strategies: 2`, a trade only executes when multiple strategies independently agree (e.g. both AIAnalyst and TechnicalAnalyst signal the same direction).

### Risk Management

Every signal passes through a risk gate before execution:

```yaml
risk:
  max_position_size: 100.0  # max USDC per single trade
  max_daily_loss: 50.0      # stop trading after this daily loss
  max_open_orders: 10       # max concurrent open orders (live mode)
```

The risk gate runs once per tick and reuses a snapshot of daily loss and open order count across all signals in that tick. Rejected signals are logged but not executed.

### Conditional Orders

Automatic stop-loss, take-profit, and trailing stop orders that trigger when price conditions are met:

```yaml
conditional_orders:
  enabled: false
  default_stop_loss_pct: 0.10    # trigger sell if price drops 10%
  default_take_profit_pct: 0.20  # trigger sell if price rises 20%
  trailing_stop_enabled: false
  trailing_stop_pct: 0.05        # trail 5% below the high watermark
```

When enabled, the orchestrator automatically creates stop-loss and take-profit orders for every buy trade. Conditional orders are checked at the start of each tick before strategy analysis:

- **Stop-loss**: sells if bid drops to or below the trigger price
- **Take-profit**: sells if bid rises to or at the trigger price
- **Trailing stop**: tracks the highest price (high watermark) and sells if price drops below `high_watermark * (1 - trail_percent)`

Signals can also carry explicit `stop_loss` and `take_profit` prices that override the config defaults.

Conditional orders can be managed via MCP tools:
- `get_conditional_orders` — list all active conditional orders
- `cancel_conditional_order(order_id)` — cancel a specific order
- `create_conditional_order(...)` — manually create a conditional order

### Position Sizing

Three sizing methods control how much USDC is allocated per trade:

```yaml
position_sizing:
  method: fixed               # fixed | kelly | fractional_kelly
  kelly_fraction: 0.25        # fraction of full Kelly (fractional_kelly only)
  max_bet_pct: 0.10           # max % of portfolio per bet (kelly/fractional_kelly)
```

- **fixed** — uses the signal's original size (from the strategy)
- **kelly** — full Kelly criterion: `f* = (bp - q) / b` where `b = (1/price) - 1`, `p = confidence`, `q = 1 - p`. Capped at `max_bet_pct` of portfolio value
- **fractional_kelly** — `kelly_fraction * full Kelly`, also capped at `max_bet_pct`

The sizer runs after aggregation and before risk checks. It never increases beyond the signal's original size.

### Backtesting

Replay historical market data through the configured strategies to evaluate performance:

```bash
# Run backtest over a directory of CSV files
uv run polymarket-agent backtest data/sample/

# Filter by date range
uv run polymarket-agent backtest data/sample/ --start 2024-01-01 --end 2024-03-01

# Export results to JSON (optionally include individual trades)
uv run polymarket-agent backtest data/sample/ --output results.json --trades
```

**CSV format** — each file must have columns: `timestamp`, `market_id`, `question`, `yes_price`, `volume`, `token_id`. Multiple CSV files in the directory are merged.

```csv
timestamp,market_id,question,yes_price,volume,token_id
2024-01-01T00:00:00Z,abc123,Will it rain?,0.65,50000,0xtok1
2024-01-02T00:00:00Z,abc123,Will it rain?,0.70,55000,0xtok1
```

The engine replays data chronologically, running strategies at each time step and executing through a temporary PaperTrader. Output includes:

| Metric | Description |
|--------|-------------|
| Total return | Percentage gain/loss over the period |
| Sharpe ratio | Annualized risk-adjusted return |
| Max drawdown | Largest peak-to-trough decline |
| Win rate | Percentage of profitable round-trip trades |
| Profit factor | Gross profit / gross loss |
| Total trades | Number of trades executed |

```yaml
backtest:
  default_spread: 0.02      # synthetic orderbook spread for historical data
  snapshot_interval: 86400   # seconds between portfolio snapshots
```

The `run_backtest` MCP tool provides the same functionality for AI agents.

### Monitoring & Dashboard

Real-time observability into the agent's behavior with structured logging, alerts, and a web dashboard.

![Dashboard Screenshot](docs/dashboard-screenshot.png)

#### Structured Logging

Enable JSON-formatted log output for machine-readable log aggregation:

```yaml
monitoring:
  structured_logging: true
  log_file: logs/agent.log   # optional file output (in addition to console)
```

Each log line is a single JSON object:

```json
{"timestamp": "2026-02-27T00:30:00+00:00", "level": "INFO", "logger": "polymarket_agent.orchestrator", "message": "Executed 2 trades (mode=paper)"}
```

#### Alert Webhooks

Get notified on every trade execution via webhook (Slack, Discord, etc.):

```yaml
monitoring:
  alert_webhooks:
    - https://hooks.slack.com/services/T00/B00/XXXX
    - https://discord.com/api/webhooks/XXXX/XXXX
```

Alerts are also always logged to the console via the built-in `ConsoleAlertSink`. Each webhook receives a JSON POST with `{"text": "Trade executed: buy 25.00 USDC on abc123 (signal_trader)"}`.

#### Signal & Portfolio Tracking

The orchestrator automatically logs every signal and portfolio snapshot to SQLite:

- **Signal log** — records every signal with strategy, market, side, confidence, size, and status (`generated`, `executed`, `rejected`)
- **Portfolio snapshots** — records balance, total value, and positions at configurable intervals

```yaml
monitoring:
  snapshot_interval: 300   # seconds between portfolio snapshots (default 5 min)
```

Query via MCP tools:
- `get_signal_log(strategy?, limit?)` — retrieve logged signals, optionally filtered by strategy
- `get_portfolio_snapshots(limit?)` — retrieve portfolio value history
- `get_strategy_performance()` — per-strategy summary: signals generated, executed, and trade volume

#### Web Dashboard

A single-page web dashboard with real-time charts and tables:

```bash
# Install optional dependencies
pip install polymarket-agent[dashboard]

# Start the dashboard (defaults from config.yaml)
uv run polymarket-agent dashboard

# Override host/port via CLI flags
uv run polymarket-agent dashboard --host 127.0.0.1 --port 3000
```

```yaml
monitoring:
  dashboard_host: "0.0.0.0"  # bind address (used when --host not provided)
  dashboard_port: 8080        # port (used when --port not provided)
```

The dashboard includes:
- **Portfolio cards** — balance, total value, open positions, recent signal count
- **P&L chart** — portfolio value over time (Chart.js line chart, auto-refreshes every 15s)
- **Open positions** — per-position P&L with entry price, current price, unrealized gains, and P&L %
- **Strategy performance** — per-strategy breakdown with trade count, win rate, net P&L, and signal count
- **Trades table** — recent trades with strategy, market, side, price, size
- **Signals table** — recent signals with strategy, confidence, execution status
- **Conditional orders** — all orders (active, triggered, cancelled) with type badges and trigger details
- **Config change history** — config hot-reload diffs showing old/new values per changed field

**REST API endpoints** (all return JSON):

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Agent version and status |
| `GET /api/portfolio` | Current balance, total value, positions |
| `GET /api/positions` | Per-position P&L (entry price, current price, unrealized P&L) |
| `GET /api/trades?limit=50` | Recent trades |
| `GET /api/signals?strategy=X&limit=100` | Signal log with optional strategy filter |
| `GET /api/snapshots?limit=100` | Portfolio value snapshots over time |
| `GET /api/strategy-performance` | Per-strategy trade count, win rate, net P&L, signal count |
| `GET /api/conditional-orders?limit=50` | All conditional orders (active, triggered, cancelled) |
| `GET /api/config-changes?limit=20` | Config hot-reload diff history |

### News Provider

The news provider fetches recent headlines to give the AIAnalyst real-world context for each market question. Two providers are available:

```yaml
news:
  enabled: true
  provider: google_rss        # google_rss (free, no API key) or tavily (1k free/month)
  api_key_env: TAVILY_API_KEY  # only needed for tavily provider
  max_calls_per_hour: 50      # rate limit across all queries
  cache_ttl: 900              # cache headlines for 15 minutes
  max_results: 5              # headlines per market
```

- **Google RSS** (default) — parses Google News RSS feeds. Free, no authentication, no API key needed. Uses the `feedparser` library.
- **Tavily** — structured, LLM-optimized search results via the Tavily API. Requires `TAVILY_API_KEY` and the `tavily-python` package (`pip install polymarket-agent[tavily]`). 1,000 free searches per month.

All queries are cached (default 15 minutes) and rate-limited. When the rate limit is exhausted or the provider is unavailable, the AIAnalyst prompt simply omits the news section and continues without it.

### Performance Reporting

Review trading performance with built-in analytics:

```bash
# Human-readable performance report
uv run polymarket-agent report --period 24h

# Machine-readable JSON report
uv run polymarket-agent report --period 7d --json
```

The `evaluate` command produces structured JSON designed for automated consumption:

```bash
# Structured evaluation for auto-tuning
uv run polymarket-agent evaluate --period 24h
```

The evaluate output includes: metrics (return, Sharpe, drawdown, win rate), per-strategy breakdown, trade analysis, current config, tunable parameters with min/max ranges, safety constraints, and a diagnostic summary.

### Auto-Tuning

The auto-tune pipeline periodically evaluates trading performance and uses an LLM to decide whether to adjust `config.yaml`. Supports three provider modes: Claude Code CLI (`claude -p`), direct Anthropic API, or any OpenAI-compatible endpoint. The trading loop hot-reloads config changes without restarting.

```
┌────────────────────────────────────┐
│ Trading Loop (tmux, 24/7)          │
│ polymarket-agent run               │
│  - each tick: check config mtime   │
│  - if changed: reload_config()     │
└──────────────────┬─────────────────┘
                   │ reads config.yaml
┌──────────────────┴─────────────────┐
│ config.yaml                        │
│  (edited by LLM tuner)             │
└──────────────────┬─────────────────┘
                   │ writes
┌──────────────────┴─────────────────┐
│ Auto-Tune (launchd, every 6h)      │
│ scripts/autotune.sh                │
│  provider=claude → claude -p       │
│  provider=openai → autotune CLI    │
│  provider=anthropic → autotune CLI │
└────────────────────────────────────┘
```

**Config hot-reload** — the `run` loop checks the config file's modification time before each tick. When a change is detected, the orchestrator computes a diff of changed fields, persists it to the `config_changes` table (viewable in the dashboard and via `/api/config-changes`), then rebuilds strategies, position sizer, and alert manager from the new config. The executor is preserved so positions remain in memory. Mode changes (e.g. paper → live) are rejected for safety.

**Setup auto-tuning:**

```bash
# Run manually (default: Claude Code CLI)
bash scripts/autotune.sh

# Use OpenAI-compatible provider instead
AUTOTUNE_PROVIDER=openai AUTOTUNE_MODEL=gpt-4o bash scripts/autotune.sh

# Use Anthropic API directly (no Claude Code CLI needed)
AUTOTUNE_PROVIDER=anthropic AUTOTUNE_MODEL=claude-sonnet-4-6 bash scripts/autotune.sh

# Or run the CLI subcommand directly
uv run polymarket-agent autotune --provider openai --model gpt-4o --period 6h

# Schedule via macOS launchd (every 6 hours)
cp scripts/com.polymarket-agent.autotune.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.polymarket-agent.autotune.plist
```

**Environment variables** for `autotune.sh`:

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOTUNE_PROVIDER` | `claude` | `claude` (Claude Code CLI), `openai`, or `anthropic` |
| `AUTOTUNE_MODEL` | — | Required for `openai`/`anthropic` providers (e.g. `gpt-4o`) |
| `AUTOTUNE_PERIOD` | `6h` | Evaluation lookback period |
| `AUTOTUNE_BASE_URL` | — | Optional API base URL for OpenAI-compatible endpoints |
| `AUTOTUNE_API_KEY_ENV` | — | Optional override for API key env var name |

**Tuning rules** enforced by the auto-tune prompt:
- Never changes the `mode` field
- Adjusts at most 2–3 parameters per session
- Respects min/max ranges defined in tunable parameters
- Makes no changes if performance is acceptable (positive return, Sharpe > 0.5, win rate > 45%)

Logs are written to `logs/autotune/autotune-YYYYMMDD-HHMMSS.log`.

### MCP Server (AI Agent Integration)

The MCP server exposes 22 tools via stdio transport for AI agents:

```bash
uv run polymarket-agent mcp
```

| Category | Tools |
|----------|-------|
| **Market data** | `search_markets`, `get_market_detail`, `get_price_history`, `get_event`, `get_price`, `get_spread`, `get_volume`, `get_positions`, `get_leaderboard` |
| **Trading** | `get_portfolio`, `get_signals`, `refresh_signals`, `place_trade`, `analyze_market` |
| **Order management** | `get_conditional_orders`, `cancel_conditional_order`, `create_conditional_order` |
| **Backtesting** | `run_backtest` |
| **Monitoring** | `get_signal_log`, `get_portfolio_snapshots`, `get_strategy_performance` |
| **System** | `health_check` |

See `deploy/env.example` for all available environment variables.

### Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `POLYMARKET_PRIVATE_KEY` | Wallet private key for live trading | Live mode only |
| `POLYMARKET_FUNDER` | Funder address for Magic/proxy wallets | Optional (live mode) |
| `ANTHROPIC_API_KEY` | API key for AIAnalyst / autotune (Anthropic provider) | AI features only |
| `OPENAI_API_KEY` | API key for AIAnalyst / autotune (OpenAI provider) | OpenAI provider only |
| `TAVILY_API_KEY` | API key for Tavily news provider | Tavily news only |

See [`deploy/env.example`](deploy/env.example) for all available variables.

### Full Configuration Reference

```yaml
mode: paper              # monitor | paper | live
starting_balance: 1000.0
poll_interval: 60        # seconds between ticks in continuous mode

strategies:
  signal_trader:
    enabled: true
    volume_threshold: 10000
    price_move_threshold: 0.05
  market_maker:
    enabled: false
    spread: 0.05
    min_liquidity: 1000
    order_size: 50
  arbitrageur:
    enabled: true
    price_sum_tolerance: 0.02
    min_deviation: 0.03
    order_size: 25
  ai_analyst:
    enabled: false
    model: claude-sonnet-4-6
    max_calls_per_hour: 20
    min_divergence: 0.15
    order_size: 25.0
    # provider: anthropic       # anthropic (default) or openai
    # base_url: null             # for local/custom OpenAI-compatible endpoints
    # api_key_env: null          # override env var name for API key
  technical_analyst:
    enabled: true
    ema_fast_period: 8
    ema_slow_period: 21
    rsi_period: 14
    history_interval: "1w"
    history_fidelity: 60
    order_size: 25.0

aggregation:
  min_confidence: 0.3
  min_strategies: 2

risk:
  max_position_size: 100.0
  max_daily_loss: 50.0
  max_open_orders: 10

conditional_orders:
  enabled: false
  default_stop_loss_pct: 0.10
  default_take_profit_pct: 0.20
  trailing_stop_enabled: false
  trailing_stop_pct: 0.05

position_sizing:
  method: fixed           # fixed | kelly | fractional_kelly
  kelly_fraction: 0.25
  max_bet_pct: 0.10

backtest:
  default_spread: 0.02
  snapshot_interval: 86400

news:
  enabled: true
  provider: google_rss       # google_rss (free) or tavily (1k free/month)
  api_key_env: TAVILY_API_KEY # only needed for tavily
  max_calls_per_hour: 50
  cache_ttl: 900             # 15 minutes
  max_results: 5

monitoring:
  structured_logging: false
  log_file: null
  alert_webhooks: []
  snapshot_interval: 300
  dashboard_host: "0.0.0.0"
  dashboard_port: 8080
```

## Project Structure

```
scripts/
├── autotune.sh                    # Auto-tune cron script (evaluate → Claude Code → config edit)
└── com.polymarket-agent.autotune.plist  # macOS launchd schedule (every 6h)

src/polymarket_agent/
├── cli.py                  # Typer CLI entry point (run, tick, status, report, evaluate, autotune, …)
├── config.py               # Pydantic config loading from YAML + config_mtime()
├── orchestrator.py          # Main loop + config hot-reload
├── autotune.py             # LLM-based config auto-tuner (Anthropic/OpenAI)
├── db.py                   # SQLite persistence
├── orders.py               # Conditional order models
├── position_sizing.py      # Kelly criterion position sizing
├── mcp_server.py           # MCP server (22 tools)
├── data/
│   ├── client.py           # CLI wrapper (subprocess + JSON parsing)
│   ├── models.py           # Pydantic models (Market, Event, OrderBook, …)
│   ├── provider.py         # DataProvider protocol
│   └── cache.py            # In-memory TTL cache
├── news/
│   ├── models.py           # NewsItem model
│   ├── provider.py         # NewsProvider protocol
│   ├── google_rss.py       # Google News RSS provider (free)
│   ├── tavily_client.py    # Tavily search provider (optional)
│   └── cached.py           # Cached + rate-limited wrapper
├── strategies/
│   ├── base.py             # Strategy ABC + Signal dataclass
│   ├── signal_trader.py    # Volume/price-move signal strategy
│   ├── market_maker.py     # Bid/ask quoting around midpoint
│   ├── arbitrageur.py      # Price-sum deviation strategy
│   ├── ai_analyst.py       # LLM-based probability strategy with TA + news enrichment
│   ├── technical_analyst.py # Rule-based TA strategy (EMA, RSI, squeeze)
│   ├── indicators.py       # Technical indicator computations (EMA, RSI, Bollinger)
│   └── aggregator.py       # Signal dedup/filter/consensus
├── backtest/
│   ├── historical.py       # CSV-based historical data provider
│   ├── engine.py           # Backtest engine (replay + execute)
│   └── metrics.py          # Performance metrics (Sharpe, drawdown, etc.)
├── monitoring/
│   ├── logging.py          # Structured JSON logging
│   └── alerts.py           # Alert sinks (console, webhook)
├── dashboard/
│   ├── api.py              # FastAPI HTTP API
│   └── static/dashboard.html  # Web dashboard UI
└── execution/
    ├── base.py             # Executor ABC + Portfolio/Order models
    ├── paper.py            # Simulated fills with SQLite logging
    └── live.py             # Real order placement via py-clob-client
```

## Development

```bash
# Run tests
uv run pytest tests/ -v

# Run tests with coverage
uv run pytest tests/ -v --cov=src/polymarket_agent

# Lint
ruff check src/

# Format
ruff format src/

# Type check (strict mode)
mypy src/
```

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| **1. Core Pipeline** | Done | CLI wrapper, data models, signal strategy, paper trading, orchestrator |
| **2. Advanced Strategies** | Done | MarketMaker, Arbitrageur, AIAnalyst, signal aggregation |
| **3. MCP Server** | Done | 22 MCP tools for AI agent integration |
| **4. Live Trading** | Done | Real order placement via py-clob-client, risk management |
| **5. Release & Deploy** | Done | v1.0.0, CI/CD, Docker, systemd |
| **6. Order Management** | Done | Stop-loss, take-profit, trailing stop, Kelly sizing |
| **7. Backtesting** | Done | Historical data replay, performance metrics, DataProvider protocol |
| **8. Monitoring** | Done | Dashboard, structured logging, alerts, signal/portfolio tracking |
| **9. Auto-Tuning** | Done | Config hot-reload, evaluate command, multi-provider LLM-driven parameter tuning |
| **10. TA + News** | Done | Technical indicators (EMA, RSI, squeeze), news headlines, TechnicalAnalyst strategy, AI prompt enrichment |

## Tech Stack

Python 3.12 · Pydantic v2 · Typer · SQLite · PyYAML · Anthropic SDK · OpenAI SDK (optional) · feedparser · Tavily (optional) · FastAPI · Chart.js · ruff · mypy · pytest
