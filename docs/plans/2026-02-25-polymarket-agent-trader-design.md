# Polymarket Agent Trader — Design Document

Date: 2026-02-25

## Summary

A Python framework that wraps the Polymarket CLI into an agent-friendly auto-trading pipeline. Starts with paper trading, adds live execution later. Exposes market data and trading as MCP tools for Claude.

Inspired by Karpathy's thesis: CLIs are agent-native interfaces. The Polymarket CLI (`polymarket` v0.1.4) is the data backbone; this project builds strategy, execution, and agent layers on top.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  MCP Server                     │
│  (Claude / AI agents call structured tools)     │
└──────────────┬──────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────┐
│                Orchestrator                     │
│  - Schedules data polls & strategy runs         │
│  - Routes signals to strategies                 │
│  - Manages paper vs. live execution mode        │
└──────┬───────────┬──────────────┬───────────────┘
       │           │              │
┌──────▼──┐  ┌─────▼─────┐  ┌────▼──────┐
│  Data   │  │ Strategy  │  │ Execution │
│  Layer  │  │  Engine   │  │  Layer    │
│         │  │           │  │           │
│ CLI     │  │ Market    │  │ Paper     │
│ wrapper │  │ Making    │  │ Trader    │
│ (JSON)  │  │ Signals   │  │ Live      │
│         │  │ Arbitrage │  │ Trader    │
│ Caching │  │ AI/LLM    │  │ (py-clob) │
└─────────┘  └───────────┘  └───────────┘
```

## Data Layer

Wraps the `polymarket` CLI with `-o json` output. Provides a typed Python API.

### Interface

```python
class PolymarketData:
    get_active_markets(tag=None, limit=25) -> list[Market]
    search_markets(query: str) -> list[Market]
    get_event(event_id: str) -> Event
    get_price(token_id: str) -> Price
    get_orderbook(token_id: str) -> OrderBook
    get_spread(token_id: str) -> Spread
    get_price_history(token_id: str, interval="1d") -> list[PricePoint]
    get_leaderboard(period="month") -> list[Trader]
    get_volume(event_id: str) -> Volume
    get_positions(address: str) -> list[Position]
```

### Implementation

- `subprocess.run(["polymarket", ..., "-o", "json"])` for all data access
- Configurable TTL caching (prices: 10s, markets: 5min)
- Pydantic models for type safety
- Fallback to direct HTTP calls if CLI unavailable

### Data Models (Pydantic)

- `Market` — id, question, outcomes, prices, volume, liquidity, status
- `Event` — id, title, markets, tags, volume
- `Price` — token_id, price, timestamp
- `OrderBook` — bids, asks, spread, midpoint
- `Position` — market, outcome, shares, avg_price, current_price, pnl

## Strategy Engine

Pluggable strategy modules with a common interface.

### Interface

```python
class Strategy(ABC):
    name: str

    @abstractmethod
    def analyze(self, markets: list[Market], data: PolymarketData) -> list[Signal]:
        """Given market data, return trade signals."""

    def configure(self, config: dict) -> None:
        """Load strategy-specific parameters."""
```

### Signal

```python
@dataclass
class Signal:
    strategy: str        # which strategy generated this
    market_id: str
    token_id: str
    side: Literal["buy", "sell"]
    confidence: float    # 0-1
    target_price: float  # desired entry price
    size: float          # USDC amount
    reason: str          # human-readable explanation
```

### Strategy Modules

1. **MarketMaker** — places bid/ask orders around midpoint with configurable spread. Manages inventory limits. Adjusts spread based on volatility.

2. **SignalTrader** — monitors price velocity, volume spikes, order book imbalances. Generates buy/sell signals when thresholds are crossed. Configurable lookback windows.

3. **Arbitrageur** — compares prices across related markets within Polymarket (e.g., "by March" vs "by June" should be monotonically priced). Trades mispricings.

4. **AIAnalyst** — sends market descriptions + price data to Claude. LLM returns probability estimate. If estimate diverges significantly from market price, generates a signal. Rate-limited to control API costs.

### Signal Aggregation

When multiple strategies fire on the same market, the orchestrator combines signals (e.g., require 2+ strategies to agree, or weight by confidence).

## Execution Layer

Two executors with the same interface, swappable by config.

### Interface

```python
class Executor(ABC):
    def place_order(self, signal: Signal) -> Order: ...
    def cancel_order(self, order_id: str) -> bool: ...
    def get_open_orders(self) -> list[Order]: ...
    def get_portfolio(self) -> Portfolio: ...
