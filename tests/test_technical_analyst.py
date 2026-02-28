"""Tests for the TechnicalAnalyst strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import Market, PricePoint
from polymarket_agent.strategies.technical_analyst import TechnicalAnalyst


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
            "description": "Test market",
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
        }
    )


def _make_price_history(values: list[float]) -> list[PricePoint]:
    return [PricePoint(timestamp=f"2026-02-{i:02d}T00:00:00Z", price=v) for i, v in enumerate(values)]


def _make_data_provider(prices: list[float]) -> MagicMock:
    data = MagicMock()
    data.get_price_history.return_value = _make_price_history(prices)
    return data


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------


def test_generates_buy_on_bullish_crossover() -> None:
    # Choppy prices followed by an uptrend — creates bullish crossover
    # without pushing RSI to overbought (100)
    prices = [
        0.40,
        0.38,
        0.42,
        0.39,
        0.41,
        0.37,
        0.40,
        0.38,
        0.36,
        0.39,
        0.37,
        0.35,
        0.38,
        0.36,
        0.34,
        0.37,
        0.39,
        0.42,
        0.44,
        0.46,
        0.49,
        0.48,
        0.51,
        0.53,
        0.50,
        0.54,
        0.56,
        0.55,
        0.58,
        0.60,
    ]
    data = _make_data_provider(prices)
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([_make_market("1", yes_price=0.5)], data)
    assert len(signals) == 1
    assert signals[0].side == "buy"
    assert signals[0].strategy == "technical_analyst"


def test_generates_sell_on_bearish_crossover() -> None:
    # Choppy prices followed by a downtrend — creates bearish crossover
    # without pushing RSI to oversold (0)
    prices = [
        0.60,
        0.62,
        0.58,
        0.61,
        0.59,
        0.63,
        0.60,
        0.62,
        0.64,
        0.61,
        0.63,
        0.65,
        0.62,
        0.64,
        0.66,
        0.63,
        0.61,
        0.58,
        0.56,
        0.54,
        0.51,
        0.53,
        0.49,
        0.47,
        0.50,
        0.46,
        0.44,
        0.45,
        0.42,
        0.40,
    ]
    data = _make_data_provider(prices)
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([_make_market("1", yes_price=0.5)], data)
    assert len(signals) == 1
    assert signals[0].side == "sell"


def test_no_signal_on_flat_prices() -> None:
    prices = [0.5] * 30
    data = _make_data_provider(prices)
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([_make_market("1", yes_price=0.5)], data)
    assert len(signals) == 0


def test_no_signal_insufficient_data() -> None:
    prices = [0.5] * 10  # Less than 21 required
    data = _make_data_provider(prices)
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([_make_market("1", yes_price=0.5)], data)
    assert len(signals) == 0


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_skips_near_resolved_markets() -> None:
    # Price < 0.05 → skip
    prices = [0.3 + 0.02 * i for i in range(30)]
    data = _make_data_provider(prices)
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([_make_market("1", yes_price=0.03)], data)
    assert len(signals) == 0


def test_skips_high_price_markets() -> None:
    # Price > 0.95 → skip
    prices = [0.3 + 0.02 * i for i in range(30)]
    data = _make_data_provider(prices)
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([_make_market("1", yes_price=0.97)], data)
    assert len(signals) == 0


def test_skips_closed_markets() -> None:
    market = _make_market("1", yes_price=0.5)
    market.closed = True
    data = _make_data_provider([0.3 + 0.02 * i for i in range(30)])
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([market], data)
    assert len(signals) == 0


def test_skips_inactive_markets() -> None:
    market = _make_market("1", yes_price=0.5)
    market.active = False
    data = _make_data_provider([0.3 + 0.02 * i for i in range(30)])
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([market], data)
    assert len(signals) == 0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_configure_sets_parameters() -> None:
    strategy = TechnicalAnalyst()
    strategy.configure(
        {
            "ema_fast_period": 5,
            "ema_slow_period": 15,
            "rsi_period": 10,
            "history_interval": "1d",
            "history_fidelity": 30,
            "order_size": 50.0,
        }
    )
    assert strategy._ema_fast_period == 5
    assert strategy._ema_slow_period == 15
    assert strategy._rsi_period == 10
    assert strategy._history_interval == "1d"
    assert strategy._history_fidelity == 30
    assert strategy._order_size == 50.0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_handles_price_history_exception() -> None:
    data = MagicMock()
    data.get_price_history.side_effect = RuntimeError("CLI failed")
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([_make_market("1", yes_price=0.5)], data)
    assert len(signals) == 0


# ---------------------------------------------------------------------------
# Signal properties
# ---------------------------------------------------------------------------


def test_signal_contains_reason_with_indicators() -> None:
    prices = [
        0.40,
        0.38,
        0.42,
        0.39,
        0.41,
        0.37,
        0.40,
        0.38,
        0.36,
        0.39,
        0.37,
        0.35,
        0.38,
        0.36,
        0.34,
        0.37,
        0.39,
        0.42,
        0.44,
        0.46,
        0.49,
        0.48,
        0.51,
        0.53,
        0.50,
        0.54,
        0.56,
        0.55,
        0.58,
        0.60,
    ]
    data = _make_data_provider(prices)
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([_make_market("1", yes_price=0.5)], data)
    assert len(signals) == 1
    reason = signals[0].reason
    assert "ema_cross=" in reason
    assert "rsi=" in reason
    assert "trend=" in reason


def test_confidence_between_zero_and_one() -> None:
    prices = [
        0.40,
        0.38,
        0.42,
        0.39,
        0.41,
        0.37,
        0.40,
        0.38,
        0.36,
        0.39,
        0.37,
        0.35,
        0.38,
        0.36,
        0.34,
        0.37,
        0.39,
        0.42,
        0.44,
        0.46,
        0.49,
        0.48,
        0.51,
        0.53,
        0.50,
        0.54,
        0.56,
        0.55,
        0.58,
        0.60,
    ]
    data = _make_data_provider(prices)
    strategy = TechnicalAnalyst()

    signals = strategy.analyze([_make_market("1", yes_price=0.5)], data)
    for signal in signals:
        assert 0.0 <= signal.confidence <= 1.0
