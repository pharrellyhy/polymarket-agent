"""ExitManager -- generates sell signals for held positions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from polymarket_agent.config import ExitManagerConfig
from polymarket_agent.strategies.base import Signal

logger = logging.getLogger(__name__)

_MIDPOINT = 0.5


class ExitManager:
    """Evaluate held positions and generate sell signals when exit conditions are met.

    Exit rules are evaluated in priority order; the first matching rule wins:
    1. Profit target -- current price >= entry * (1 + profit_target_pct)
    2. Stop loss -- current price <= entry * (1 - stop_loss_pct)
    3. Signal reversal -- entry condition no longer holds
    4. Stale position -- held longer than max_hold_hours
    """

    def __init__(self, config: ExitManagerConfig) -> None:
        self._config = config

    def evaluate(
        self,
        positions: dict[str, dict[str, Any]],
        current_prices: dict[str, float],
    ) -> list[Signal]:
        """Return sell signals for positions that should be closed."""
        if not self._config.enabled:
            return []

        signals: list[Signal] = []
        for token_id, pos in positions.items():
            current_price = current_prices.get(token_id)
            if current_price is None:
                continue

            reason = self._check_exit(pos, current_price)
            if reason is None:
                continue

            shares = float(pos.get("shares", 0))
            if shares <= 0:
                continue

            size = shares * current_price
            signals.append(
                Signal(
                    strategy="exit_manager",
                    market_id=str(pos.get("market_id", "")),
                    token_id=token_id,
                    side="sell",
                    confidence=1.0,
                    target_price=current_price,
                    size=size,
                    reason=reason,
                )
            )
        return signals

    def _check_exit(self, pos: dict[str, Any], current_price: float) -> str | None:
        """Check exit rules in priority order. Return reason string or None."""
        avg_price = float(pos.get("avg_price", 0))
        if avg_price <= 0:
            return None

        # Rule 1: Profit target
        if current_price >= avg_price * (1.0 + self._config.profit_target_pct):
            pct = (current_price - avg_price) / avg_price * 100
            return f"profit_target: +{pct:.1f}% (entry={avg_price:.4f}, current={current_price:.4f})"

        # Rule 2: Stop loss
        if current_price <= avg_price * (1.0 - self._config.stop_loss_pct):
            pct = (avg_price - current_price) / avg_price * 100
            return f"stop_loss: -{pct:.1f}% (entry={avg_price:.4f}, current={current_price:.4f})"

        # Rule 3: Signal reversal
        if self._config.signal_reversal:
            reversal = self._check_signal_reversal(pos, current_price)
            if reversal is not None:
                return reversal

        # Rule 4: Stale position
        opened_at_str = pos.get("opened_at")
        if opened_at_str:
            try:
                opened_at = datetime.fromisoformat(opened_at_str)
                age = datetime.now(timezone.utc) - opened_at
                if age > timedelta(hours=self._config.max_hold_hours):
                    hours = age.total_seconds() / 3600
                    return f"stale: held {hours:.1f}h > max {self._config.max_hold_hours}h"
            except (ValueError, TypeError):
                pass

        return None

    @staticmethod
    def _check_signal_reversal(pos: dict[str, Any], current_price: float) -> str | None:
        """Check if the original entry condition has reversed."""
        entry_strategy = pos.get("entry_strategy", "unknown")

        if entry_strategy == "signal_trader":
            # Signal trader buys when yes_price < midpoint. Reversed when price >= midpoint.
            if current_price >= _MIDPOINT:
                return f"signal_reversal: price {current_price:.4f} crossed above midpoint ({_MIDPOINT})"

        if entry_strategy == "arbitrageur":
            # Arbitrageur buys underpriced side. If price has normalized, exit.
            # We approximate: if current price is within 2% of avg_price, the arb closed.
            avg_price = float(pos.get("avg_price", 0))
            if avg_price > 0 and abs(current_price - avg_price) / avg_price < 0.02:
                return f"signal_reversal: arb deviation closed (entry={avg_price:.4f}, current={current_price:.4f})"

        return None
