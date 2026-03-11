"""Tests for the execution Kelly position sizing method."""

import pytest

from polymarket_agent.position_sizing import PositionSizer
from polymarket_agent.strategies.base import Signal


def _make_signal(
    confidence: float = 0.7,
    price: float = 0.5,
    size: float = 25.0,
    execution_probability: float | None = None,
) -> Signal:
    return Signal(
        strategy="test",
        market_id="m1",
        token_id="tok1",
        side="buy",
        confidence=confidence,
        target_price=price,
        size=size,
        reason="test",
        execution_probability=execution_probability,
    )


class TestExecutionKellySize:
    def test_basic_computation(self) -> None:
        """execution_kelly_size should return positive value for favorable odds."""
        result = PositionSizer.execution_kelly_size(0.7, 0.5, 1.0)
        assert result > 0

    def test_lower_exec_prob_reduces_size(self) -> None:
        """Lower execution probability should give smaller sizing."""
        full = PositionSizer.execution_kelly_size(0.7, 0.5, 1.0)
        partial = PositionSizer.execution_kelly_size(0.7, 0.5, 0.5)
        assert partial < full

    def test_zero_exec_prob(self) -> None:
        """Zero execution probability should give zero size."""
        result = PositionSizer.execution_kelly_size(0.7, 0.5, 0.0)
        assert result == 0.0

    def test_invalid_price_zero(self) -> None:
        result = PositionSizer.execution_kelly_size(0.7, 0.0, 1.0)
        assert result == 0.0

    def test_invalid_price_one(self) -> None:
        result = PositionSizer.execution_kelly_size(0.7, 1.0, 1.0)
        assert result == 0.0

    def test_low_confidence(self) -> None:
        """Very low confidence should give zero or near-zero sizing."""
        result = PositionSizer.execution_kelly_size(0.1, 0.5, 1.0)
        # b=1, p=0.1, q=0.9 -> (1*0.1 - 0.9)/1 = -0.8 -> clamped to 0
        assert result == 0.0

    def test_compute_size_execution_kelly(self) -> None:
        """PositionSizer.compute_size should work with execution_kelly method."""
        from unittest.mock import MagicMock

        sizer = PositionSizer(method="execution_kelly", max_bet_pct=0.10)
        signal = _make_signal(confidence=0.7, price=0.5, execution_probability=0.9)

        portfolio = MagicMock()
        portfolio.total_value = 1000.0

        result = sizer.compute_size(signal, portfolio)
        assert result > 0
        assert result <= 100.0  # 10% of 1000

    def test_compute_size_fallback_without_exec_prob(self) -> None:
        """Should default to execution_probability=1.0 when not set."""
        from unittest.mock import MagicMock

        sizer = PositionSizer(method="execution_kelly", max_bet_pct=0.10)
        signal = _make_signal(confidence=0.7, price=0.5, execution_probability=None)

        portfolio = MagicMock()
        portfolio.total_value = 1000.0

        result = sizer.compute_size(signal, portfolio)
        assert result > 0
