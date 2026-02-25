"""Tests for the Arbitrageur strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.arbitrageur import Arbitrageur


def _make_market(
    market_id: str,
    yes_price: float,
    no_price: float | None = None,
    active: bool = True,
    closed: bool = False,
) -> Market:
    if no_price is None:
        no_price = round(1 - yes_price, 4)
    return Market.from_cli(
        {
            "id": market_id,
            "question": f"Test market {market_id}?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(yes_price), str(no_price)]),
            "volume": "50000",
            "volume24hr": "10000",
            "liquidity": "5000",
            "active": active,
            "closed": closed,
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
        }
    )


def test_arbitrageur_detects_price_sum_deviation() -> None:
    """If Yes+No prices don't sum to ~1.0, emit a signal."""
    strategy = Arbitrageur()
    strategy.configure({"price_sum_tolerance": 0.02})
    # Yes=0.60, No=0.35 -> sum=0.95, deviation=0.05 > tolerance
    market = _make_market("1", yes_price=0.60, no_price=0.35)
    signals = strategy.analyze([market], MagicMock())
    assert len(signals) >= 1
    assert any("price_sum" in s.reason for s in signals)


def test_arbitrageur_ignores_correct_pricing() -> None:
    """Markets with correct pricing should not generate signals."""
    strategy = Arbitrageur()
    strategy.configure({"price_sum_tolerance": 0.02})
    signals = strategy.analyze([_make_market("1", yes_price=0.50)], MagicMock())
    assert signals == []


def test_arbitrageur_skips_inactive() -> None:
    strategy = Arbitrageur()
    market = _make_market("1", yes_price=0.60, no_price=0.35, active=False, closed=True)
    signals = strategy.analyze([market], MagicMock())
    assert signals == []


def test_arbitrageur_configures_tolerance() -> None:
    strategy = Arbitrageur()
    strategy.configure({"price_sum_tolerance": 0.05})
    assert strategy._price_sum_tolerance == 0.05


def test_arbitrageur_skips_signal_when_token_id_missing() -> None:
    strategy = Arbitrageur()
    market = _make_market("2", yes_price=0.60, no_price=0.35)
    market.clob_token_ids = []

    signals = strategy.analyze([market], MagicMock())

    assert signals == []
