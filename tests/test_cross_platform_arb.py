"""Tests for the CrossPlatformArb strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import CrossPlatformPrice, Market
from polymarket_agent.strategies.cross_platform_arb import CrossPlatformArb


def _make_market(
    market_id: str = "100",
    question: str = "Will the US GDP grow by 3% in 2026?",
    yes_price: float = 0.5,
) -> Market:
    return Market.from_cli(
        {
            "id": market_id,
            "question": question,
            "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(yes_price), str(round(1 - yes_price, 4))]),
            "volume": "50000",
            "volume24hr": "10000",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
        }
    )


def _make_ext_price(
    question: str = "Will US GDP grow by 3% in 2026?",
    probability: float = 0.7,
    platform: str = "kalshi",
) -> CrossPlatformPrice:
    return CrossPlatformPrice(platform=platform, question=question, probability=probability)


def test_arb_generates_buy_signal() -> None:
    """Signal when external price is higher than Polymarket (Yes underpriced)."""
    strategy = CrossPlatformArb()
    strategy.configure(
        {
            "min_divergence": 0.05,
            "similarity_threshold": 0.5,
            "order_size": 25.0,
            "polymarket_fee": 0.02,
            "external_fee": 0.03,
        }
    )

    # Mock external prices
    strategy._kalshi.get_active_events = MagicMock(
        return_value=[_make_ext_price("Will the US GDP grow by 3% in 2026?", 0.75)]
    )
    strategy._metaculus.get_active_questions = MagicMock(return_value=[])

    market = _make_market("100", "Will the US GDP grow by 3% in 2026?", 0.5)
    signals = strategy.analyze([market], MagicMock())

    assert len(signals) == 1
    assert signals[0].side == "buy"
    assert signals[0].strategy == "cross_platform_arb"


def test_arb_generates_sell_signal() -> None:
    """Signal when external price is lower than Polymarket (Yes overpriced)."""
    strategy = CrossPlatformArb()
    strategy.configure(
        {
            "min_divergence": 0.05,
            "similarity_threshold": 0.5,
            "polymarket_fee": 0.02,
            "external_fee": 0.03,
        }
    )

    strategy._kalshi.get_active_events = MagicMock(
        return_value=[_make_ext_price("Will the US GDP grow by 3% in 2026?", 0.25)]
    )
    strategy._metaculus.get_active_questions = MagicMock(return_value=[])

    market = _make_market("100", "Will the US GDP grow by 3% in 2026?", 0.5)
    signals = strategy.analyze([market], MagicMock())

    assert len(signals) == 1
    assert signals[0].side == "sell"


def test_arb_no_signal_within_fee_threshold() -> None:
    """No signal when divergence is within fee threshold."""
    strategy = CrossPlatformArb()
    strategy.configure(
        {
            "min_divergence": 0.05,
            "similarity_threshold": 0.5,
            "polymarket_fee": 0.02,
            "external_fee": 0.03,
        }
    )

    # Divergence = 0.55 - 0.50 = 0.05, fee_threshold = 0.05 + 0.02 + 0.03 = 0.10
    strategy._kalshi.get_active_events = MagicMock(
        return_value=[_make_ext_price("Will the US GDP grow by 3% in 2026?", 0.55)]
    )
    strategy._metaculus.get_active_questions = MagicMock(return_value=[])

    market = _make_market("100", "Will the US GDP grow by 3% in 2026?", 0.5)
    signals = strategy.analyze([market], MagicMock())
    assert len(signals) == 0


def test_arb_no_match_below_similarity() -> None:
    """No signal when question similarity is below threshold."""
    strategy = CrossPlatformArb()
    strategy.configure({"similarity_threshold": 0.9})

    strategy._kalshi.get_active_events = MagicMock(
        return_value=[_make_ext_price("Completely different question about weather", 0.75)]
    )
    strategy._metaculus.get_active_questions = MagicMock(return_value=[])

    market = _make_market("100", "Will the US GDP grow by 3% in 2026?", 0.5)
    signals = strategy.analyze([market], MagicMock())
    assert len(signals) == 0


def test_arb_no_external_prices() -> None:
    """No signals when no external prices are available."""
    strategy = CrossPlatformArb()

    strategy._kalshi.get_active_events = MagicMock(return_value=[])
    strategy._metaculus.get_active_questions = MagicMock(return_value=[])

    signals = strategy.analyze([_make_market()], MagicMock())
    assert len(signals) == 0


def test_arb_handles_api_failure() -> None:
    """Strategy should handle external API failures gracefully."""
    strategy = CrossPlatformArb()

    strategy._kalshi.get_active_events = MagicMock(side_effect=RuntimeError("API down"))
    strategy._metaculus.get_active_questions = MagicMock(side_effect=RuntimeError("API down"))

    signals = strategy.analyze([_make_market()], MagicMock())
    assert len(signals) == 0


def test_arb_picks_best_match() -> None:
    """Strategy should use the highest-similarity match."""
    strategy = CrossPlatformArb()
    strategy.configure(
        {
            "min_divergence": 0.01,
            "similarity_threshold": 0.5,
            "polymarket_fee": 0.0,
            "external_fee": 0.0,
        }
    )

    strategy._kalshi.get_active_events = MagicMock(
        return_value=[
            _make_ext_price("Will GDP grow in 2026?", 0.70),  # lower similarity
            _make_ext_price("Will the US GDP grow by 3% in 2026?", 0.80),  # higher similarity
        ]
    )
    strategy._metaculus.get_active_questions = MagicMock(return_value=[])

    market = _make_market("100", "Will the US GDP grow by 3% in 2026?", 0.5)
    signals = strategy.analyze([market], MagicMock())

    assert len(signals) == 1
    assert "0.80" in signals[0].reason  # Should use the 0.80 price from better match


def test_arb_configure_updates_fields() -> None:
    """configure() should update all strategy parameters."""
    strategy = CrossPlatformArb()
    strategy.configure(
        {
            "min_divergence": 0.10,
            "similarity_threshold": 0.70,
            "order_size": 50.0,
            "polymarket_fee": 0.05,
            "external_fee": 0.05,
        }
    )
    assert strategy._min_divergence == 0.10
    assert strategy._similarity_threshold == 0.70
    assert strategy._order_size == 50.0
