"""Arbitrageur strategy — exploits pricing inconsistencies within markets."""

from __future__ import annotations

import logging
from typing import Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_PRICE_SUM_TOLERANCE: float = 0.02
_DEFAULT_MIN_DEVIATION: float = 0.03
_DEFAULT_ORDER_SIZE: float = 25.0


class Arbitrageur(Strategy):
    """Detect and trade pricing inconsistencies.

    Currently checks: complementary outcome prices should sum to ~1.0.
    If the sum deviates beyond tolerance, the underpriced side is bought.
    """

    name: str = "arbitrageur"

    def __init__(self) -> None:
        self._price_sum_tolerance: float = _DEFAULT_PRICE_SUM_TOLERANCE
        self._min_deviation: float = _DEFAULT_MIN_DEVIATION
        self._order_size: float = _DEFAULT_ORDER_SIZE

    def configure(self, config: dict[str, Any]) -> None:
        self._price_sum_tolerance = float(config.get("price_sum_tolerance", _DEFAULT_PRICE_SUM_TOLERANCE))
        self._min_deviation = float(config.get("min_deviation", _DEFAULT_MIN_DEVIATION))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))

    def analyze(self, markets: list[Market], data: Any) -> list[Signal]:
        signals: list[Signal] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            signal = self._check_price_sum(market)
            if signal is not None:
                signals.append(signal)
        return signals

    def _check_price_sum(self, market: Market) -> Signal | None:
        """Check if outcome prices sum to approximately 1.0."""
        if len(market.outcome_prices) < 2:
            return None

        price_sum = sum(market.outcome_prices)
        deviation = abs(price_sum - 1.0)

        if deviation <= self._price_sum_tolerance:
            return None

        # Buy the underpriced side
        if price_sum < 1.0:
            # Outcomes are collectively underpriced — buy the cheaper one
            min_idx = market.outcome_prices.index(min(market.outcome_prices))
            side: Literal["buy", "sell"] = "buy"
            target_price = market.outcome_prices[min_idx]
            token_id = market.clob_token_ids[min_idx] if min_idx < len(market.clob_token_ids) else ""
        else:
            # Outcomes are collectively overpriced — sell the more expensive one
            max_idx = market.outcome_prices.index(max(market.outcome_prices))
            side = "sell"
            target_price = market.outcome_prices[max_idx]
            token_id = market.clob_token_ids[max_idx] if max_idx < len(market.clob_token_ids) else ""

        confidence = min(deviation / 0.1, 1.0)

        return Signal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            side=side,
            confidence=round(confidence, 4),
            target_price=target_price,
            size=self._order_size,
            reason=f"price_sum={price_sum:.4f}, deviation={deviation:.4f}",
        )
