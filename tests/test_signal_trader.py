"""Tests for the SignalTrader strategy."""

import json

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.signal_trader import SignalTrader


def _make_market(market_id: str, yes_price: float, volume_24h: float, volume: float = 100000) -> Market:
    return Market.from_cli(
        {
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
        }
    )


def test_signal_trader_flags_high_volume_markets() -> None:
    strategy = SignalTrader()
    strategy.configure({"volume_threshold": 5000, "price_move_threshold": 0.05})
    markets = [
        _make_market("1", 0.3, volume_24h=10000),
        _make_market("2", 0.5, volume_24h=1000),
    ]
    signals = strategy.analyze(markets, data=None)
    market_ids = [s.market_id for s in signals]
    assert "1" in market_ids
    assert "2" not in market_ids


def test_signal_trader_skips_closed_markets() -> None:
    strategy = SignalTrader()
    strategy.configure({"volume_threshold": 1000, "price_move_threshold": 0.05})
    market = Market.from_cli(
        {
            "id": "99",
            "question": "Closed?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.5","0.5"]',
            "volume": "50000",
            "volume24hr": "20000",
            "active": False,
            "closed": True,
        }
    )
    signals = strategy.analyze([market], data=None)
    assert len(signals) == 0


def test_signal_trader_respects_thresholds() -> None:
    strategy = SignalTrader()
    strategy.configure({"volume_threshold": 50000, "price_move_threshold": 0.1})
    assert strategy._volume_threshold == 50000
    assert strategy._price_move_threshold == 0.1


def test_signal_trader_skips_market_with_no_token_ids() -> None:
    """Markets without clob_token_ids should not emit signals."""
    strategy = SignalTrader()
    strategy.configure({"volume_threshold": 1000, "price_move_threshold": 0.05})
    market = Market.from_cli(
        {
            "id": "50",
            "question": "No tokens?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.3","0.7"]',
            "volume": "100000",
            "volume24hr": "20000",
            "active": True,
            "closed": False,
        }
    )
    signals = strategy.analyze([market], data=None)
    assert len(signals) == 0
