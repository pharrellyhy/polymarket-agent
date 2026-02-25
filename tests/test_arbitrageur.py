"""Tests for the Arbitrageur strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.arbitrageur import Arbitrageur


def _make_market(market_id: str, yes_price: float, group_title: str = "") -> Market:
    return Market.from_cli(
        {
            "id": market_id,
            "question": f"Test market {market_id}?",
            "outcomes": '[\"Yes\",\"No\"]',
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
    # Market where Yes=0.60, No=0.35 -> sum=0.95, deviation=0.05 > tolerance
    market = Market.from_cli(
        {
            "id": "1",
            "question": "Test?",
            "outcomes": '[\"Yes\",\"No\"]',
            "outcomePrices": '[\"0.60\",\"0.35\"]',
            "volume": "50000",
            "volume24hr": "10000",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "clobTokenIds": '[\"0xtok1_yes\",\"0xtok1_no\"]',
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
            "outcomes": '[\"Yes\",\"No\"]',
            "outcomePrices": '[\"0.60\",\"0.35\"]',
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
