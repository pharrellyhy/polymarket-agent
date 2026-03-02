"""Volatility anomaly detection for AIAnalyst enrichment.

Computes a composite anomaly score from multiple volatility metrics.
Designed to be injected into the AIAnalyst LLM prompt as contextual data.
"""

import math

from polymarket_agent.data.models import PricePoint, VolatilityReport
from polymarket_agent.strategies.indicators import compute_ema

# Anomaly score weights
_W_ROC = 0.25
_W_ACCELERATION = 0.20
_W_VOLUME_SPIKE = 0.20
_W_BB_WIDTH = 0.20
_W_SPREAD = 0.15

_DEFAULT_THRESHOLD = 0.6
_MIN_DATA_POINTS = 21


def compute_rate_of_change(prices: list[float], period: int = 10) -> float:
    """Compute price rate of change over the given period.

    Returns absolute percentage change, clamped to [0, 1].
    """
    if len(prices) < period + 1:
        return 0.0
    old = prices[-(period + 1)]
    current = prices[-1]
    if old == 0:
        return 0.0
    roc = abs((current - old) / old)
    return min(roc, 1.0)


def compute_price_acceleration(prices: list[float], period: int = 5) -> float:
    """Compute price acceleration (second derivative of price).

    Measures how rapidly the rate of change itself is changing.
    Returns a normalized 0-1 score.
    """
    if len(prices) < 2 * period + 1:
        return 0.0
    roc_recent = compute_rate_of_change(prices, period)
    roc_prior = compute_rate_of_change(prices[:-period], period)
    delta = abs(roc_recent - roc_prior)
    return min(delta * 5.0, 1.0)  # Scale up for sensitivity


def compute_volume_spike_ratio(volumes: list[float], lookback: int = 20) -> float:
    """Compute volume spike ratio: current volume vs rolling average.

    Returns a normalized 0-1 score. 1.0 = volume >= 3x average.
    """
    if not volumes or len(volumes) < 2:
        return 0.0
    window = volumes[-lookback:] if len(volumes) >= lookback else volumes
    avg = sum(window[:-1]) / max(len(window) - 1, 1)
    if avg == 0:
        return 0.0
    ratio = volumes[-1] / avg
    return min(max(ratio - 1.0, 0.0) / 2.0, 1.0)  # Normalize: 3x -> 1.0


def compute_bb_width_percentile(prices: list[float], bb_period: int = 20) -> float:
    """Compute where current BB width sits relative to historical widths.

    Returns a 0-1 percentile (0 = tightest, 1 = widest).
    Extreme values (< 0.1 or > 0.9) are anomalous.
    """
    if len(prices) < bb_period:
        return 0.5

    widths: list[float] = []
    for i in range(bb_period, len(prices) + 1):
        window = prices[i - bb_period : i]
        sma = sum(window) / bb_period
        if sma == 0:
            widths.append(0.0)
            continue
        variance = sum((p - sma) ** 2 for p in window) / bb_period
        std_dev = math.sqrt(variance)
        widths.append((2 * 2.0 * std_dev) / sma)

    if not widths:
        return 0.5

    current = widths[-1]
    rank = sum(1 for w in widths if w <= current)
    return rank / len(widths)


def _compute_spread_widening(prices: list[float], period: int = 10) -> float:
    """Estimate spread widening from price volatility increase.

    Uses short-term vs long-term volatility ratio as a proxy.
    """
    if len(prices) < period * 2:
        return 0.0

    recent = prices[-period:]
    prior = prices[-(period * 2) : -period]

    recent_vol = _std_dev(recent)
    prior_vol = _std_dev(prior)
    if prior_vol == 0:
        return 0.0

    ratio = recent_vol / prior_vol
    return min(max(ratio - 1.0, 0.0) / 2.0, 1.0)


def _std_dev(values: list[float]) -> float:
    """Compute population standard deviation."""
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def compute_volatility_report(
    price_points: list[PricePoint],
    token_id: str,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
) -> VolatilityReport | None:
    """Compute a full volatility report from price history.

    Returns None if insufficient data (< 21 points).
    """
    if len(price_points) < _MIN_DATA_POINTS:
        return None

    prices = [pp.price for pp in price_points]

    roc = compute_rate_of_change(prices)
    acceleration = compute_price_acceleration(prices)
    # Use EMA ratio as a volume proxy (no true volume in PricePoint)
    ema_fast = compute_ema(prices, 5)
    ema_slow = compute_ema(prices, 20)
    volume_proxy = abs(ema_fast.value - ema_slow.value) / max(ema_slow.value, 0.001)
    volume_spike = min(volume_proxy * 5.0, 1.0)

    bb_pct = compute_bb_width_percentile(prices)
    # Convert percentile to extremity score (distance from 0.5)
    bb_extremity = abs(bb_pct - 0.5) * 2.0

    spread_widen = _compute_spread_widening(prices)

    anomaly_score = (
        _W_ROC * roc
        + _W_ACCELERATION * acceleration
        + _W_VOLUME_SPIKE * volume_spike
        + _W_BB_WIDTH * bb_extremity
        + _W_SPREAD * spread_widen
    )
    anomaly_score = min(max(anomaly_score, 0.0), 1.0)

    return VolatilityReport(
        token_id=token_id,
        rate_of_change=round(roc, 4),
        price_acceleration=round(acceleration, 4),
        volume_spike_ratio=round(volume_spike, 4),
        bb_width_percentile=round(bb_pct, 4),
        spread_widening=round(spread_widen, 4),
        anomaly_score=round(anomaly_score, 4),
        is_anomalous=anomaly_score >= threshold,
    )


def format_volatility_summary(report: VolatilityReport) -> str:
    """Format a VolatilityReport into a human-readable prompt section."""
    status = "ANOMALOUS" if report.is_anomalous else "Normal"
    lines = [
        f"Volatility status: {status} (score: {report.anomaly_score:.2f})",
        f"Rate of change: {report.rate_of_change:.4f}",
        f"Price acceleration: {report.price_acceleration:.4f}",
        f"Volume spike ratio: {report.volume_spike_ratio:.4f}",
        f"BB width percentile: {report.bb_width_percentile:.2%}",
        f"Spread widening: {report.spread_widening:.4f}",
    ]
    return "\n".join(lines)
