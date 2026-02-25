# Polymarket Agent — Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the data layer (CLI wrapper + Pydantic models), paper trading executor, SQLite persistence, orchestrator loop, and a minimal CLI — enough to run strategies against a simulated portfolio using live Polymarket data.

**Architecture:** Python package using `uv` for project management. The data layer shells out to `polymarket` CLI with `-o json` and parses results into Pydantic models with TTL caching. The paper trader simulates fills against real order book data. The orchestrator polls data and runs strategies on a configurable interval. A basic signal trader strategy is included to prove the pipeline end-to-end.

**Tech Stack:** Python 3.13, uv, Pydantic v2, SQLite (stdlib), PyYAML, Typer, ruff, mypy, pytest

**Prerequisites:**
- `polymarket` CLI installed (`brew install polymarket` via Polymarket/polymarket-cli tap)
- `uv` installed
- Python 3.12+

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/polymarket_agent/__init__.py`
- Create: `tests/__init__.py`
- Create: `config.yaml`

**Step 1: Initialize project with uv**

Run:
```bash
cd /Users/pharrelly/codebase/github/brainstorming
uv init --lib --name polymarket-agent
```

Then replace the generated `pyproject.toml` with:

```toml
[project]
name = "polymarket-agent"
version = "0.1.0"
description = "Agent-friendly auto-trading pipeline for Polymarket"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "typer>=0.9",
]

[project.scripts]
polymarket-agent = "polymarket_agent.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
disallow_untyped_defs = true
warn_return_any = true
strict = true

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.0",
    "ruff>=0.4",
    "mypy>=1.10",
]
```

**Step 2: Create package structure**

```bash
mkdir -p src/polymarket_agent/data
mkdir -p src/polymarket_agent/strategies
mkdir -p src/polymarket_agent/execution
mkdir -p tests
```

Create `src/polymarket_agent/__init__.py`:
```python
"""Polymarket Agent — agent-friendly auto-trading pipeline."""
```

Create empty `__init__.py` files in each subpackage:
- `src/polymarket_agent/data/__init__.py`
- `src/polymarket_agent/strategies/__init__.py`
- `src/polymarket_agent/execution/__init__.py`
- `tests/__init__.py`

**Step 3: Create default config.yaml**

```yaml
mode: paper
starting_balance: 1000.0
poll_interval: 60

strategies:
  signal_trader:
    enabled: true
    volume_threshold: 10000
    price_move_threshold: 0.05

risk:
  max_position_size: 100.0
  max_daily_loss: 50.0
  max_open_orders: 10
```

**Step 4: Install dependencies**

Run:
```bash
uv sync
```

Expected: resolves and installs pydantic, pyyaml, typer, pytest, pytest-mock, ruff, mypy

**Step 5: Verify pytest runs**

Run:
```bash
uv run pytest --co
```

Expected: `no tests ran` (no test files yet), exit 0 with no import errors

**Step 6: Commit**

```bash
git add pyproject.toml uv.lock config.yaml src/ tests/
git commit -m "feat: scaffold polymarket-agent project"
```

---

### Task 2: Pydantic Data Models

**Files:**
- Create: `src/polymarket_agent/data/models.py`
- Create: `tests/test_models.py`

**Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
"""Tests for Pydantic data models."""

from polymarket_agent.data.models import Event, Market, OrderBook, OrderBookLevel


SAMPLE_MARKET_JSON = {
    "id": "517310",
    "question": "Will Trump deport less than 250,000?",
    "conditionId": "0xaf9d0e448129a9f657f851d49495ba4742055d80e0ef1166ba0ee81d4d594214",
    "slug": "will-trump-deport-less-than-250000",
    "endDate": "2025-12-31T12:00:00Z",
    "liquidity": "17346.01768",
    "description": "This market will resolve to Yes if...",
    "outcomes": '["Yes","No"]',
    "outcomePrices": '["0.047","0.953"]',
    "volume": "1228511.944941",
    "active": True,
    "closed": False,
    "clobTokenIds": '["0xe0cb24200c550f33b8c0faffc6f500598ca6953137ba9077a55239fb858fc371","0x92eae301a0617b6992cbcdb0d555d490b9539b387f9eef512339613ddce2eb4"]',
    "volume24hr": "10670.252910000008",
    "groupItemTitle": "<250k",
}

SAMPLE_EVENT_JSON = {
    "id": "16167",
    "ticker": "microstrategy-sell-any-bitcoin-in-2025",
    "slug": "microstrategy-sell-any-bitcoin-in-2025",
    "title": "MicroStrategy sells any Bitcoin by ___ ?",
    "description": "This market will resolve to Yes if...",
    "startDate": "2024-12-31T18:51:45.506005Z",
    "endDate": "2025-12-31T12:00:00Z",
    "active": True,
    "closed": False,
    "liquidity": "187791.88561",
    "volume": "20753924.165745",
    "volume24hr": "20717.72332",
    "markets": [],
}

SAMPLE_BOOK_JSON = {
    "asks": [
        {"price": "0.999", "size": "650.27"},
        {"price": "0.95", "size": "100"},
    ],
    "bids": [
        {"price": "0.04", "size": "500"},
        {"price": "0.03", "size": "1000"},
    ],
}


def test_market_from_cli_json():
    market = Market.from_cli(SAMPLE_MARKET_JSON)
    assert market.id == "517310"
    assert market.question == "Will Trump deport less than 250,000?"
    assert market.outcomes == ["Yes", "No"]
    assert market.outcome_prices == [0.047, 0.953]
    assert market.volume == 1228511.944941
    assert market.liquidity == 17346.01768
    assert market.active is True
    assert len(market.clob_token_ids) == 2
    assert market.volume_24h == 10670.252910000008


def test_event_from_cli_json():
    event = Event.from_cli(SAMPLE_EVENT_JSON)
    assert event.id == "16167"
    assert event.title == "MicroStrategy sells any Bitcoin by ___ ?"
    assert event.volume == 20753924.165745
    assert event.active is True


def test_orderbook_from_cli_json():
    book = OrderBook.from_cli(SAMPLE_BOOK_JSON)
    assert len(book.asks) == 2
    assert len(book.bids) == 2
    assert book.asks[0].price == 0.999
    assert book.asks[0].size == 650.27
    assert book.bids[0].price == 0.04
    assert book.best_ask == 0.95
    assert book.best_bid == 0.04
    assert book.midpoint == (0.95 + 0.04) / 2
    assert book.spread == 0.95 - 0.04


def test_market_handles_missing_optional_fields():
    minimal = {
        "id": "1",
        "question": "Test?",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.5","0.5"]',
        "volume": "100",
        "active": True,
        "closed": False,
    }
    market = Market.from_cli(minimal)
    assert market.id == "1"
    assert market.liquidity == 0.0
    assert market.volume_24h == 0.0
    assert market.clob_token_ids == []
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`

