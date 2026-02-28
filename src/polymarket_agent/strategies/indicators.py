"""Technical indicator computations for price history data.

Pure-computation module with no external dependencies beyond PricePoint.
All functions accept a list of float prices (newest-last) and return
structured Pydantic models.
"""

import math
from typing import Literal

from pydantic import BaseModel

from polymarket_agent.data.models import PricePoint


class EMAResult(BaseModel):
    """Result of an exponential moving average computation."""

    period: int
    value: float


class RSIResult(BaseModel):
    """Relative Strength Index + Stochastic RSI."""

    rsi: float  # 0-100
    stoch_rsi: float  # 0-1
    is_overbought: bool
    is_oversold: bool


class SqueezeResult(BaseModel):
    """Bollinger Band squeeze detection."""

    is_squeezing: bool
    squeeze_releasing: bool
    momentum: float
    bb_width: float


class TechnicalContext(BaseModel):
    """Aggregated technical analysis for a single token."""

    token_id: str
    ema_fast: EMAResult
    ema_slow: EMAResult
    rsi: RSIResult
    squeeze: SqueezeResult
    trend_direction: Literal["up", "down", "neutral"]
    ema_crossover: Literal["bullish", "bearish", "none"]
    price_change_pct: float
    current_price: float
    price_start: float


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------


def compute_ema(prices: list[float], period: int) -> EMAResult:
    """Compute the exponential moving average for the given period.

    ``prices`` should be ordered oldest-first.  Returns the most recent
    EMA value.
    """
    if len(prices) < period:
        # Not enough data — return simple average of available points
        return EMAResult(period=period, value=sum(prices) / len(prices))

    multiplier = 2.0 / (period + 1)
    # Seed with SMA of the first `period` values
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return EMAResult(period=period, value=ema)


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


def compute_rsi(prices: list[float], period: int = 14) -> RSIResult:
    """Compute RSI and Stochastic RSI.

    Uses the standard Wilder smoothing (exponential moving average of
    gains/losses).
    """
    if len(prices) < period + 1:
        return RSIResult(rsi=50.0, stoch_rsi=0.5, is_overbought=False, is_oversold=False)

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    # Seed with simple average of first `period` changes
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing for the remaining values
    rsi_series: list[float] = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0 and avg_gain == 0:
            rsi_val = 50.0
        elif avg_loss == 0:
            rsi_val = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100.0 - 100.0 / (1.0 + rs)
        rsi_series.append(rsi_val)

    current_rsi = rsi_series[-1] if rsi_series else 50.0

    # Stochastic RSI over the last `period` RSI values (or all available)
    window = rsi_series[-period:] if len(rsi_series) >= period else rsi_series
    if window:
        rsi_min = min(window)
        rsi_max = max(window)
        if rsi_max - rsi_min > 0:
            stoch_rsi = (current_rsi - rsi_min) / (rsi_max - rsi_min)
        else:
            stoch_rsi = 0.5
    else:
        stoch_rsi = 0.5

    return RSIResult(
        rsi=round(current_rsi, 2),
        stoch_rsi=round(max(0.0, min(1.0, stoch_rsi)), 4),
        is_overbought=current_rsi > 70,
        is_oversold=current_rsi < 30,
    )


# ---------------------------------------------------------------------------
# Bollinger Band Squeeze
# ---------------------------------------------------------------------------


def compute_squeeze(
    prices: list[float],
    bb_period: int = 20,
    bb_mult: float = 2.0,
) -> SqueezeResult:
    """Detect Bollinger Band squeeze (low volatility compression).

    Uses ATR approximated from absolute price changes since PricePoint
    lacks OHLC data.  A squeeze is detected when Bollinger Band width
    is below the recent median width.
    """
    if len(prices) < bb_period:
        return SqueezeResult(is_squeezing=False, squeeze_releasing=False, momentum=0.0, bb_width=0.0)

    # Bollinger Bands on the last bb_period prices
    window = prices[-bb_period:]
    sma = sum(window) / bb_period
    variance = sum((p - sma) ** 2 for p in window) / bb_period
    std_dev = math.sqrt(variance)
    bb_width = (2 * bb_mult * std_dev) / sma if sma > 0 else 0.0

    # Compute historical BB widths for comparison
    widths: list[float] = []
    for i in range(bb_period, len(prices) + 1):
        w = prices[i - bb_period : i]
        w_sma = sum(w) / bb_period
        w_var = sum((p - w_sma) ** 2 for p in w) / bb_period
        w_std = math.sqrt(w_var)
        w_width = (2 * bb_mult * w_std) / w_sma if w_sma > 0 else 0.0
        widths.append(w_width)

    # Squeeze: current width below median of historical widths
    sorted_widths = sorted(widths)
    median_width = sorted_widths[len(sorted_widths) // 2]
    is_squeezing = bb_width < median_width

    # Squeeze releasing: was squeezing last period, no longer squeezing now
    squeeze_releasing = False
    if len(widths) >= 2:
        prev_width = widths[-2]
        was_squeezing = prev_width < median_width
        squeeze_releasing = was_squeezing and not is_squeezing

    # Momentum: rate of price change (last 5 bars or available)
    momentum_window = min(5, len(prices) - 1)
    if momentum_window > 0:
        momentum = (prices[-1] - prices[-1 - momentum_window]) / momentum_window
    else:
        momentum = 0.0

    return SqueezeResult(
        is_squeezing=is_squeezing,
        squeeze_releasing=squeeze_releasing,
        momentum=round(momentum, 6),
        bb_width=round(bb_width, 6),
    )


# ---------------------------------------------------------------------------
# Combined analysis
# ---------------------------------------------------------------------------

_MIN_DATA_POINTS = 21


def analyze_market_technicals(
    price_points: list[PricePoint],
    token_id: str,
    *,
    ema_fast_period: int = 8,
    ema_slow_period: int = 21,
    rsi_period: int = 14,
) -> TechnicalContext | None:
    """Run all technical indicators on a price history series.

    Returns ``None`` if fewer than ``_MIN_DATA_POINTS`` data points are
    available (not enough data for meaningful analysis).
    """
    if len(price_points) < _MIN_DATA_POINTS:
        return None

    prices = [pp.price for pp in price_points]

    ema_fast = compute_ema(prices, ema_fast_period)
    ema_slow = compute_ema(prices, ema_slow_period)
    rsi = compute_rsi(prices, rsi_period)
    squeeze = compute_squeeze(prices)

    # Trend direction from price change
    price_start = prices[0]
    current_price = prices[-1]
    if price_start > 0:
        change_pct = (current_price - price_start) / price_start
    else:
        change_pct = 0.0

    trend: Literal["up", "down", "neutral"]
    if change_pct > 0.02:
        trend = "up"
    elif change_pct < -0.02:
        trend = "down"
    else:
        trend = "neutral"

    # EMA crossover
    crossover: Literal["bullish", "bearish", "none"]
    if ema_fast.value > ema_slow.value * 1.005:
        crossover = "bullish"
    elif ema_fast.value < ema_slow.value * 0.995:
        crossover = "bearish"
    else:
        crossover = "none"

    return TechnicalContext(
        token_id=token_id,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        rsi=rsi,
        squeeze=squeeze,
        trend_direction=trend,
        ema_crossover=crossover,
        price_change_pct=round(change_pct, 4),
        current_price=current_price,
        price_start=price_start,
    )