```

### PaperTrader (Phase 1)

- Simulates order fills against real order book data
- Virtual USDC balance (configurable starting amount)
- All trades logged to SQLite
- Live P&L calculation against current prices
- No wallet needed

### LiveTrader (Phase 2)

- Uses `py-clob-client` for authenticated orders
- Same interface as PaperTrader
- Risk limits: max position size, max daily loss, max open orders
- Requires private key via `polymarket setup`

### Portfolio

```python
@dataclass
class Portfolio:
    balance: float
    positions: list[Position]
    total_value: float
    pnl: float
    trade_history: list[Trade]
```

## MCP Server

Exposes Polymarket as tools for AI agents.

| Tool | Description |
|------|-------------|
| `search_markets` | Search active markets by keyword or tag |
| `get_market_detail` | Full details: description, prices, volume, order book |
| `get_price_history` | Historical prices for a market |
| `analyze_market` | Run AI analyst, get probability estimate |
| `get_portfolio` | Current positions, P&L, balance |
| `get_signals` | Latest signals from all active strategies |
| `place_trade` | Execute a trade (paper or live, with confirmation) |
| `get_leaderboard` | Top traders by P&L or volume |

## Orchestrator

Main loop running at configurable intervals:

```
every N seconds (default 60s):
  1. Fetch latest market data via Data Layer
  2. Run each active Strategy -> collect Signals
  3. Aggregate/filter signals (confidence threshold, dedup)
  4. Auto mode: send signals to Executor
     Manual mode: log signals, wait for MCP/human approval
  5. Update portfolio state
  6. Log everything to SQLite
```

### Modes

- **monitor** — data collection and signal generation only, no trading
- **paper** — auto-executes signals against simulated portfolio
- **live** — auto-executes with real money (wallet + explicit opt-in required)
- **mcp** — runs MCP server, Claude decides what to trade

## Configuration

Single YAML file:

```yaml
mode: paper
starting_balance: 1000
poll_interval: 60

strategies:
  market_maker:
    enabled: false
    spread: 0.05
  signal_trader:
    enabled: true
    volume_threshold: 10000
  arbitrageur:
    enabled: true
  ai_analyst:
    enabled: true
    model: claude-sonnet-4-6
    max_calls_per_hour: 20

risk:
  max_position_size: 100
  max_daily_loss: 50
  max_open_orders: 10
```

## Tech Stack

- **Python 3.12+**
- **Polymarket CLI** (`polymarket` v0.1.4) — data backbone
- **py-clob-client** — live trade execution (Phase 2)
- **Pydantic** — data models and config validation
- **SQLite** — trade log and portfolio state
- **MCP SDK** (`mcp` Python package) — MCP server
- **Anthropic SDK** — AI analyst strategy
- **PyYAML** — config loading
- **Click or Typer** — CLI interface for the tool itself

## Project Structure

```
polymarket-agent/
├── config.yaml
├── pyproject.toml
├── src/
│   └── polymarket_agent/
│       ├── __init__.py
│       ├── cli.py              # CLI entry point
│       ├── config.py           # Config loading
│       ├── data/
│       │   ├── __init__.py
│       │   ├── client.py       # PolymarketData (CLI wrapper)
│       │   ├── cache.py        # TTL cache
│       │   └── models.py       # Pydantic models
│       ├── strategies/
│       │   ├── __init__.py
│       │   ├── base.py         # Strategy ABC + Signal
│       │   ├── market_maker.py
│       │   ├── signal_trader.py
│       │   ├── arbitrageur.py
│       │   └── ai_analyst.py
│       ├── execution/
│       │   ├── __init__.py
│       │   ├── base.py         # Executor ABC + Portfolio
│       │   ├── paper.py        # PaperTrader
│       │   └── live.py         # LiveTrader (Phase 2)
│       ├── orchestrator.py     # Main loop
│       ├── mcp_server.py       # MCP server
│       └── db.py               # SQLite operations
└── tests/
    └── ...
```

## Dependencies

- `polymarket` CLI (installed via Homebrew)
- `py-clob-client` (Phase 2, for live trading)
- `anthropic` (for AI analyst)
- `pydantic`
- `pyyaml`
- `typer`
- `mcp`
- `httpx` (fallback HTTP client)

## Phases

1. **Data Layer + Paper Trading** — CLI wrapper, models, paper executor, basic orchestrator
2. **Strategy Modules** — implement all four strategies, signal aggregation
3. **MCP Server** — expose tools for Claude
4. **Live Trading** — py-clob-client integration, wallet setup, risk management