Expected: `ModuleNotFoundError` — models.py doesn't exist yet.

**Step 3: Write minimal implementation**

Create `src/polymarket_agent/data/models.py`:

```python
"""Pydantic models for Polymarket data, parsed from CLI JSON output."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class Market(BaseModel):
    """A Polymarket prediction market."""

    id: str
    question: str
    condition_id: str = ""
    slug: str = ""
    description: str = ""
    outcomes: list[str] = Field(default_factory=list)
    outcome_prices: list[float] = Field(default_factory=list)
    clob_token_ids: list[str] = Field(default_factory=list)
    volume: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    active: bool = False
    closed: bool = False
    end_date: str = ""
    group_item_title: str = ""

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> Market:
        """Parse a market from polymarket CLI JSON output."""
        outcomes_raw = data.get("outcomes", "[]")
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw

        prices_raw = data.get("outcomePrices", "[]")
        prices = [float(p) for p in (json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw)]

        tokens_raw = data.get("clobTokenIds", "[]")
        tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else (tokens_raw or [])

        return cls(
            id=data["id"],
            question=data["question"],
            condition_id=data.get("conditionId", ""),
            slug=data.get("slug", ""),
            description=data.get("description", ""),
            outcomes=outcomes,
            outcome_prices=prices,
            clob_token_ids=tokens,
            volume=float(data.get("volume", 0)),
            volume_24h=float(data.get("volume24hr", 0)),
            liquidity=float(data.get("liquidity") or 0),
            active=data.get("active", False),
            closed=data.get("closed", False),
            end_date=data.get("endDate", ""),
            group_item_title=data.get("groupItemTitle", ""),
        )


class Event(BaseModel):
    """A Polymarket event (container for related markets)."""

    id: str
    title: str
    slug: str = ""
    description: str = ""
    active: bool = False
    closed: bool = False
    volume: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    markets: list[Market] = Field(default_factory=list)

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> Event:
        """Parse an event from polymarket CLI JSON output."""
        raw_markets = data.get("markets") or []
        markets = [Market.from_cli(m) for m in raw_markets]
        return cls(
            id=data["id"],
            title=data.get("title", ""),
            slug=data.get("slug", ""),
            description=data.get("description", ""),
            active=data.get("active", False),
            closed=data.get("closed", False),
            volume=float(data.get("volume", 0)),
            volume_24h=float(data.get("volume24hr", 0)),
            liquidity=float(data.get("liquidity") or 0),
            markets=markets,
        )


class OrderBookLevel(BaseModel):
    """A single level in an order book."""

    price: float
    size: float


class OrderBook(BaseModel):
    """Order book for a token."""

    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> OrderBook:
        """Parse an order book from polymarket CLI JSON output."""
        bids = [OrderBookLevel(price=float(b["price"]), size=float(b["size"])) for b in data.get("bids", [])]
        asks = [OrderBookLevel(price=float(a["price"]), size=float(a["size"])) for a in data.get("asks", [])]
        return cls(bids=bids, asks=asks)

    @property
    def best_bid(self) -> float:
        return max((b.price for b in self.bids), default=0.0)

    @property
    def best_ask(self) -> float:
        return min((a.price for a in self.asks), default=0.0)

    @property
    def midpoint(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return self.best_ask - self.best_bid


class Price(BaseModel):
    """Current price for a token."""

    token_id: str
    price: float
    side: str = ""


class PricePoint(BaseModel):
    """A single point in price history."""

    timestamp: int
    price: float
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`

Expected: all 4 tests PASS.

**Step 5: Lint and type check**

Run:
```bash
uv run ruff check src/polymarket_agent/data/models.py
uv run ruff format src/polymarket_agent/data/models.py
```

**Step 6: Commit**

```bash
git add src/polymarket_agent/data/models.py tests/test_models.py
git commit -m "feat: add Pydantic models for Market, Event, OrderBook"
```

