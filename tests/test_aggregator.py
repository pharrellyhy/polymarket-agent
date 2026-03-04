"""Tests for signal aggregation."""

from typing import Literal

from polymarket_agent.strategies.aggregator import aggregate_signals
from polymarket_agent.strategies.base import Signal


def _signal(
    strategy: str,
    market_id: str,
    side: Literal["buy", "sell"] = "buy",
    confidence: float = 0.8,
    token_id: str | None = None,
) -> Signal:
    return Signal(
        strategy=strategy,
        market_id=market_id,
        token_id=token_id or f"0xtok_{market_id}",
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
    # Blended confidence: (0.6 + 0.9) / 2 = 0.75
    assert result[0].confidence == 0.75
    assert result[0].strategy == "B"  # reason from best signal


def test_conflicting_sides_suppressed() -> None:
    """When strategies disagree on side for the same market+token, suppress both."""
    signals = [
        _signal("A", "1", "buy", confidence=0.8),
        _signal("B", "1", "sell", confidence=0.7),
    ]
    result = aggregate_signals(signals, min_confidence=0.0, min_strategies=1)
    assert len(result) == 0


def test_keeps_different_sides_different_tokens() -> None:
    """Different tokens in the same market are not considered conflicting."""
    signals = [
        _signal("A", "1", "buy", confidence=0.8, token_id="yes-token"),
        _signal("B", "1", "sell", confidence=0.7, token_id="no-token"),
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
    # Blended confidence: (0.8 + 0.7) / 2 = 0.75
    assert result[0].confidence == 0.75


def test_empty_input() -> None:
    result = aggregate_signals([], min_confidence=0.0, min_strategies=1)
    assert result == []


def test_does_not_merge_different_tokens_same_market_and_side() -> None:
    """Signals for different tokens in the same market are kept separate."""
    signals = [
        _signal("ai_analyst", "1", "sell", confidence=0.9, token_id="yes-token"),
        _signal("signal_trader", "1", "sell", confidence=0.8, token_id="no-token"),
    ]
    result = aggregate_signals(signals, min_confidence=0.0, min_strategies=1)
    assert len(result) == 2
    assert {s.token_id for s in result} == {"yes-token", "no-token"}


def test_min_strategies_counts_unique_strategies() -> None:
    """Duplicate signals from the same strategy count as one for min_strategies."""
    signals = [
        _signal("A", "1", "buy", confidence=0.9),
        _signal("A", "1", "buy", confidence=0.8),
    ]

    result = aggregate_signals(signals, min_confidence=0.0, min_strategies=2)

    assert result == []


def test_conflict_resolution_disabled_keeps_both_sides() -> None:
    """When conflict_resolution=False, opposing signals are NOT suppressed."""
    signals = [
        _signal("A", "1", "buy", confidence=0.8),
        _signal("B", "1", "sell", confidence=0.7),
    ]
    result = aggregate_signals(
        signals, min_confidence=0.0, min_strategies=1, conflict_resolution=False
    )
    assert len(result) == 2


def test_blend_confidence_disabled_uses_max() -> None:
    """When blend_confidence=False, winner-takes-all confidence is used."""
    signals = [
        _signal("A", "1", "buy", confidence=0.6),
        _signal("B", "1", "buy", confidence=0.9),
    ]
    result = aggregate_signals(
        signals, min_confidence=0.0, min_strategies=1, blend_confidence=False
    )
    assert len(result) == 1
    assert result[0].confidence == 0.9


# ------------------------------------------------------------------
# Weighted aggregation (Phase G)
# ------------------------------------------------------------------


def test_weighted_blending_favors_accurate_strategy() -> None:
    """When strategy_weights is provided, confidence blending is weighted."""
    signals = [
        _signal("accurate", "1", "buy", confidence=0.9),
        _signal("inaccurate", "1", "buy", confidence=0.4),
    ]
    weights = {"accurate": 0.8, "inaccurate": 0.3}

    result = aggregate_signals(
        signals, min_confidence=0.0, min_strategies=1, strategy_weights=weights
    )
    assert len(result) == 1
    # Weighted avg: (0.9*0.8 + 0.4*0.3) / (0.8+0.3) = (0.72 + 0.12) / 1.1 ≈ 0.7636
    assert result[0].confidence > 0.7  # closer to accurate strategy's value


def test_weighted_conflict_resolution_lets_stronger_side_through() -> None:
    """Weighted conflict resolution: higher-weight side wins, losing side suppressed."""
    signals = [
        _signal("accurate", "1", "buy", confidence=0.9),
        _signal("inaccurate", "1", "sell", confidence=0.7),
    ]
    weights = {"accurate": 0.8, "inaccurate": 0.3}

    result = aggregate_signals(
        signals, min_confidence=0.0, min_strategies=1, strategy_weights=weights
    )
    # Buy side wins because "accurate" has higher weight
    assert len(result) == 1
    assert result[0].side == "buy"


def test_weighted_conflict_resolution_suppresses_weaker_side() -> None:
    """Weaker side in a conflict is suppressed, not both sides."""
    signals = [
        _signal("weak", "1", "buy", confidence=0.9),
        _signal("strong", "1", "sell", confidence=0.7),
    ]
    weights = {"weak": 0.3, "strong": 0.9}

    result = aggregate_signals(
        signals, min_confidence=0.0, min_strategies=1, strategy_weights=weights
    )
    # Sell side wins because "strong" has higher weight
    assert len(result) == 1
    assert result[0].side == "sell"


def test_no_weights_backward_compatible() -> None:
    """When strategy_weights=None, behavior is unchanged."""
    signals = [
        _signal("A", "1", "buy", confidence=0.8),
        _signal("B", "1", "sell", confidence=0.7),
    ]
    # Without weights: conflict → suppress both
    result = aggregate_signals(
        signals, min_confidence=0.0, min_strategies=1, strategy_weights=None
    )
    assert len(result) == 0


def test_weighted_blending_unknown_strategy_defaults_to_1() -> None:
    """Strategies not in the weights dict get weight 1.0."""
    signals = [
        _signal("known", "1", "buy", confidence=0.6),
        _signal("unknown", "1", "buy", confidence=0.9),
    ]
    weights = {"known": 0.5}  # "unknown" defaults to 1.0

    result = aggregate_signals(
        signals, min_confidence=0.0, min_strategies=1, strategy_weights=weights
    )
    assert len(result) == 1
    # Weighted avg: (0.6*0.5 + 0.9*1.0) / (0.5 + 1.0) = 1.2/1.5 = 0.8
    assert abs(result[0].confidence - 0.8) < 0.01
