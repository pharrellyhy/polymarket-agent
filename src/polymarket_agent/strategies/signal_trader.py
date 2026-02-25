"""SignalTrader strategy — volume-filtered directional signals."""

from __future__ import annotations

from typing import Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy

_DEFAULT_VOLUME_THRESHOLD: float = 5000.0
_DEFAULT_PRICE_MOVE_THRESHOLD: float = 0.05
_MIDPOINT: float = 0.5


class SignalTrader(Strategy):
    """Generate buy/sell signals for active, high-volume markets.

    Markets are filtered by 24-hour volume.  A *buy* signal is emitted when
    the Yes price is below the midpoint (0.5) by more than
    ``price_move_threshold``, and a *sell* signal when it is above.
    """

    name: str = "signal_trader"

    def __init__(self) -> None:
        self._volume_threshold: float = _DEFAULT_VOLUME_THRESHOLD
        self._price_move_threshold: float = _DEFAULT_PRICE_MOVE_THRESHOLD

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def configure(self, config: dict[str, Any]) -> None:
        """Load strategy-specific configuration."""
        self._volume_threshold = float(config.get("volume_threshold", _DEFAULT_VOLUME_THRESHOLD))
        self._price_move_threshold = float(config.get("price_move_threshold", _DEFAULT_PRICE_MOVE_THRESHOLD))

    def analyze(self, markets: list[Market], data: Any) -> list[Signal]:
        """Return directional signals for qualifying markets."""
        return [s for market in markets if (s := self._evaluate(market)) is not None]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evaluate(self, market: Market) -> Signal | None:
        """Evaluate a single market and return a Signal if it qualifies."""
        if not market.active or market.closed:
            return None

        if market.volume_24h < self._volume_threshold:
            return None

        yes_price = market.outcome_prices[0] if market.outcome_prices else _MIDPOINT
        distance = abs(yes_price - _MIDPOINT)

        if distance <= self._price_move_threshold:
            return None

        if yes_price < _MIDPOINT:
            side: Literal["buy", "sell"] = "buy"
            token_id = market.clob_token_ids[0] if market.clob_token_ids else ""
        else:
            side = "sell"
            token_id = market.clob_token_ids[1] if len(market.clob_token_ids) > 1 else ""

        confidence = min(distance / _MIDPOINT, 1.0)

        return Signal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            side=side,
            confidence=round(confidence, 4),
            target_price=yes_price,
            size=round(market.volume_24h * 0.01, 2),
            reason=f"yes_price={yes_price:.4f}, 24h_vol={market.volume_24h:.0f}",
        )