---

### Task 3: TTL Cache

**Files:**
- Create: `src/polymarket_agent/data/cache.py`
- Create: `tests/test_cache.py`

**Step 1: Write the failing test**

Create `tests/test_cache.py`:

```python
"""Tests for TTL cache."""

import time

from polymarket_agent.data.cache import TTLCache


def test_cache_stores_and_retrieves():
    cache = TTLCache(default_ttl=60)
    cache.set("key1", {"data": "value"})
    assert cache.get("key1") == {"data": "value"}


def test_cache_returns_none_for_missing_key():
    cache = TTLCache(default_ttl=60)
    assert cache.get("missing") is None


def test_cache_expires_after_ttl():
    cache = TTLCache(default_ttl=0.1)
    cache.set("key1", "value")
    assert cache.get("key1") == "value"
    time.sleep(0.15)
    assert cache.get("key1") is None


def test_cache_custom_ttl_per_key():
    cache = TTLCache(default_ttl=60)
    cache.set("short", "value", ttl=0.1)
    cache.set("long", "value", ttl=60)
    time.sleep(0.15)
    assert cache.get("short") is None
    assert cache.get("long") == "value"


def test_cache_clear():
    cache = TTLCache(default_ttl=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cache.py -v`

Expected: `ImportError`

**Step 3: Write minimal implementation**

Create `src/polymarket_agent/data/cache.py`:

```python
"""Simple TTL cache for Polymarket data."""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """In-memory cache with per-key TTL expiration."""

    def __init__(self, default_ttl: float = 60.0) -> None:
        self._default_ttl = default_ttl
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        expires_at = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        self._store[key] = (value, expires_at)

    def clear(self) -> None:
        self._store.clear()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cache.py -v`

Expected: all 5 tests PASS.

**Step 5: Lint**

Run: `uv run ruff check src/polymarket_agent/data/cache.py`

**Step 6: Commit**

```bash
git add src/polymarket_agent/data/cache.py tests/test_cache.py
git commit -m "feat: add TTL cache for data layer"
```

---

### Task 4: CLI Wrapper (PolymarketData Client)

**Files:**
- Create: `src/polymarket_agent/data/client.py`
- Create: `tests/test_client.py`

**Step 1: Write the failing test**

Create `tests/test_client.py`:

```python
"""Tests for PolymarketData CLI wrapper client."""

import json
import subprocess

import pytest

from polymarket_agent.data.client import PolymarketData


MOCK_MARKETS_JSON = json.dumps([
    {
        "id": "100",
        "question": "Will it rain tomorrow?",
        "conditionId": "0xabc",
        "slug": "will-it-rain-tomorrow",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.6","0.4"]',
        "volume": "50000",
        "volume24hr": "1200",
        "liquidity": "5000",
        "active": True,
        "closed": False,
        "clobTokenIds": '["0xtok1","0xtok2"]',
    }
])

MOCK_EVENTS_JSON = json.dumps([
    {
        "id": "200",
        "title": "Weather Events",
        "slug": "weather-events",
        "description": "Weather prediction markets",
        "active": True,
        "closed": False,
        "volume": "100000",
        "volume24hr": "5000",
        "liquidity": "20000",
        "markets": [],
    }
])

MOCK_BOOK_JSON = json.dumps({
    "bids": [{"price": "0.55", "size": "100"}],
    "asks": [{"price": "0.65", "size": "200"}],
})


def _mock_run(args, **kwargs):
    cmd = " ".join(args)
    result = subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
    if "markets list" in cmd or "markets search" in cmd:
        result.stdout = MOCK_MARKETS_JSON
    elif "events list" in cmd:
        result.stdout = MOCK_EVENTS_JSON
    elif "clob book" in cmd:
        result.stdout = MOCK_BOOK_JSON
    return result


@pytest.fixture
def client(mocker):
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    return PolymarketData()


def test_get_active_markets(client):
    markets = client.get_active_markets(limit=1)
    assert len(markets) == 1
    assert markets[0].id == "100"
    assert markets[0].question == "Will it rain tomorrow?"


def test_get_events(client):
    events = client.get_events(limit=1)
    assert len(events) == 1
    assert events[0].title == "Weather Events"


def test_get_orderbook(client):
    book = client.get_orderbook("0xtok1")
    assert book.best_bid == 0.55
    assert book.best_ask == 0.65


def test_cli_error_raises(mocker):
    def _fail(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="API error")
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_fail)
    client = PolymarketData()
    with pytest.raises(RuntimeError, match="polymarket CLI failed"):
        client.get_active_markets()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client.py -v`

Expected: `ImportError`

**Step 3: Write minimal implementation**

Create `src/polymarket_agent/data/client.py`:

