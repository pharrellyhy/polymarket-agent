"""Tests for technical indicator computations."""

from polymarket_agent.data.models import PricePoint
from polymarket_agent.strategies.indicators import (
    TechnicalContext,
    analyze_market_technicals,
    compute_ema,
    compute_rsi,
    compute_squeeze,
)


def _make_prices(values: list[float]) -> list[PricePoint]:
    """Build PricePoint list from raw floats."""
    return [PricePoint(timestamp=f"2026-02-{i:02d}T00:00:00Z", price=v) for i, v in enumerate(values)]


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------


def test_ema_basic() -> None:
    prices = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = compute_ema(prices, period=3)
    assert result.period == 3
    # SMA of first 3 = 2.0, then EMA smooths toward later values
    assert result.value > 2.0


def test_ema_single_value() -> None:
    result = compute_ema([5.0], period=3)
    assert result.value == 5.0


def test_ema_exact_period() -> None:
    prices = [1.0, 2.0, 3.0]
    result = compute_ema(prices, period=3)
    # Exactly period values → SMA only (no EMA smoothing steps)
    assert abs(result.value - 2.0) < 1e-9


def test_ema_shorter_than_period() -> None:
    prices = [1.0, 3.0]
    result = compute_ema(prices, period=5)
    assert abs(result.value - 2.0) < 1e-9


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


def test_rsi_uptrend() -> None:
    # Steady uptrend — RSI should be high
    prices = [float(i) for i in range(30)]
    result = compute_rsi(prices, period=14)
    assert result.rsi > 70
    assert result.is_overbought


def test_rsi_downtrend() -> None:
    # Steady downtrend — RSI should be low
    prices = [float(30 - i) for i in range(30)]
    result = compute_rsi(prices, period=14)
    assert result.rsi < 30
    assert result.is_oversold


def test_rsi_flat() -> None:
    prices = [0.5] * 30
    result = compute_rsi(prices, period=14)
    # All deltas are zero — RSI is 50 by convention
    assert 45 <= result.rsi <= 55


def test_rsi_insufficient_data() -> None:
    result = compute_rsi([1.0, 2.0, 3.0], period=14)
    assert result.rsi == 50.0
    assert not result.is_overbought
    assert not result.is_oversold


def test_stoch_rsi_range() -> None:
    # Mix of up and down — stoch_rsi should be in [0, 1]
    prices = [0.5 + 0.01 * (i % 7 - 3) for i in range(40)]
    result = compute_rsi(prices, period=14)
    assert 0.0 <= result.stoch_rsi <= 1.0


# ---------------------------------------------------------------------------
# Squeeze
# ---------------------------------------------------------------------------


def test_squeeze_flat_prices() -> None:
    prices = [0.5] * 30
    result = compute_squeeze(prices)
    # All prices identical → zero width → squeezing
    assert result.bb_width == 0.0


def test_squeeze_insufficient_data() -> None:
    result = compute_squeeze([1.0, 2.0])
    assert not result.is_squeezing
    assert not result.squeeze_releasing
    assert result.momentum == 0.0


def test_squeeze_momentum_direction() -> None:
    # Rising prices → positive momentum
    prices = [0.4 + 0.01 * i for i in range(30)]
    result = compute_squeeze(prices)
    assert result.momentum > 0

    # Falling prices → negative momentum
    prices_down = [0.7 - 0.01 * i for i in range(30)]
    result_down = compute_squeeze(prices_down)
    assert result_down.momentum < 0


# ---------------------------------------------------------------------------
# Combined analysis
# ---------------------------------------------------------------------------


def test_analyze_returns_none_for_insufficient_data() -> None:
    points = _make_prices([0.5] * 10)
    result = analyze_market_technicals(points, "tok123")
    assert result is None


def test_analyze_returns_context_with_enough_data() -> None:
    # 30 data points with an uptrend
    values = [0.4 + 0.005 * i for i in range(30)]
    points = _make_prices(values)
    result = analyze_market_technicals(points, "tok456")
    assert isinstance(result, TechnicalContext)
    assert result.token_id == "tok456"
    assert result.trend_direction == "up"
    assert result.price_change_pct > 0


def test_analyze_bullish_crossover() -> None:
    # Start low, ramp up — fast EMA should lead slow EMA
    values = [0.3] * 15 + [0.3 + 0.02 * i for i in range(15)]
    points = _make_prices(values)
    result = analyze_market_technicals(points, "tok_bull")
    assert result is not None
    assert result.ema_crossover == "bullish"


def test_analyze_bearish_crossover() -> None:
    # Start high, ramp down — fast EMA should lag below slow EMA
    values = [0.7] * 15 + [0.7 - 0.02 * i for i in range(15)]
    points = _make_prices(values)
    result = analyze_market_technicals(points, "tok_bear")
    assert result is not None
    assert result.ema_crossover == "bearish"


def test_analyze_neutral_trend() -> None:
    # Flat prices — trend should be neutral
    values = [0.5] * 30
    points = _make_prices(values)
    result = analyze_market_technicals(points, "tok_flat")
    assert result is not None
    assert result.trend_direction == "neutral"
    assert result.ema_crossover == "none"


def test_analyze_custom_periods() -> None:
    values = [0.4 + 0.005 * i for i in range(40)]
    points = _make_prices(values)
    result = analyze_market_technicals(points, "tok_custom", ema_fast_period=5, ema_slow_period=15, rsi_period=10)
    assert result is not None
    assert result.ema_fast.period == 5
    assert result.ema_slow.period == 15
