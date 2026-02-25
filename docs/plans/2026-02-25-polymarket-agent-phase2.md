# Polymarket Agent — Phase 2 Implementation Plan: Strategy Modules

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the remaining three trading strategies (MarketMaker, Arbitrageur, AIAnalyst), add signal aggregation to the orchestrator, and fix existing mypy type errors.

**Architecture:** Each strategy implements the existing `Strategy` ABC and is registered in `STRATEGY_REGISTRY`. The orchestrator gains a signal aggregation step that deduplicates and filters signals before execution. The AIAnalyst uses the Anthropic SDK to get probability estimates from Claude.

**Tech Stack:** Python 3.12+, Pydantic v2, anthropic SDK, existing infrastructure from Phase 1

**Prerequisites:**
- Phase 1 complete (all 32 tests passing, ruff clean)
- `polymarket` CLI installed
- `uv` installed
- For AIAnalyst: `ANTHROPIC_API_KEY` environment variable (optional — strategy gracefully skips if missing)

**Phase 1 status:** COMPLETE. 14 commits, 32 tests, ruff clean. Known mypy issues (5 errors) will be fixed as Task 1 below.

---

### Task 1: Fix Existing mypy Errors

**Files:**
- Modify: `src/polymarket_agent/config.py`
- Modify: `src/polymarket_agent/data/client.py`
- Modify: `src/polymarket_agent/strategies/signal_trader.py`
- Modify: `src/polymarket_agent/execution/base.py`
- Modify: `pyproject.toml`

**Context:** Phase 1 left 5 mypy errors. Fix them before adding new code.

**Step 1: Install types-PyYAML stub**

Run:
```bash
uv add --dev types-PyYAML
```

**Step 2: Fix client.py type: ignore comment**

In `src/polymarket_agent/data/client.py:107`, change:
```python
return cached  # type: ignore[return-value]
```
to:
```python
assert isinstance(cached, str)
return cached
```

**Step 3: Fix signal_trader.py Literal type**

In `src/polymarket_agent/strategies/signal_trader.py:66-69`, change:
```python
if yes_price < _MIDPOINT:
    side: str = "buy"
```
to:
```python
from typing import Literal
# ...
if yes_price < _MIDPOINT:
    side: Literal["buy", "sell"] = "buy"
```