```python
"""Polymarket data client — wraps the polymarket CLI with -o json."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from polymarket_agent.data.cache import TTLCache
from polymarket_agent.data.models import Event, Market, OrderBook


class PolymarketData:
    """Fetches Polymarket data by shelling out to the polymarket CLI."""

    def __init__(self, cache_ttl_prices: float = 10.0, cache_ttl_markets: float = 300.0) -> None:
        self._price_cache = TTLCache(default_ttl=cache_ttl_prices)
        self._market_cache = TTLCache(default_ttl=cache_ttl_markets)

    def get_active_markets(self, tag: str | None = None, limit: int = 25) -> list[Market]:
        args = ["markets", "list", "--active", "true", "--limit", str(limit)]
        data = self._run_cli_cached(self._market_cache, f"markets:active:{tag}:{limit}", args)
        return [Market.from_cli(m) for m in data]

    def get_events(self, tag: str | None = None, limit: int = 25) -> list[Event]:
        args = ["events", "list", "--active", "true", "--limit", str(limit)]
        if tag:
            args.extend(["--tag", tag])
        data = self._run_cli_cached(self._market_cache, f"events:{tag}:{limit}", args)
        return [Event.from_cli(e) for e in data]

    def get_orderbook(self, token_id: str) -> OrderBook:
        args = ["clob", "book", token_id]
        data = self._run_cli_cached(self._price_cache, f"book:{token_id}", args)
        return OrderBook.from_cli(data)

    def get_price_history(self, token_id: str, interval: str = "1d", fidelity: int = 50) -> list[dict[str, Any]]:
        args = ["clob", "price-history", token_id, "--interval", interval, "--fidelity", str(fidelity)]
        result: list[dict[str, Any]] = self._run_cli(args)
        return result

    def _run_cli_cached(self, cache: TTLCache, key: str, args: list[str]) -> Any:
        cached = cache.get(key)
        if cached is not None:
            return cached
        result = self._run_cli(args)
        cache.set(key, result)
        return result

    def _run_cli(self, args: list[str]) -> Any:
        full_args = ["polymarket", *args, "-o", "json"]
        proc = subprocess.run(full_args, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"polymarket CLI failed (exit {proc.returncode}): {proc.stderr.strip()}")
        if not proc.stdout.strip():
            return []
        return json.loads(proc.stdout)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client.py -v`

Expected: all 4 tests PASS.

**Step 5: Lint**

Run: `uv run ruff check src/polymarket_agent/data/client.py`

**Step 6: Commit**

```bash
git add src/polymarket_agent/data/client.py tests/test_client.py
git commit -m "feat: add PolymarketData CLI wrapper client"
```

---

### Task 5: Config Loading

**Files:**
- Create: `src/polymarket_agent/config.py`
- Create: `tests/test_config.py`

**Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
"""Tests for config loading."""

import tempfile
from pathlib import Path

from polymarket_agent.config import AppConfig, load_config


SAMPLE_YAML = """\
mode: paper
starting_balance: 2000.0
poll_interval: 30

strategies:
  signal_trader:
    enabled: true
    volume_threshold: 5000
    price_move_threshold: 0.03

risk:
  max_position_size: 200.0
  max_daily_loss: 100.0
  max_open_orders: 5
"""


def test_load_config_from_yaml():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        f.flush()
        config = load_config(Path(f.name))
    assert config.mode == "paper"
    assert config.starting_balance == 2000.0
    assert config.poll_interval == 30
    assert config.strategies["signal_trader"]["enabled"] is True
    assert config.risk.max_position_size == 200.0


def test_default_config():
    config = AppConfig()
    assert config.mode == "paper"
    assert config.starting_balance == 1000.0
    assert config.risk.max_position_size == 100.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`

Expected: `ImportError`

**Step 3: Write minimal implementation**

Create `src/polymarket_agent/config.py`:

```python
"""Configuration loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class RiskConfig(BaseModel):
    max_position_size: float = 100.0
    max_daily_loss: float = 50.0
    max_open_orders: int = 10


class AppConfig(BaseModel):
    mode: Literal["monitor", "paper", "live", "mcp"] = "paper"
    starting_balance: float = 1000.0
    poll_interval: int = 60
    strategies: dict[str, dict[str, Any]] = Field(default_factory=dict)
    risk: RiskConfig = Field(default_factory=RiskConfig)


def load_config(path: Path) -> AppConfig:
    """Load config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return AppConfig(**raw)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`

Expected: all 2 tests PASS.

**Step 5: Commit**

```bash
git add src/polymarket_agent/config.py tests/test_config.py
git commit -m "feat: add config loading with Pydantic validation"
```

---

### Task 6: Strategy Base + Signal Model

**Files:**
- Create: `src/polymarket_agent/strategies/base.py`
- Create: `tests/test_strategy_base.py`

**Step 1: Write the failing test**

Create `tests/test_strategy_base.py`:

```python
"""Tests for strategy base class and Signal model."""

from polymarket_agent.strategies.base import Signal, Strategy
from polymarket_agent.data.client import PolymarketData
from polymarket_agent.data.models import Market


class DummyStrategy(Strategy):
    name = "dummy"

    def analyze(self, markets, data):
        return [
            Signal(
                strategy=self.name,
                market_id="100",
                token_id="0xtok1",
                side="buy",
                confidence=0.8,
                target_price=0.5,
                size=50.0,
                reason="Test signal",
            )
        ]


def test_signal_creation():
    signal = Signal(
        strategy="test",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=0.75,
        target_price=0.6,
        size=25.0,
        reason="price looks low",
    )
    assert signal.strategy == "test"
    assert signal.side == "buy"
    assert signal.confidence == 0.75


