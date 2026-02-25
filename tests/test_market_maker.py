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
            "outcomes": '[\"Yes\",\"No\"]',
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
            "outcomes": '[\"Yes\",\"No\"]',
            "outcomePrices": '[\"0.5\",\"0.5\"]',
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