Add the `Literal` import at the top of the file (it's already imported in `base.py`).

**Step 4: Fix execution/base.py return type**

In `src/polymarket_agent/execution/base.py:22-25`, the `total_value` property returns `Any` because `dict.get()` returns `Any`. Fix by adding explicit type annotation:
```python
@property
def total_value(self) -> float:
    position_value: float = sum(
        float(p.get("shares", 0)) * float(p.get("current_price", p.get("avg_price", 0)))
        for p in self.positions.values()
    )
    return self.balance + position_value
```

**Step 5: Run mypy to verify**

Run: `uv run mypy src/`
Expected: `Success: no issues found`

**Step 6: Run tests to verify no regressions**

Run: `uv run pytest tests/ -v`
Expected: 32 passed

**Step 7: Commit**

```bash
git add -A
git commit -m "fix: resolve mypy type errors from Phase 1"
```

---

### Task 2: MarketMaker Strategy

**Files:**
- Create: `src/polymarket_agent/strategies/market_maker.py`
- Create: `tests/test_market_maker.py`
- Modify: `src/polymarket_agent/orchestrator.py` (add to STRATEGY_REGISTRY)

**Context:** The MarketMaker places virtual bid/ask orders around the order book midpoint. It uses the `PolymarketData.get_orderbook()` method to fetch real book data and calculates a spread to quote around. Configurable parameters: `spread` (default 0.05), `max_inventory` (default 500 shares), `min_liquidity` (default 1000).

**Step 1: Write tests**

```python
"""Tests for the MarketMaker strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import Market, OrderBook
from polymarket_agent.strategies.market_maker import MarketMaker


def _make_market(market_id: str = "100", yes_price: float = 0.5, volume_24h: float = 10000.0) -> Market:
    return Market.from_cli(
        {
            "id": market_id,
            "question": f"Test market {market_id}?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(yes_price), str(round(1 - yes_price, 4))]),
            "volume": "50000",
            "volume24hr": str(volume_24h),
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
        }
    )


def _mock_orderbook(best_bid: float = 0.48, best_ask: float = 0.52) -> OrderBook:
    return OrderBook.from_cli(
        {
            "bids": [{"price": str(best_bid), "size": "500"}],
            "asks": [{"price": str(best_ask), "size": "500"}],
        }
    )


def test_market_maker_generates_buy_and_sell_signals() -> None:
    strategy = MarketMaker()
    strategy.configure({"spread": 0.05, "min_liquidity": 1000})
    data = MagicMock()
    data.get_orderbook.return_value = _mock_orderbook(0.48, 0.52)
    market = _make_market("1", yes_price=0.5, volume_24h=10000)
    signals = strategy.analyze([market], data)
    sides = {s.side for s in signals}
    assert "buy" in sides
    assert "sell" in sides


def test_market_maker_skips_low_liquidity() -> None:
    strategy = MarketMaker()
    strategy.configure({"spread": 0.05, "min_liquidity": 100000})
    data = MagicMock()
    market = _make_market("1", yes_price=0.5, volume_24h=10000)
    signals = strategy.analyze([market], data)
    assert len(signals) == 0


def test_market_maker_skips_inactive_markets() -> None:
    strategy = MarketMaker()
    data = MagicMock()
    market = Market.from_cli(
        {
            "id": "2",
            "question": "Closed?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.5","0.5"]',
            "volume": "50000",
            "volume24hr": "20000",
            "active": False,
            "closed": True,
        }
    )
    signals = strategy.analyze([market], data)
    assert len(signals) == 0


def test_market_maker_configures_spread() -> None:
    strategy = MarketMaker()
    strategy.configure({"spread": 0.10})
    assert strategy._spread == 0.10
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_market_maker.py -v`
Expected: FAIL (import error)

**Step 3: Implement MarketMaker**

```python
"""MarketMaker strategy — provides liquidity by quoting around the midpoint."""

from __future__ import annotations

import logging
from typing import Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_SPREAD: float = 0.05
_DEFAULT_MAX_INVENTORY: float = 500.0
_DEFAULT_MIN_LIQUIDITY: float = 1000.0
_DEFAULT_ORDER_SIZE: float = 50.0


class MarketMaker(Strategy):
    """Quote bid/ask around order book midpoint for active, liquid markets.

    For each qualifying market, emits a buy signal below midpoint and a sell
    signal above midpoint, separated by the configured spread.
    """

    name: str = "market_maker"

    def __init__(self) -> None:
        self._spread: float = _DEFAULT_SPREAD
        self._max_inventory: float = _DEFAULT_MAX_INVENTORY
        self._min_liquidity: float = _DEFAULT_MIN_LIQUIDITY
        self._order_size: float = _DEFAULT_ORDER_SIZE

    def configure(self, config: dict[str, Any]) -> None:
        self._spread = float(config.get("spread", _DEFAULT_SPREAD))
        self._max_inventory = float(config.get("max_inventory", _DEFAULT_MAX_INVENTORY))
        self._min_liquidity = float(config.get("min_liquidity", _DEFAULT_MIN_LIQUIDITY))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))

    def analyze(self, markets: list[Market], data: Any) -> list[Signal]:
        signals: list[Signal] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            if market.liquidity < self._min_liquidity:
                continue
            if not market.clob_token_ids:
                continue

            try:
                book = data.get_orderbook(market.clob_token_ids[0])
            except Exception:
                logger.debug("Failed to fetch orderbook for %s, skipping", market.id)
                continue

            midpoint = book.midpoint
            if midpoint <= 0:
                continue

            buy_price = round(midpoint - self._spread / 2, 4)
            sell_price = round(midpoint + self._spread / 2, 4)

            buy_price = max(0.01, min(buy_price, 0.99))
            sell_price = max(0.01, min(sell_price, 0.99))

            token_id_yes = market.clob_token_ids[0]
            token_id_no = market.clob_token_ids[1] if len(market.clob_token_ids) > 1 else ""

            signals.append(
                Signal(
                    strategy=self.name,
                    market_id=market.id,
                    token_id=token_id_yes,
                    side="buy",
                    confidence=0.5,
                    target_price=buy_price,
                    size=self._order_size,
                    reason=f"MM bid @ {buy_price:.4f} (mid={midpoint:.4f}, spread={self._spread})",
                )
            )
            signals.append(
                Signal(
                    strategy=self.name,
                    market_id=market.id,
                    token_id=token_id_no if token_id_no else token_id_yes,
                    side="sell",
                    confidence=0.5,
                    target_price=sell_price,
                    size=self._order_size,
                    reason=f"MM ask @ {sell_price:.4f} (mid={midpoint:.4f}, spread={self._spread})",
                )
            )
        return signals
```

**Step 4: Register in orchestrator**

Add to `STRATEGY_REGISTRY` in `orchestrator.py`:
```python
from polymarket_agent.strategies.market_maker import MarketMaker

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "signal_trader": SignalTrader,
    "market_maker": MarketMaker,
}
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_market_maker.py tests/test_orchestrator.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/polymarket_agent/strategies/market_maker.py tests/test_market_maker.py src/polymarket_agent/orchestrator.py
git commit -m "feat: add MarketMaker strategy"
```

---

### Task 3: Arbitrageur Strategy

**Files:**
- Create: `src/polymarket_agent/strategies/arbitrageur.py`
- Create: `tests/test_arbitrageur.py`
- Modify: `src/polymarket_agent/orchestrator.py` (add to STRATEGY_REGISTRY)
- Modify: `src/polymarket_agent/data/client.py` (add `get_events` if not already fetching nested markets)

**Context:** The Arbitrageur compares prices across related markets within the same event. For example, if an event has "by March" at 60% and "by June" at 50%, that's a mispricing (June should be >= March). It also checks that complementary outcome prices sum to approximately 1.0.

**Step 1: Write tests**

```python
"""Tests for the Arbitrageur strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import Event, Market
from polymarket_agent.strategies.arbitrageur import Arbitrageur


def _make_market(market_id: str, yes_price: float, group_title: str = "") -> Market:
    return Market.from_cli(
        {
            "id": market_id,
            "question": f"Test market {market_id}?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(yes_price), str(round(1 - yes_price, 4))]),
            "volume": "50000",
            "volume24hr": "10000",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
            "groupItemTitle": group_title,
        }
    )


def test_arbitrageur_detects_price_sum_deviation() -> None:
    """If Yes+No prices don't sum to ~1.0, emit a signal."""
    strategy = Arbitrageur()
    strategy.configure({"price_sum_tolerance": 0.02})
    # Market where Yes=0.60, No=0.35 → sum=0.95, deviation=0.05 > tolerance
    market = Market.from_cli(
        {
            "id": "1",
            "question": "Test?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.60","0.35"]',
            "volume": "50000",
            "volume24hr": "10000",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "clobTokenIds": '["0xtok1_yes","0xtok1_no"]',
        }
    )
    data = MagicMock()
    signals = strategy.analyze([market], data)
    assert len(signals) >= 1
    assert any("price_sum" in s.reason for s in signals)


def test_arbitrageur_ignores_correct_pricing() -> None:
    """Markets with correct pricing should not generate signals."""
    strategy = Arbitrageur()
    strategy.configure({"price_sum_tolerance": 0.02})
    market = _make_market("1", yes_price=0.50)
    data = MagicMock()
    signals = strategy.analyze([market], data)
    assert len(signals) == 0


def test_arbitrageur_skips_inactive() -> None:
    strategy = Arbitrageur()
    data = MagicMock()
    market = Market.from_cli(
        {
            "id": "1",
            "question": "Closed?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.60","0.35"]',
            "volume": "50000",
            "volume24hr": "10000",
            "active": False,
            "closed": True,
        }
    )
    signals = strategy.analyze([market], data)
    assert len(signals) == 0


def test_arbitrageur_configures_tolerance() -> None:
    strategy = Arbitrageur()
    strategy.configure({"price_sum_tolerance": 0.05})
    assert strategy._price_sum_tolerance == 0.05
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_arbitrageur.py -v`
Expected: FAIL (import error)

**Step 3: Implement Arbitrageur**

```python
"""Arbitrageur strategy — exploits pricing inconsistencies within markets."""

from __future__ import annotations

import logging
from typing import Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_PRICE_SUM_TOLERANCE: float = 0.02
_DEFAULT_MIN_DEVIATION: float = 0.03
_DEFAULT_ORDER_SIZE: float = 25.0


class Arbitrageur(Strategy):
    """Detect and trade pricing inconsistencies.

    Currently checks: complementary outcome prices should sum to ~1.0.
    If the sum deviates beyond tolerance, the underpriced side is bought.
    """

    name: str = "arbitrageur"

    def __init__(self) -> None:
        self._price_sum_tolerance: float = _DEFAULT_PRICE_SUM_TOLERANCE
        self._min_deviation: float = _DEFAULT_MIN_DEVIATION
        self._order_size: float = _DEFAULT_ORDER_SIZE

    def configure(self, config: dict[str, Any]) -> None:
        self._price_sum_tolerance = float(config.get("price_sum_tolerance", _DEFAULT_PRICE_SUM_TOLERANCE))
        self._min_deviation = float(config.get("min_deviation", _DEFAULT_MIN_DEVIATION))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))

    def analyze(self, markets: list[Market], data: Any) -> list[Signal]:
        signals: list[Signal] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            signal = self._check_price_sum(market)
            if signal is not None:
                signals.append(signal)
        return signals

    def _check_price_sum(self, market: Market) -> Signal | None:
        """Check if outcome prices sum to approximately 1.0."""
        if len(market.outcome_prices) < 2:
            return None

        price_sum = sum(market.outcome_prices)
        deviation = abs(price_sum - 1.0)

        if deviation <= self._price_sum_tolerance:
            return None

        # Buy the underpriced side
        if price_sum < 1.0:
            # Outcomes are collectively underpriced — buy the cheaper one
            min_idx = market.outcome_prices.index(min(market.outcome_prices))
            side: Literal["buy", "sell"] = "buy"
            target_price = market.outcome_prices[min_idx]
            token_id = market.clob_token_ids[min_idx] if min_idx < len(market.clob_token_ids) else ""
        else:
            # Outcomes are collectively overpriced — sell the more expensive one
            max_idx = market.outcome_prices.index(max(market.outcome_prices))
            side = "sell"
            target_price = market.outcome_prices[max_idx]
            token_id = market.clob_token_ids[max_idx] if max_idx < len(market.clob_token_ids) else ""

        confidence = min(deviation / 0.1, 1.0)

        return Signal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            side=side,
            confidence=round(confidence, 4),
            target_price=target_price,
            size=self._order_size,
            reason=f"price_sum={price_sum:.4f}, deviation={deviation:.4f}",
        )
```

**Step 4: Register in orchestrator**

Add to `STRATEGY_REGISTRY`:
```python
from polymarket_agent.strategies.arbitrageur import Arbitrageur

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "signal_trader": SignalTrader,
    "market_maker": MarketMaker,
    "arbitrageur": Arbitrageur,
}
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_arbitrageur.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/polymarket_agent/strategies/arbitrageur.py tests/test_arbitrageur.py src/polymarket_agent/orchestrator.py
git commit -m "feat: add Arbitrageur strategy"
```

---

### Task 4: AIAnalyst Strategy

**Files:**
- Create: `src/polymarket_agent/strategies/ai_analyst.py`
- Create: `tests/test_ai_analyst.py`
- Modify: `src/polymarket_agent/orchestrator.py` (add to STRATEGY_REGISTRY)
- Modify: `pyproject.toml` (add `anthropic` dependency)

**Context:** The AIAnalyst sends market questions + context to Claude and asks for a probability estimate. If Claude's estimate diverges significantly from the market price, a signal is generated. The strategy is rate-limited and gracefully degrades when no API key is available.

**Step 1: Add anthropic dependency**

Run:
```bash
uv add anthropic
```

**Step 2: Write tests**

```python
"""Tests for the AIAnalyst strategy."""

import json
from unittest.mock import MagicMock, patch

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.ai_analyst import AIAnalyst


def _make_market(market_id: str = "100", yes_price: float = 0.5) -> Market:
    return Market.from_cli(
        {
            "id": market_id,
            "question": f"Will event {market_id} happen?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(yes_price), str(round(1 - yes_price, 4))]),
            "volume": "50000",
            "volume24hr": "10000",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "description": "This is a test market about whether an event will happen.",
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
        }
    )


def test_ai_analyst_generates_signal_on_divergence() -> None:
    """If AI estimate diverges from market price, emit a signal."""
    strategy = AIAnalyst()
    strategy.configure({"min_divergence": 0.10, "max_calls_per_hour": 100})

    # Mock the Anthropic client to return a probability of 0.80
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="0.80")]
    mock_client.messages.create.return_value = mock_response
    strategy._client = mock_client

    market = _make_market("1", yes_price=0.50)  # AI says 0.80, market says 0.50 → divergence 0.30
    data = MagicMock()
    signals = strategy.analyze([market], data)
    assert len(signals) == 1
    assert signals[0].side == "buy"  # AI thinks it's underpriced


def test_ai_analyst_no_signal_when_aligned() -> None:
    """If AI estimate is close to market price, no signal."""
    strategy = AIAnalyst()
    strategy.configure({"min_divergence": 0.10})

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="0.52")]
    mock_client.messages.create.return_value = mock_response
    strategy._client = mock_client

    market = _make_market("1", yes_price=0.50)  # AI says 0.52, market says 0.50 → divergence 0.02
    data = MagicMock()
    signals = strategy.analyze([market], data)
    assert len(signals) == 0


def test_ai_analyst_graceful_without_api_key() -> None:
    """Strategy should return empty signals if no API key is available."""
    with patch.dict("os.environ", {}, clear=True):
        strategy = AIAnalyst()
        strategy.configure({})
        if strategy._client is None:
            market = _make_market("1", yes_price=0.50)
            data = MagicMock()
            signals = strategy.analyze([market], data)
            assert len(signals) == 0


def test_ai_analyst_respects_rate_limit() -> None:
    """Strategy should stop calling API after hitting rate limit."""
    strategy = AIAnalyst()
    strategy.configure({"max_calls_per_hour": 2})

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="0.80")]
    mock_client.messages.create.return_value = mock_response
    strategy._client = mock_client

    markets = [_make_market(str(i), yes_price=0.50) for i in range(5)]
    data = MagicMock()
    signals = strategy.analyze(markets, data)
    # Should only call API max_calls_per_hour times
    assert mock_client.messages.create.call_count <= 2
```

**Step 3: Implement AIAnalyst**

```python
"""AIAnalyst strategy — uses Claude to estimate market probabilities."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_MODEL: str = "claude-sonnet-4-6"
_DEFAULT_MAX_CALLS_PER_HOUR: int = 20
_DEFAULT_MIN_DIVERGENCE: float = 0.15
_DEFAULT_ORDER_SIZE: float = 25.0


class AIAnalyst(Strategy):
    """Ask Claude for probability estimates and trade on divergence.

    Sends market question + description to Claude, parses a probability
    from the response. If the estimate diverges from the market price
    by more than ``min_divergence``, a buy or sell signal is generated.

    Gracefully degrades when ANTHROPIC_API_KEY is not set.
    """

    name: str = "ai_analyst"

    def __init__(self) -> None:
        self._model: str = _DEFAULT_MODEL
        self._max_calls_per_hour: int = _DEFAULT_MAX_CALLS_PER_HOUR
        self._min_divergence: float = _DEFAULT_MIN_DIVERGENCE
        self._order_size: float = _DEFAULT_ORDER_SIZE
        self._call_timestamps: list[float] = []
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        """Initialize the Anthropic client if API key is available."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.info("ANTHROPIC_API_KEY not set — AIAnalyst disabled")
            return
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — AIAnalyst disabled")

    def configure(self, config: dict[str, Any]) -> None:
        self._model = config.get("model", _DEFAULT_MODEL)
        self._max_calls_per_hour = int(config.get("max_calls_per_hour", _DEFAULT_MAX_CALLS_PER_HOUR))
        self._min_divergence = float(config.get("min_divergence", _DEFAULT_MIN_DIVERGENCE))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))

    def analyze(self, markets: list[Market], data: Any) -> list[Signal]:
        if self._client is None:
            return []

        signals: list[Signal] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            if not self._can_call():
                break
            signal = self._evaluate(market)
            if signal is not None:
                signals.append(signal)
        return signals

    def _can_call(self) -> bool:
        """Check if we're within rate limits."""
        now = time.monotonic()
        cutoff = now - 3600.0
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        return len(self._call_timestamps) < self._max_calls_per_hour

    def _evaluate(self, market: Market) -> Signal | None:
        """Ask Claude for a probability estimate and compare to market price."""
        if not market.outcome_prices or not market.clob_token_ids:
            return None

        yes_price = market.outcome_prices[0]

        prompt = (
            f"You are a prediction market analyst. Estimate the probability (0.0 to 1.0) "
            f"that the following question resolves to Yes.\n\n"
            f"Question: {market.question}\n"
        )
        if market.description:
            prompt += f"Description: {market.description}\n"
        prompt += (
            f"\nCurrent market price: {yes_price:.2f}\n"
            f"Respond with ONLY a single decimal number between 0.0 and 1.0."
        )

        try:
            self._call_timestamps.append(time.monotonic())
            response = self._client.messages.create(
                model=self._model,
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # Extract first decimal number from response
            match = re.search(r"(\d+\.?\d*)", text)
            if not match:
                logger.warning("Could not parse probability from AI response: %s", text)
                return None
            estimate = float(match.group(1))
            estimate = max(0.0, min(1.0, estimate))
        except Exception:
            logger.exception("AI analyst call failed for market %s", market.id)
            return None

        divergence = estimate - yes_price

        if abs(divergence) < self._min_divergence:
            return None

        if divergence > 0:
            side: Literal["buy", "sell"] = "buy"
            token_id = market.clob_token_ids[0]
        else:
            side = "sell"
            token_id = market.clob_token_ids[1] if len(market.clob_token_ids) > 1 else market.clob_token_ids[0]

        confidence = min(abs(divergence) / 0.3, 1.0)

        return Signal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            side=side,
            confidence=round(confidence, 4),
            target_price=yes_price,
            size=self._order_size,
            reason=f"ai_estimate={estimate:.4f}, market={yes_price:.4f}, div={divergence:+.4f}",
        )
```

**Step 4: Register in orchestrator**

Add to `STRATEGY_REGISTRY`:
```python
from polymarket_agent.strategies.ai_analyst import AIAnalyst

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "signal_trader": SignalTrader,
    "market_maker": MarketMaker,
    "arbitrageur": Arbitrageur,
    "ai_analyst": AIAnalyst,
}
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_ai_analyst.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/polymarket_agent/strategies/ai_analyst.py tests/test_ai_analyst.py src/polymarket_agent/orchestrator.py pyproject.toml uv.lock
git commit -m "feat: add AIAnalyst strategy with Claude integration"
```

---

### Task 5: Signal Aggregation

**Files:**
- Create: `src/polymarket_agent/strategies/aggregator.py`
- Create: `tests/test_aggregator.py`
- Modify: `src/polymarket_agent/orchestrator.py` (integrate aggregation step)

**Context:** When multiple strategies fire on the same market, the orchestrator should aggregate signals. Rules: (1) Deduplicate by market+side — keep highest confidence. (2) Apply a minimum confidence threshold. (3) Optionally require N strategies to agree before executing.

**Step 1: Write tests**

```python
"""Tests for signal aggregation."""

from polymarket_agent.strategies.aggregator import aggregate_signals
from polymarket_agent.strategies.base import Signal


def _signal(strategy: str, market_id: str, side: str = "buy", confidence: float = 0.8) -> Signal:
    return Signal(
        strategy=strategy,
        market_id=market_id,
        token_id=f"0xtok_{market_id}",
        side=side,
        confidence=confidence,
        target_price=0.5,
        size=25.0,
        reason="test",
    )


def test_deduplicates_same_market_same_side() -> None:
    signals = [
        _signal("A", "1", "buy", confidence=0.6),
        _signal("B", "1", "buy", confidence=0.9),
    ]
    result = aggregate_signals(signals, min_confidence=0.0, min_strategies=1)
    assert len(result) == 1
    assert result[0].confidence == 0.9
    assert result[0].strategy == "B"


def test_keeps_different_sides() -> None:
    signals = [
        _signal("A", "1", "buy", confidence=0.8),
        _signal("B", "1", "sell", confidence=0.7),
    ]
    result = aggregate_signals(signals, min_confidence=0.0, min_strategies=1)
    assert len(result) == 2


def test_filters_below_min_confidence() -> None:
    signals = [
        _signal("A", "1", "buy", confidence=0.3),
        _signal("B", "2", "buy", confidence=0.8),
    ]
    result = aggregate_signals(signals, min_confidence=0.5, min_strategies=1)
    assert len(result) == 1
    assert result[0].market_id == "2"


def test_requires_min_strategies() -> None:
    signals = [
        _signal("A", "1", "buy", confidence=0.8),
        _signal("B", "2", "buy", confidence=0.8),
        _signal("C", "2", "buy", confidence=0.7),
    ]
    result = aggregate_signals(signals, min_confidence=0.0, min_strategies=2)
    # Only market "2" has 2 strategies agreeing
    assert len(result) == 1
    assert result[0].market_id == "2"


def test_empty_input() -> None:
    result = aggregate_signals([], min_confidence=0.0, min_strategies=1)
    assert result == []
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_aggregator.py -v`
Expected: FAIL

**Step 3: Implement aggregator**

```python
"""Signal aggregation — deduplication, filtering, and consensus."""

from __future__ import annotations

from polymarket_agent.strategies.base import Signal


def aggregate_signals(
    signals: list[Signal],
    *,
    min_confidence: float = 0.5,
    min_strategies: int = 1,
) -> list[Signal]:
    """Aggregate signals from multiple strategies.

    1. Group signals by (market_id, side).
    2. Filter groups that don't meet min_strategies threshold.
    3. For each group, keep the signal with highest confidence.
    4. Filter by min_confidence.
    """
    if not signals:
        return []

    # Group by (market_id, side)
    groups: dict[tuple[str, str], list[Signal]] = {}
    for signal in signals:
        key = (signal.market_id, signal.side)
        groups.setdefault(key, []).append(signal)

    result: list[Signal] = []
    for _key, group in groups.items():
        if len(group) < min_strategies:
            continue
        best = max(group, key=lambda s: s.confidence)
        if best.confidence >= min_confidence:
            result.append(best)

    return result
```

**Step 4: Integrate into orchestrator**

Update `orchestrator.py` `tick()` method to call `aggregate_signals()` before execution:

```python
from polymarket_agent.strategies.aggregator import aggregate_signals

# In tick(), after collecting signals:
signals = aggregate_signals(signals, min_confidence=0.5, min_strategies=1)
```

**Step 5: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All pass (existing + new)

**Step 6: Commit**

```bash
git add src/polymarket_agent/strategies/aggregator.py tests/test_aggregator.py src/polymarket_agent/orchestrator.py
git commit -m "feat: add signal aggregation with dedup and confidence filtering"
```

---

### Task 6: Update Config for New Strategies

**Files:**
- Modify: `config.yaml`
- Modify: `src/polymarket_agent/config.py`
- Modify: `tests/test_config.py`

**Context:** Add configuration entries for the three new strategies and aggregation settings.

**Step 1: Update config.yaml**

```yaml
mode: paper
starting_balance: 1000.0
poll_interval: 60

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
    order_size: 25
  ai_analyst:
    enabled: false
    model: claude-sonnet-4-6
    max_calls_per_hour: 20
    min_divergence: 0.15

aggregation:
  min_confidence: 0.5
  min_strategies: 1

risk:
  max_position_size: 100.0
  max_daily_loss: 50.0
  max_open_orders: 10
```

**Step 2: Update AppConfig**

Add `AggregationConfig` to `config.py`:
```python
class AggregationConfig(BaseModel):
    min_confidence: float = 0.5
    min_strategies: int = 1

class AppConfig(BaseModel):
    # ... existing fields ...
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
```

**Step 3: Update orchestrator to use aggregation config**

Pass `config.aggregation.min_confidence` and `config.aggregation.min_strategies` to `aggregate_signals()`.

**Step 4: Add test for new config**

```python
def test_aggregation_config_defaults() -> None:
    config = AppConfig()
    assert config.aggregation.min_confidence == 0.5
    assert config.aggregation.min_strategies == 1
```

**Step 5: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All pass

**Step 6: Commit**

```bash
git add config.yaml src/polymarket_agent/config.py tests/test_config.py src/polymarket_agent/orchestrator.py
git commit -m "feat: add config for new strategies and signal aggregation"
```

---

### Task 7: Integration Test — Full Pipeline with All Strategies

**Files:**
- Create: `tests/test_integration.py`

**Context:** End-to-end test running all strategies through the orchestrator with mocked CLI data.

**Step 1: Write integration test**

```python
"""Integration test — full pipeline with all strategies."""

import json
import subprocess
import tempfile
from pathlib import Path

from polymarket_agent.config import AppConfig
from polymarket_agent.orchestrator import Orchestrator

MOCK_MARKETS = json.dumps(
    [
        {
            "id": "100",
            "question": "Will it rain tomorrow?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.30","0.70"]',
            "volume": "80000",
            "volume24hr": "15000",
            "liquidity": "10000",
            "active": True,
            "closed": False,
            "clobTokenIds": '["0xtok1","0xtok2"]',
            "description": "Weather prediction market",
        },
        {
            "id": "101",
            "question": "Will BTC hit 100k?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.60","0.35"]',  # Sum = 0.95 → arb opportunity
            "volume": "200000",
            "volume24hr": "50000",
            "liquidity": "30000",
            "active": True,
            "closed": False,
            "clobTokenIds": '["0xtok3","0xtok4"]',
        },
    ]
)

MOCK_BOOK = json.dumps(
    {
        "bids": [{"price": "0.28", "size": "500"}],
        "asks": [{"price": "0.32", "size": "500"}],
    }
)


def _mock_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    cmd = " ".join(args)
    if "clob book" in cmd:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=MOCK_BOOK, stderr="")
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=MOCK_MARKETS, stderr="")


def test_full_pipeline_paper_mode(mocker: object) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            strategies={
                "signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05},
                "arbitrageur": {"enabled": True, "price_sum_tolerance": 0.02},
            },
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        result = orch.tick()
        assert result["markets_fetched"] == 2
        assert result["signals_generated"] >= 1
        portfolio = orch.get_portfolio()
        assert portfolio.balance <= 1000.0 or result["trades_executed"] == 0


def test_full_pipeline_monitor_mode(mocker: object) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="monitor",
            strategies={
                "signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05},
                "arbitrageur": {"enabled": True},
            },
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        result = orch.tick()
        assert result["trades_executed"] == 0
        assert result["signals_generated"] >= 0
```

**Step 2: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All pass

**Step 3: Run ruff + mypy**

Run:
```bash
uv run ruff check src/
uv run mypy src/
```
Expected: Clean

**Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration test for full multi-strategy pipeline"
```

---

### Task 8: Final Verification

**Step 1: Run full test suite with coverage**

Run: `uv run pytest tests/ -v --cov=src/polymarket_agent`

**Step 2: Run linting**

Run: `uv run ruff check src/ tests/`

**Step 3: Run type checking**

Run: `uv run mypy src/`

**Step 4: Smoke test with live data**

Run: `uv run polymarket-agent tick`
Expected: Fetches markets, generates signals from multiple strategies, executes paper trades.

**Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "chore: Phase 2 cleanup and verification"
```