def test_dummy_strategy_produces_signals():
    strategy = DummyStrategy()
    signals = strategy.analyze([], None)
    assert len(signals) == 1
    assert signals[0].strategy == "dummy"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategy_base.py -v`

Expected: `ImportError`

**Step 3: Write minimal implementation**

Create `src/polymarket_agent/strategies/base.py`:

```python
"""Strategy base class and Signal model."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from polymarket_agent.data.client import PolymarketData
    from polymarket_agent.data.models import Market


@dataclass
class Signal:
    """A trade signal emitted by a strategy."""

    strategy: str
    market_id: str
    token_id: str
    side: Literal["buy", "sell"]
    confidence: float
    target_price: float
    size: float
    reason: str


class Strategy(ABC):
    """Base class for all trading strategies."""

    name: str

    @abstractmethod
    def analyze(self, markets: list[Market], data: PolymarketData) -> list[Signal]:
        """Analyze markets and return trade signals."""

    def configure(self, config: dict[str, Any]) -> None:
        """Load strategy-specific config. Override in subclasses."""
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_strategy_base.py -v`

Expected: all 2 tests PASS.

**Step 5: Commit**

```bash
git add src/polymarket_agent/strategies/base.py tests/test_strategy_base.py
git commit -m "feat: add Strategy ABC and Signal dataclass"
```

---

### Task 7: Signal Trader Strategy

**Files:**
- Create: `src/polymarket_agent/strategies/signal_trader.py`
- Create: `tests/test_signal_trader.py`

**Step 1: Write the failing test**

Create `tests/test_signal_trader.py`:

```python
"""Tests for the SignalTrader strategy."""

import json

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.signal_trader import SignalTrader


def _make_market(market_id, yes_price, volume_24h, volume=100000):
    return Market.from_cli({
        "id": market_id,
        "question": f"Test market {market_id}?",
        "outcomes": '["Yes","No"]',
        "outcomePrices": json.dumps([str(yes_price), str(round(1 - yes_price, 4))]),
        "volume": str(volume),
        "volume24hr": str(volume_24h),
        "liquidity": "10000",
        "active": True,
        "closed": False,
        "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
    })


def test_signal_trader_flags_high_volume_markets():
    strategy = SignalTrader()
    strategy.configure({"volume_threshold": 5000, "price_move_threshold": 0.05})
    markets = [
        _make_market("1", 0.3, volume_24h=10000),
        _make_market("2", 0.5, volume_24h=1000),  # low volume, should be skipped
    ]
    signals = strategy.analyze(markets, data=None)
    market_ids = [s.market_id for s in signals]
    assert "1" in market_ids
    assert "2" not in market_ids


def test_signal_trader_skips_closed_markets():
    strategy = SignalTrader()
    strategy.configure({"volume_threshold": 1000, "price_move_threshold": 0.05})
    market = Market.from_cli({
        "id": "99",
        "question": "Closed?",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.5","0.5"]',
        "volume": "50000",
        "volume24hr": "20000",
        "active": False,
        "closed": True,
    })
    signals = strategy.analyze([market], data=None)
    assert len(signals) == 0


def test_signal_trader_respects_thresholds():
    strategy = SignalTrader()
    strategy.configure({"volume_threshold": 50000, "price_move_threshold": 0.1})
    assert strategy._volume_threshold == 50000
    assert strategy._price_move_threshold == 0.1
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_signal_trader.py -v`

Expected: `ImportError`

**Step 3: Write minimal implementation**

Create `src/polymarket_agent/strategies/signal_trader.py`:

```python
"""Signal-based trading strategy — trades on volume spikes and price momentum."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polymarket_agent.strategies.base import Signal, Strategy

if TYPE_CHECKING:
    from polymarket_agent.data.client import PolymarketData
    from polymarket_agent.data.models import Market


class SignalTrader(Strategy):
    """Identifies trading opportunities based on volume and price movement."""

    name = "signal_trader"

    def __init__(self) -> None:
        self._volume_threshold: float = 10000
        self._price_move_threshold: float = 0.05

    def configure(self, config: dict[str, Any]) -> None:
        self._volume_threshold = config.get("volume_threshold", self._volume_threshold)
        self._price_move_threshold = config.get("price_move_threshold", self._price_move_threshold)

    def analyze(self, markets: list[Market], data: PolymarketData | None) -> list[Signal]:
        signals: list[Signal] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            if market.volume_24h < self._volume_threshold:
                continue
            if not market.clob_token_ids or not market.outcome_prices:
                continue

            yes_price = market.outcome_prices[0]
            token_id = market.clob_token_ids[0]
            distance_from_even = abs(yes_price - 0.5)

            if distance_from_even > self._price_move_threshold:
                side = "buy" if yes_price < 0.5 else "sell"
                signals.append(Signal(
                    strategy=self.name,
                    market_id=market.id,
                    token_id=token_id,
                    side=side,
                    confidence=min(distance_from_even * 2, 0.9),
                    target_price=yes_price,
                    size=min(market.volume_24h * 0.001, 50.0),
                    reason=f"High volume ({market.volume_24h:.0f} 24h) with price at {yes_price:.2f}",
                ))
        return signals
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_signal_trader.py -v`

Expected: all 3 tests PASS.

**Step 5: Commit**

```bash
git add src/polymarket_agent/strategies/signal_trader.py tests/test_signal_trader.py
git commit -m "feat: add SignalTrader strategy"
```

---

### Task 8: SQLite Database Layer

**Files:**
- Create: `src/polymarket_agent/db.py`
- Create: `tests/test_db.py`

**Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
"""Tests for SQLite database layer."""

import tempfile
from pathlib import Path

from polymarket_agent.db import Database, Trade


def test_db_initializes_tables():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        assert db._conn is not None


def test_record_and_query_trade():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.record_trade(Trade(
            strategy="signal_trader",
            market_id="100",
            token_id="0xtok1",
            side="buy",
            price=0.55,
            size=25.0,
            reason="test trade",
        ))
        trades = db.get_trades()
        assert len(trades) == 1
        assert trades[0]["market_id"] == "100"
        assert trades[0]["price"] == 0.55


def test_get_trades_by_strategy():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.record_trade(Trade(strategy="alpha", market_id="1", token_id="t1", side="buy", price=0.5, size=10, reason="a"))
        db.record_trade(Trade(strategy="beta", market_id="2", token_id="t2", side="sell", price=0.7, size=20, reason="b"))
        trades = db.get_trades(strategy="alpha")
        assert len(trades) == 1
        assert trades[0]["strategy"] == "alpha"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`

