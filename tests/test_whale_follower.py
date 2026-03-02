"""Tests for the WhaleFollower strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import Market, Trader
from polymarket_agent.strategies.whale_follower import WhaleFollower


def _make_market(market_id: str = "100", yes_price: float = 0.5, slug: str = "") -> Market:
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
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
            "slug": slug or f"event-{market_id}",
        }
    )


def _make_trader(name: str = "whale1", rank: int = 1) -> Trader:
    return Trader(rank=rank, name=name, address="0xabc123", volume=100000.0, pnl=5000.0, markets_traded=50)


def _cli_trade(condition_id: str = "100", slug: str = "event-100", side: str = "BUY", size: float = 500) -> dict:
    """Create a mock CLI trade dict matching `polymarket data trades` output."""
    return {
        "condition_id": condition_id,
        "slug": slug,
        "side": side,
        "size": str(size),
        "price": "0.5",
        "timestamp": 1772382625,
        "title": "Test market",
        "proxy_wallet": "0xabc123",
        "outcome": "Yes",
        "outcome_index": 0,
        "transaction_hash": "0xdeadbeef",
    }


def test_whale_follower_generates_signal() -> None:
    """WhaleFollower should emit a signal when a top trader makes a large trade."""
    strategy = WhaleFollower()
    strategy.configure({"min_trade_size": 100.0, "order_size": 25.0, "top_n": 5})

    data = MagicMock()
    data.get_leaderboard.return_value = [_make_trader("whale1", 1)]
    data.get_trader_trades.return_value = [_cli_trade("100", "event-100", "BUY", 500)]

    markets = [_make_market("100", slug="event-100")]
    signals = strategy.analyze(markets, data)
    assert len(signals) == 1
    assert signals[0].side == "buy"
    assert signals[0].strategy == "whale_follower"


def test_whale_follower_no_signal_small_trade() -> None:
    """WhaleFollower should not emit a signal for trades below min_trade_size."""
    strategy = WhaleFollower()
    strategy.configure({"min_trade_size": 1000.0, "order_size": 25.0})

    data = MagicMock()
    data.get_leaderboard.return_value = [_make_trader("whale1", 1)]
    data.get_trader_trades.return_value = [_cli_trade("100", "event-100", "BUY", 50)]

    markets = [_make_market("100", slug="event-100")]
    signals = strategy.analyze(markets, data)
    assert len(signals) == 0


def test_whale_follower_deduplicates() -> None:
    """Repeated signals for the same trader/market should be deduplicated."""
    strategy = WhaleFollower()
    strategy.configure({"min_trade_size": 100.0, "order_size": 25.0})

    data = MagicMock()
    data.get_leaderboard.return_value = [_make_trader("whale1", 1)]
    data.get_trader_trades.return_value = [
        _cli_trade("100", "event-100", "BUY", 500),
        _cli_trade("100", "event-100", "BUY", 600),
    ]

    markets = [_make_market("100", slug="event-100")]
    signals = strategy.analyze(markets, data)
    assert len(signals) == 1  # Only one signal despite two trades


def test_whale_follower_confidence_inversely_proportional_to_rank() -> None:
    """Confidence should decrease with higher rank numbers."""
    strategy = WhaleFollower()
    strategy.configure({"min_trade_size": 100.0, "order_size": 25.0})

    data = MagicMock()
    data.get_leaderboard.return_value = [_make_trader("whale1", 1), _make_trader("whale10", 10)]
    data.get_trader_trades.side_effect = [
        [_cli_trade("100", "event-100", "BUY", 500)],
        [_cli_trade("200", "event-200", "BUY", 500)],
    ]

    markets = [_make_market("100", slug="event-100"), _make_market("200", slug="event-200")]
    signals = strategy.analyze(markets, data)

    if len(signals) == 2:
        rank1_signal = next(s for s in signals if "rank 1" in s.reason)
        rank10_signal = next(s for s in signals if "rank 10" in s.reason)
        assert rank1_signal.confidence > rank10_signal.confidence


def test_whale_follower_no_leaderboard() -> None:
    """WhaleFollower should return empty signals when leaderboard is empty."""
    strategy = WhaleFollower()
    data = MagicMock()
    data.get_leaderboard.return_value = []

    signals = strategy.analyze([_make_market()], data)
    assert len(signals) == 0


def test_whale_follower_skips_unknown_market() -> None:
    """WhaleFollower should skip trades for markets not in the active list."""
    strategy = WhaleFollower()
    strategy.configure({"min_trade_size": 100.0})

    data = MagicMock()
    data.get_leaderboard.return_value = [_make_trader("whale1", 1)]
    data.get_trader_trades.return_value = [_cli_trade("999", "event-999", "BUY", 500)]

    markets = [_make_market("100", slug="event-100")]  # Market 999 not in list
    signals = strategy.analyze(markets, data)
    assert len(signals) == 0


def test_whale_follower_does_not_dedup_before_market_match() -> None:
    """Trades for unknown markets should not consume dedup state."""
    strategy = WhaleFollower()
    strategy.configure({"min_trade_size": 100.0})

    data = MagicMock()
    data.get_leaderboard.return_value = [_make_trader("whale1", 1)]
    data.get_trader_trades.return_value = [_cli_trade("999", "event-999", "BUY", 500)]

    # First run: market 999 is not active, so no signal should be emitted.
    assert strategy.analyze([_make_market("100", slug="event-100")], data) == []

    # Second run: once market 999 is active, the same trade should still produce a signal.
    signals = strategy.analyze([_make_market("999", slug="event-999")], data)
    assert len(signals) == 1
