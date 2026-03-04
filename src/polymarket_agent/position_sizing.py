"""Position sizing strategies: fixed, Kelly criterion, fractional Kelly.

Includes a CalibrationTable that maps (strategy, confidence_bin) to
historical win rates for more accurate Kelly sizing.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_agent.db import Database
    from polymarket_agent.execution.base import Portfolio
    from polymarket_agent.strategies.base import Signal


class CalibrationTable:
    """Maps (strategy, confidence_bin) to historical win rates.

    Bins confidence into 0.1-width buckets (0.0-0.1, 0.1-0.2, ..., 0.9-1.0).
    Falls back to raw confidence when fewer than ``min_samples`` exist per bin.
    """

    def __init__(self, *, min_samples: int = 20) -> None:
        self._min_samples = min_samples
        # (strategy, bin_index) -> (wins, total)
        self._table: dict[tuple[str, int], tuple[int, int]] = {}

    def refresh(self, db: "Database") -> None:
        """Rebuild the calibration table from resolved signal outcomes."""
        self._table.clear()
        raw_rows = db.get_resolved_outcomes()
        if not raw_rows:
            return

        for row in raw_rows:
            strategy = str(row["strategy"])
            confidence = float(row["confidence"])  # type: ignore[arg-type]
            outcome = row["outcome"]
            bin_idx = self._confidence_to_bin(confidence)
            key = (strategy, bin_idx)
            wins, total = self._table.get(key, (0, 0))
            if outcome == "win":
                wins += 1
            self._table[key] = (wins, total + 1)

    def calibrated_confidence(self, strategy: str, raw_confidence: float) -> float:
        """Return calibrated win probability for a strategy/confidence pair.

        Falls back to raw confidence if insufficient historical data.
        """
        bin_idx = self._confidence_to_bin(raw_confidence)
        key = (strategy, bin_idx)
        if key not in self._table:
            return raw_confidence
        wins, total = self._table[key]
        if total < self._min_samples:
            return raw_confidence
        return wins / total

    @staticmethod
    def _confidence_to_bin(confidence: float) -> int:
        """Map confidence [0, 1] to bin index [0, 9]."""
        return min(max(int(confidence * 10), 0), 9)

    @property
    def has_data(self) -> bool:
        """Return True if any calibration data has been loaded."""
        return bool(self._table)


class PositionSizer:
    """Compute trade sizes using configurable sizing methods."""

    def __init__(
        self,
        method: str = "fixed",
        kelly_fraction: float = 0.25,
        max_bet_pct: float = 0.10,
        calibration: CalibrationTable | None = None,
    ) -> None:
        self._method = method
        self._kelly_fraction = kelly_fraction
        self._max_bet_pct = max_bet_pct
        self._calibration = calibration

    def compute_size(self, signal: "Signal", portfolio: "Portfolio") -> float:
        """Return a clamped USDC size based on the chosen sizing method."""
        if self._method == "kelly":
            confidence = self._get_calibrated_confidence(signal)
            raw = self.kelly_size(confidence, signal.target_price)
        elif self._method == "fractional_kelly":
            confidence = self._get_calibrated_confidence(signal)
            raw = self.fractional_kelly_size(confidence, signal.target_price)
        else:
            return signal.size

        max_bet = portfolio.total_value * self._max_bet_pct
        sized = raw * portfolio.total_value
        # When Kelly returns ~0 (e.g. no calibration data yet), fall back to
        # the strategy's original order_size rather than placing a zero trade.
        if sized < 1.0:
            sized = signal.size
        return max(0.0, min(sized, max_bet, signal.size))

    def _get_calibrated_confidence(self, signal: "Signal") -> float:
        """Get calibrated confidence if a calibration table is available."""
        if self._calibration is not None and self._calibration.has_data:
            return self._calibration.calibrated_confidence(signal.strategy, signal.confidence)
        return signal.confidence

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