Expected: `ImportError`

**Step 3: Write minimal implementation**

Create `src/polymarket_agent/db.py`:

```python
"""SQLite database for trade logging and portfolio state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Trade:
    """A trade to be recorded."""

    strategy: str
    market_id: str
    token_id: str
    side: str
    price: float
    size: float
    reason: str


class Database:
    """SQLite database for persisting trades and portfolio state."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                strategy TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                reason TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def record_trade(self, trade: Trade) -> None:
        self._conn.execute(
            "INSERT INTO trades (strategy, market_id, token_id, side, price, size, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trade.strategy, trade.market_id, trade.token_id, trade.side, trade.price, trade.size, trade.reason),
        )
        self._conn.commit()

    def get_trades(self, strategy: str | None = None) -> list[dict]:
        if strategy:
            rows = self._conn.execute("SELECT * FROM trades WHERE strategy = ? ORDER BY timestamp DESC", (strategy,)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM trades ORDER BY timestamp DESC").fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`

Expected: all 3 tests PASS.

**Step 5: Commit**

```bash
git add src/polymarket_agent/db.py tests/test_db.py
git commit -m "feat: add SQLite database for trade logging"
```

---

### Task 9: Execution Layer — Paper Trader

**Files:**
- Create: `src/polymarket_agent/execution/base.py`
- Create: `src/polymarket_agent/execution/paper.py`
- Create: `tests/test_paper_trader.py`

**Step 1: Write the failing test**

Create `tests/test_paper_trader.py`:

```python
"""Tests for paper trading executor."""

import tempfile
from pathlib import Path

from polymarket_agent.execution.paper import PaperTrader
from polymarket_agent.strategies.base import Signal
from polymarket_agent.db import Database


def _make_signal(market_id="100", side="buy", price=0.5, size=25.0):
    return Signal(
        strategy="test",
        market_id=market_id,
        token_id=f"0xtok_{market_id}",
        side=side,
        confidence=0.8,
        target_price=price,
        size=size,
        reason="test",
    )


def test_paper_trader_initial_balance():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        trader = PaperTrader(starting_balance=1000.0, db=db)
        portfolio = trader.get_portfolio()
        assert portfolio.balance == 1000.0
        assert portfolio.positions == {}
        assert portfolio.total_value == 1000.0


def test_paper_trader_buy():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        trader = PaperTrader(starting_balance=1000.0, db=db)
        order = trader.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        assert order is not None
        portfolio = trader.get_portfolio()
        assert portfolio.balance == 950.0
        assert "0xtok_100" in portfolio.positions


def test_paper_trader_insufficient_balance():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        trader = PaperTrader(starting_balance=10.0, db=db)
        order = trader.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        assert order is None


def test_paper_trader_logs_trades():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        trader = PaperTrader(starting_balance=1000.0, db=db)
        trader.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        trades = db.get_trades()
        assert len(trades) == 1
        assert trades[0]["side"] == "buy"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paper_trader.py -v`

Expected: `ImportError`

**Step 3: Write base executor and paper trader**

Create `src/polymarket_agent/execution/base.py`:

```python
"""Executor base class and portfolio model."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from polymarket_agent.strategies.base import Signal


@dataclass
class Portfolio:
    """Current portfolio state."""

    balance: float
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def total_value(self) -> float:
        position_value = sum(
            p.get("shares", 0) * p.get("current_price", p.get("avg_price", 0))
            for p in self.positions.values()
        )
        return self.balance + position_value


@dataclass
class Order:
    """A filled order."""

    market_id: str
    token_id: str
    side: str
    price: float
    size: float
    shares: float


class Executor(ABC):
    """Base class for trade execution."""

    @abstractmethod
    def place_order(self, signal: Signal) -> Order | None: ...

    @abstractmethod
    def get_portfolio(self) -> Portfolio: ...
```

Create `src/polymarket_agent/execution/paper.py`:

