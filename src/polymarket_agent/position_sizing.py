"""Position sizing strategies: fixed, Kelly criterion, fractional Kelly."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_agent.execution.base import Portfolio
    from polymarket_agent.strategies.base import Signal


class PositionSizer:
    """Compute trade sizes using configurable sizing methods."""

    def __init__(
        self,
        method: str = "fixed",
        kelly_fraction: float = 0.25,
        max_bet_pct: float = 0.10,
    ) -> None:
        self._method = method
        self._kelly_fraction = kelly_fraction
        self._max_bet_pct = max_bet_pct

    def compute_size(self, signal: Signal, portfolio: Portfolio) -> float:
        """Return a clamped USDC size based on the chosen sizing method."""
        if self._method == "kelly":
            raw = self.kelly_size(signal.confidence, signal.target_price)
        elif self._method == "fractional_kelly":
            raw = self.fractional_kelly_size(signal.confidence, signal.target_price)
        else:
            return signal.size

        max_bet = portfolio.total_value * self._max_bet_pct
        return max(0.0, min(raw * portfolio.total_value, max_bet, signal.size))

    @staticmethod
    def kelly_size(confidence: float, price: float) -> float:
        """Full Kelly criterion: f* = (bp - q) / b.

        b = decimal odds = (1 / price) - 1
        p = estimated probability (confidence)
        q = 1 - p
        """
        if price <= 0 or price >= 1:
            return 0.0
        b = (1.0 / price) - 1.0
        if b <= 0:
            return 0.0
        q = 1.0 - confidence
        f = (b * confidence - q) / b
        return max(f, 0.0)

    def fractional_kelly_size(self, confidence: float, price: float) -> float:
        """Fractional Kelly: kelly_fraction * full Kelly."""
        return self._kelly_fraction * self.kelly_size(confidence, price)

    @staticmethod
    def fixed_size(size: float) -> float:
        """Pass through the signal's original size."""
        return size
