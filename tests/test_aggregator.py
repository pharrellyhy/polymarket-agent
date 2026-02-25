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
    assert len(result) == 1
    assert result[0].market_id == "2"


def test_empty_input() -> None:
    result = aggregate_signals([], min_confidence=0.0, min_strategies=1)
    assert result == []