```python
"""Paper trading executor — simulates fills against a virtual balance."""

from __future__ import annotations

from polymarket_agent.db import Database, Trade
from polymarket_agent.execution.base import Executor, Order, Portfolio
from polymarket_agent.strategies.base import Signal


class PaperTrader(Executor):
    """Simulates trading with a virtual USDC balance."""

    def __init__(self, starting_balance: float, db: Database) -> None:
        self._balance = starting_balance
        self._positions: dict[str, dict] = {}
        self._db = db

    def place_order(self, signal: Signal) -> Order | None:
        if signal.side == "buy":
            return self._buy(signal)
        return self._sell(signal)

    def _buy(self, signal: Signal) -> Order | None:
        cost = signal.size
        if cost > self._balance:
            return None

        shares = signal.size / signal.target_price
        self._balance -= cost

        pos = self._positions.get(signal.token_id, {"shares": 0.0, "avg_price": 0.0, "market_id": signal.market_id})
        total_shares = pos["shares"] + shares
        if total_shares > 0:
            pos["avg_price"] = (pos["shares"] * pos["avg_price"] + shares * signal.target_price) / total_shares
        pos["shares"] = total_shares
        pos["market_id"] = signal.market_id
        self._positions[signal.token_id] = pos

        self._log(signal)
        return Order(market_id=signal.market_id, token_id=signal.token_id, side="buy", price=signal.target_price, size=cost, shares=shares)

    def _sell(self, signal: Signal) -> Order | None:
        pos = self._positions.get(signal.token_id)
        if not pos or pos["shares"] <= 0:
            return None

        shares_to_sell = min(signal.size / signal.target_price, pos["shares"])
        proceeds = shares_to_sell * signal.target_price
        pos["shares"] -= shares_to_sell
        self._balance += proceeds

        if pos["shares"] <= 0:
            del self._positions[signal.token_id]

        self._log(signal)
        return Order(market_id=signal.market_id, token_id=signal.token_id, side="sell", price=signal.target_price, size=proceeds, shares=shares_to_sell)

    def _log(self, signal: Signal) -> None:
        self._db.record_trade(Trade(
            strategy=signal.strategy, market_id=signal.market_id, token_id=signal.token_id,
            side=signal.side, price=signal.target_price, size=signal.size, reason=signal.reason,
        ))

    def get_portfolio(self) -> Portfolio:
        return Portfolio(balance=self._balance, positions=dict(self._positions))
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paper_trader.py -v`

Expected: all 4 tests PASS.

**Step 5: Commit**

```bash
git add src/polymarket_agent/execution/base.py src/polymarket_agent/execution/paper.py tests/test_paper_trader.py
git commit -m "feat: add paper trading executor"
```

---

### Task 10: Orchestrator

**Files:**
- Create: `src/polymarket_agent/orchestrator.py`
- Create: `tests/test_orchestrator.py`

**Step 1: Write the failing test**

Create `tests/test_orchestrator.py`:

```python
"""Tests for the orchestrator."""

import json
import subprocess
import tempfile
from pathlib import Path

from polymarket_agent.config import AppConfig
from polymarket_agent.orchestrator import Orchestrator


MOCK_MARKETS = json.dumps([{
    "id": "100",
    "question": "Will it rain?",
    "outcomes": '["Yes","No"]',
    "outcomePrices": '["0.3","0.7"]',
    "volume": "50000",
    "volume24hr": "12000",
    "liquidity": "5000",
    "active": True,
    "closed": False,
    "clobTokenIds": '["0xtok1","0xtok2"]',
}])


def _mock_run(args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=MOCK_MARKETS, stderr="")


def test_orchestrator_single_tick(mocker):
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            strategies={"signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05}},
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        result = orch.tick()
        assert "markets_fetched" in result
        assert "signals_generated" in result
        assert "trades_executed" in result


def test_orchestrator_monitor_mode_no_trades(mocker):
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="monitor",
            strategies={"signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05}},
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        result = orch.tick()
        assert result["trades_executed"] == 0


def test_orchestrator_portfolio(mocker):
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper", starting_balance=500.0)
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        assert orch.get_portfolio().balance == 500.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py -v`

Expected: `ImportError`

**Step 3: Write minimal implementation**

Create `src/polymarket_agent/orchestrator.py`:

```python
"""Orchestrator — coordinates data, strategies, and execution."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from polymarket_agent.config import AppConfig
from polymarket_agent.data.client import PolymarketData
from polymarket_agent.db import Database
from polymarket_agent.execution.base import Portfolio
from polymarket_agent.execution.paper import PaperTrader
from polymarket_agent.strategies.base import Signal, Strategy
from polymarket_agent.strategies.signal_trader import SignalTrader

logger = logging.getLogger(__name__)

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "signal_trader": SignalTrader,
}


class Orchestrator:
    """Coordinates data fetching, strategy execution, and trade placement."""

    def __init__(self, config: AppConfig, db_path: Path) -> None:
        self._config = config
        self._db = Database(db_path)
        self._data = PolymarketData()
        self._executor = PaperTrader(starting_balance=config.starting_balance, db=self._db)
        self._strategies = self._load_strategies()

    def _load_strategies(self) -> list[Strategy]:
        strategies: list[Strategy] = []
        for name, params in self._config.strategies.items():
            if not params.get("enabled", False):
                continue
            cls = STRATEGY_REGISTRY.get(name)
            if cls is None:
                logger.warning("Unknown strategy: %s", name)
                continue
            strategy = cls()
            strategy.configure(params)
            strategies.append(strategy)
        return strategies

    def tick(self) -> dict[str, Any]:
        """Run one cycle: fetch data, generate signals, execute trades."""
        try:
            markets = self._data.get_active_markets(limit=50)
        except RuntimeError as e:
            logger.error("Failed to fetch markets: %s", e)
            return {"markets_fetched": 0, "signals_generated": 0, "trades_executed": 0, "error": str(e)}

        all_signals: list[Signal] = []
        for strategy in self._strategies:
            try:
                all_signals.extend(strategy.analyze(markets, self._data))
            except Exception:
                logger.exception("Strategy %s failed", strategy.name)

        trades_executed = 0
        if self._config.mode != "monitor":
            for signal in all_signals:
                if signal.confidence >= 0.3:
                    order = self._executor.place_order(signal)
                    if order is not None:
                        trades_executed += 1
                        logger.info("Executed: %s %.2f shares of %s @ %.4f", order.side, order.shares, order.market_id, order.price)

        return {"markets_fetched": len(markets), "signals_generated": len(all_signals), "trades_executed": trades_executed}

    def get_portfolio(self) -> Portfolio:
        return self._executor.get_portfolio()

    def get_recent_trades(self, limit: int = 20) -> list[dict]:
        return self._db.get_trades()[:limit]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator.py -v`

