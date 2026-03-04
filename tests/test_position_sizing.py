"""Tests for position sizing: Kelly criterion, fractional Kelly, and fixed."""

import tempfile
from pathlib import Path

from polymarket_agent.db import Database
from polymarket_agent.execution.base import Portfolio
from polymarket_agent.position_sizing import CalibrationTable, PositionSizer
from polymarket_agent.strategies.base import Signal


def _make_signal(*, confidence: float = 0.7, price: float = 0.50, size: float = 50.0) -> Signal:
    return Signal(
        strategy="test",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=confidence,
        target_price=price,
        size=size,
        reason="test signal",
    )


def _make_portfolio(balance: float = 1000.0) -> Portfolio:
    return Portfolio(balance=balance)


# ------------------------------------------------------------------
# Kelly formula
# ------------------------------------------------------------------


class TestKellySize:
    def test_positive_edge(self) -> None:
        """With confidence > price, Kelly should return a positive fraction."""
        f = PositionSizer.kelly_size(confidence=0.7, price=0.50)
        # b = (1/0.5) - 1 = 1.0; f = (1*0.7 - 0.3) / 1 = 0.4
        assert abs(f - 0.4) < 1e-9

    def test_no_edge(self) -> None:
        """When confidence equals market price, Kelly is zero."""
        f = PositionSizer.kelly_size(confidence=0.5, price=0.50)
        assert f == 0.0

    def test_negative_edge(self) -> None:
        """When confidence < market price, Kelly returns 0 (clamped)."""
        f = PositionSizer.kelly_size(confidence=0.3, price=0.50)
        assert f == 0.0

    def test_extreme_price_zero(self) -> None:
        f = PositionSizer.kelly_size(confidence=0.8, price=0.0)
        assert f == 0.0

    def test_extreme_price_one(self) -> None:
        f = PositionSizer.kelly_size(confidence=0.8, price=1.0)
        assert f == 0.0

    def test_high_confidence_low_price(self) -> None:
        """High edge scenario: f should be large."""
        f = PositionSizer.kelly_size(confidence=0.9, price=0.20)
        # b = 4; f = (4*0.9 - 0.1) / 4 = 3.5/4 = 0.875
        assert abs(f - 0.875) < 1e-9


# ------------------------------------------------------------------
# Fractional Kelly
# ------------------------------------------------------------------


class TestFractionalKelly:
    def test_quarter_kelly(self) -> None:
        sizer = PositionSizer(method="fractional_kelly", kelly_fraction=0.25)
        f = sizer.fractional_kelly_size(confidence=0.7, price=0.50)
        assert abs(f - 0.1) < 1e-9  # 0.25 * 0.4 = 0.1


# ------------------------------------------------------------------
# Fixed size
# ------------------------------------------------------------------


class TestFixedSize:
    def test_passthrough(self) -> None:
        assert PositionSizer.fixed_size(25.0) == 25.0


# ------------------------------------------------------------------
# compute_size integration
# ------------------------------------------------------------------


class TestComputeSize:
    def test_fixed_method_returns_signal_size(self) -> None:
        sizer = PositionSizer(method="fixed")
        signal = _make_signal(size=50.0)
        portfolio = _make_portfolio(1000.0)
        assert sizer.compute_size(signal, portfolio) == 50.0

    def test_kelly_method_caps_at_max_bet(self) -> None:
        sizer = PositionSizer(method="kelly", max_bet_pct=0.05)
        signal = _make_signal(confidence=0.9, price=0.20, size=200.0)
        portfolio = _make_portfolio(1000.0)
        result = sizer.compute_size(signal, portfolio)
        # max_bet = 1000 * 0.05 = 50
        assert result <= 50.0

    def test_kelly_method_caps_at_signal_size(self) -> None:
        sizer = PositionSizer(method="kelly", max_bet_pct=1.0)
        signal = _make_signal(confidence=0.7, price=0.50, size=30.0)
        portfolio = _make_portfolio(1000.0)
        result = sizer.compute_size(signal, portfolio)
        assert result <= 30.0

    def test_fractional_kelly_method(self) -> None:
        sizer = PositionSizer(method="fractional_kelly", kelly_fraction=0.25, max_bet_pct=1.0)
        signal = _make_signal(confidence=0.7, price=0.50, size=200.0)
        portfolio = _make_portfolio(1000.0)
        result = sizer.compute_size(signal, portfolio)
        # fractional_kelly = 0.25 * 0.4 = 0.1; result = 0.1 * 1000 = 100
        assert abs(result - 100.0) < 1e-9

    def test_zero_edge_returns_zero(self) -> None:
        sizer = PositionSizer(method="kelly")
        signal = _make_signal(confidence=0.5, price=0.50, size=50.0)
        portfolio = _make_portfolio(1000.0)
        assert sizer.compute_size(signal, portfolio) == 0.0


