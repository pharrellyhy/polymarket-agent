"""Tests for volatility anomaly detection."""

from polymarket_agent.data.models import PricePoint, VolatilityReport
from polymarket_agent.strategies.volatility import (
    compute_bb_width_percentile,
    compute_price_acceleration,
    compute_rate_of_change,
    compute_volatility_report,
    compute_volume_spike_ratio,
    format_volatility_summary,
)


def _make_prices(n: int = 30, base: float = 0.5, step: float = 0.005) -> list[float]:
    """Generate a simple ascending price series."""
    return [base + i * step for i in range(n)]


def _make_price_points(prices: list[float]) -> list[PricePoint]:
    """Convert a price list into PricePoint objects."""
    return [PricePoint(timestamp=f"2026-02-{i:02d}T00:00:00Z", price=p) for i, p in enumerate(prices)]


def test_rate_of_change_ascending() -> None:
    """Rate of change should be positive for ascending prices."""
    prices = _make_prices(30)
    roc = compute_rate_of_change(prices, period=10)
    assert roc > 0
    assert roc <= 1.0


def test_rate_of_change_flat() -> None:
    """Rate of change should be ~0 for flat prices."""
    prices = [0.5] * 30
    roc = compute_rate_of_change(prices, period=10)
    assert roc == 0.0


def test_rate_of_change_insufficient_data() -> None:
    """Rate of change should be 0 with insufficient data."""
    assert compute_rate_of_change([0.5], period=10) == 0.0


def test_price_acceleration_stable() -> None:
    """Acceleration should be low for steady trends."""
    prices = _make_prices(30, step=0.001)
    accel = compute_price_acceleration(prices)
    assert accel < 0.3


def test_price_acceleration_insufficient_data() -> None:
    """Acceleration should be 0 with insufficient data."""
    assert compute_price_acceleration([0.5] * 5) == 0.0


def test_volume_spike_ratio_normal() -> None:
    """Volume spike ratio should be low for uniform volumes."""
    volumes = [100.0] * 20
    ratio = compute_volume_spike_ratio(volumes)
    assert ratio == 0.0


def test_volume_spike_ratio_spike() -> None:
    """Volume spike ratio should be high when last value is 3x average."""
    volumes = [100.0] * 19 + [300.0]
    ratio = compute_volume_spike_ratio(volumes)
    assert ratio > 0.5


def test_volume_spike_ratio_empty() -> None:
    """Volume spike ratio should be 0 for empty list."""
    assert compute_volume_spike_ratio([]) == 0.0


def test_bb_width_percentile_range() -> None:
    """BB width percentile should be between 0 and 1."""
    prices = _make_prices(30)
    pct = compute_bb_width_percentile(prices)
    assert 0.0 <= pct <= 1.0


def test_bb_width_percentile_insufficient_data() -> None:
    """BB width percentile should be 0.5 with insufficient data."""
    assert compute_bb_width_percentile([0.5] * 5) == 0.5


def test_compute_volatility_report_normal() -> None:
    """Full report should return valid values for normal price series."""
    prices = _make_prices(30)
    points = _make_price_points(prices)
    report = compute_volatility_report(points, "tok1")
    assert report is not None
    assert isinstance(report, VolatilityReport)
    assert report.token_id == "tok1"
    assert 0.0 <= report.anomaly_score <= 1.0


def test_compute_volatility_report_insufficient_data() -> None:
    """Report should be None with fewer than 21 data points."""
    points = _make_price_points([0.5] * 10)
    assert compute_volatility_report(points, "tok1") is None


def test_compute_volatility_report_anomalous() -> None:
    """Report should flag anomalous conditions with volatile prices."""
    # Create a price series with a dramatic spike
    prices = [0.5] * 20 + [0.5 + i * 0.05 for i in range(10)]
    points = _make_price_points(prices)
    report = compute_volatility_report(points, "tok1", threshold=0.1)
    assert report is not None
    assert report.anomaly_score > 0.0


def test_format_volatility_summary() -> None:
    """Formatted summary should contain key metrics."""
    report = VolatilityReport(
        token_id="tok1",
        rate_of_change=0.15,
        price_acceleration=0.1,
        volume_spike_ratio=0.3,
        bb_width_percentile=0.85,
        spread_widening=0.2,
        anomaly_score=0.65,
        is_anomalous=True,
    )
    summary = format_volatility_summary(report)
    assert "ANOMALOUS" in summary
    assert "0.65" in summary
    assert "Rate of change" in summary


def test_format_volatility_summary_normal() -> None:
    """Formatted summary should show Normal for non-anomalous."""
    report = VolatilityReport(token_id="tok1", anomaly_score=0.3, is_anomalous=False)
    summary = format_volatility_summary(report)
    assert "Normal" in summary