Expected: all 3 tests PASS.

**Step 5: Commit**

```bash
git add src/polymarket_agent/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add orchestrator loop"
```

---

### Task 11: CLI Entry Point

**Files:**
- Create: `src/polymarket_agent/cli.py`
- Create: `tests/test_cli.py`

**Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
"""Tests for CLI entry point."""

from typer.testing import CliRunner

from polymarket_agent.cli import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "polymarket-agent" in result.stdout.lower() or "Polymarket" in result.stdout


def test_cli_status():
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`

Expected: `ImportError`

**Step 3: Write minimal implementation**

Create `src/polymarket_agent/cli.py`:

```python
"""CLI entry point for polymarket-agent."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import typer

from polymarket_agent.config import AppConfig, load_config
from polymarket_agent.orchestrator import Orchestrator

app = typer.Typer(name="polymarket-agent", help="Polymarket Agent — agent-friendly auto-trading pipeline")

DEFAULT_CONFIG = Path("config.yaml")
DEFAULT_DB = Path("polymarket_agent.db")


def _get_config(config_path: Path) -> AppConfig:
    if config_path.exists():
        return load_config(config_path)
    return AppConfig()


@app.command()
def run(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Path to config.yaml"),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="Path to SQLite database"),
) -> None:
    """Run the continuous trading loop."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = _get_config(config)
    orch = Orchestrator(config=cfg, db_path=db)
    typer.echo(f"Starting polymarket-agent in {cfg.mode} mode (poll every {cfg.poll_interval}s)")
    try:
        while True:
            result = orch.tick()
            portfolio = orch.get_portfolio()
            typer.echo(f"[{cfg.mode}] markets={result['markets_fetched']} signals={result['signals_generated']} trades={result['trades_executed']} balance=${portfolio.balance:.2f}")
            time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def status(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
) -> None:
    """Show current portfolio and recent trades."""
    cfg = _get_config(config)
    orch = Orchestrator(config=cfg, db_path=db)
    portfolio = orch.get_portfolio()
    typer.echo(f"Mode: {cfg.mode}")
    typer.echo(f"Balance: ${portfolio.balance:.2f}")
    typer.echo(f"Total Value: ${portfolio.total_value:.2f}")
    typer.echo(f"Positions: {len(portfolio.positions)}")


@app.command()
def tick(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
) -> None:
    """Run a single tick of the trading loop."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = _get_config(config)
    orch = Orchestrator(config=cfg, db_path=db)
    result = orch.tick()
    portfolio = orch.get_portfolio()
    typer.echo(f"Markets: {result['markets_fetched']}, Signals: {result['signals_generated']}, Trades: {result['trades_executed']}")
    typer.echo(f"Portfolio: ${portfolio.balance:.2f} cash, ${portfolio.total_value:.2f} total")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`

Expected: all 2 tests PASS.

**Step 5: Commit**

```bash
git add src/polymarket_agent/cli.py tests/test_cli.py
git commit -m "feat: add CLI with run, status, and tick commands"
```

---

### Task 12: Full Test Suite + Lint + Smoke Test

**Step 1: Run full test suite**

Run: `uv run pytest -v`

Expected: all tests pass (~25 tests).

**Step 2: Run ruff lint and format**

Run:
```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Fix any issues.

**Step 3: Run a live single tick**

Run:
```bash
uv run polymarket-agent tick --config config.yaml
```

Expected output like:
```
Markets: 25, Signals: N, Trades: N
Portfolio: $1000.00 cash, $1000.00 total
```

**Step 4: Commit**

```bash
git add -A
git commit -m "chore: full test suite passing, lint clean"
```

---

## Summary

| Task | Component | Key Files |
|------|-----------|-----------|
| 1 | Project scaffolding | `pyproject.toml`, package dirs |
| 2 | Pydantic data models | `data/models.py` |
| 3 | TTL cache | `data/cache.py` |
| 4 | CLI wrapper client | `data/client.py` |
| 5 | Config loading | `config.py` |
| 6 | Strategy ABC + Signal | `strategies/base.py` |
| 7 | SignalTrader strategy | `strategies/signal_trader.py` |
| 8 | SQLite database | `db.py` |
| 9 | Paper trader | `execution/base.py`, `execution/paper.py` |
| 10 | Orchestrator | `orchestrator.py` |
| 11 | CLI entry point | `cli.py` |
| 12 | Full test suite + smoke test | verification |

**Next phases (separate plans):**
- Phase 2: More strategies (market maker, arbitrageur, AI analyst)
- Phase 3: MCP server
- Phase 4: Live trading with py-clob-client