# ------------------------------------------------------------------
# CalibrationTable
# ------------------------------------------------------------------


class TestCalibrationTable:
    def test_empty_table_returns_raw_confidence(self) -> None:
        """With no data, calibrated_confidence returns raw value."""
        ct = CalibrationTable()
        assert ct.calibrated_confidence("ai_analyst", 0.8) == 0.8
        assert not ct.has_data

    def test_below_min_samples_returns_raw(self) -> None:
        """With fewer samples than min_samples, returns raw confidence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            # Record 5 outcomes (below default min_samples=20)
            for _ in range(5):
                db.record_signal_outcome(
                    strategy="ai_analyst",
                    market_id="100",
                    token_id="tok",
                    side="buy",
                    confidence=0.85,
                    predicted_price=0.5,
                    entry_price=0.5,
                    size=10.0,
                )
            db.resolve_signal_outcomes("100", resolved_price=1.0)

            ct = CalibrationTable(min_samples=20)
            ct.refresh(db)
            assert ct.has_data
            # Still returns raw because total < 20
            assert ct.calibrated_confidence("ai_analyst", 0.85) == 0.85

    def test_calibrated_confidence_with_sufficient_data(self) -> None:
        """With enough samples, returns historical win rate for the bin."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            # Record 25 outcomes: 15 wins, 10 losses in confidence bin 0.8
            for i in range(25):
                market_id = f"m{i}"
                db.record_signal_outcome(
                    strategy="ai_analyst",
                    market_id=market_id,
                    token_id=f"tok{i}",
                    side="buy",
                    confidence=0.85,  # bin 8 (0.8-0.9)
                    predicted_price=0.5,
                    entry_price=0.5,
                    size=10.0,
                )
                # First 15 win, last 10 lose
                resolved_price = 1.0 if i < 15 else 0.0
                db.resolve_signal_outcomes(market_id, resolved_price)

            ct = CalibrationTable(min_samples=20)
            ct.refresh(db)
            # Historical win rate: 15/25 = 0.6 for bin 8
            calibrated = ct.calibrated_confidence("ai_analyst", 0.85)
            assert calibrated == 15 / 25  # 0.6

    def test_confidence_to_bin_boundaries(self) -> None:
        """Bin mapping handles edge cases."""
        assert CalibrationTable._confidence_to_bin(-0.25) == 0
        assert CalibrationTable._confidence_to_bin(0.0) == 0
        assert CalibrationTable._confidence_to_bin(0.09) == 0
        assert CalibrationTable._confidence_to_bin(0.1) == 1
        assert CalibrationTable._confidence_to_bin(0.95) == 9
        assert CalibrationTable._confidence_to_bin(1.0) == 9  # clamped

    def test_sizer_uses_calibration(self) -> None:
        """PositionSizer with calibration produces different sizes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            # Create enough data for bin 8 (0.8-0.9): win rate = 0.55
            for i in range(20):
                market_id = f"m{i}"
                db.record_signal_outcome(
                    strategy="test",
                    market_id=market_id,
                    token_id=f"tok{i}",
                    side="buy",
                    confidence=0.82,
                    predicted_price=0.5,
                    entry_price=0.5,
                    size=10.0,
                )
                resolved_price = 1.0 if i < 11 else 0.0  # 11/20 = 0.55
                db.resolve_signal_outcomes(market_id, resolved_price)

            ct = CalibrationTable(min_samples=20)
            ct.refresh(db)

            signal = _make_signal(confidence=0.8, price=0.50, size=200.0)
            portfolio = _make_portfolio(1000.0)

            sizer_raw = PositionSizer(method="kelly", max_bet_pct=1.0)
            sizer_cal = PositionSizer(method="kelly", max_bet_pct=1.0, calibration=ct)

            # Rename signal strategy to match calibration data
            signal_cal = Signal(
                strategy="test",
                market_id="100",
                token_id="0xtok1",
                side="buy",
                confidence=0.8,
                target_price=0.50,
                size=200.0,
                reason="test signal",
            )

            size_raw = sizer_raw.compute_size(signal, portfolio)
            size_cal = sizer_cal.compute_size(signal_cal, portfolio)

            # Calibrated should produce smaller size (0.55 vs 0.8 confidence)
            assert size_cal < size_raw
