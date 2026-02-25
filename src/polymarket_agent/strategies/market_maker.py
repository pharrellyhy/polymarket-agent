"""MarketMaker strategy — provides liquidity by quoting around the midpoint."""

from __future__ import annotations

import logging
from typing import Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_SPREAD: float = 0.05
_DEFAULT_MAX_INVENTORY: float = 500.0
_DEFAULT_MIN_LIQUIDITY: float = 1000.0
_DEFAULT_ORDER_SIZE: float = 50.0


class MarketMaker(Strategy):
    """Quote bid/ask around order book midpoint for active, liquid markets.

    For each qualifying market, emits a buy signal below midpoint and a sell
    signal above midpoint, separated by the configured spread.
    """

    name: str = "market_maker"

    def __init__(self) -> None:
        self._spread: float = _DEFAULT_SPREAD
        self._max_inventory: float = _DEFAULT_MAX_INVENTORY
        self._min_liquidity: float = _DEFAULT_MIN_LIQUIDITY
        self._order_size: float = _DEFAULT_ORDER_SIZE

    def configure(self, config: dict[str, Any]) -> None:
        self._spread = float(config.get("spread", _DEFAULT_SPREAD))
        self._max_inventory = float(config.get("max_inventory", _DEFAULT_MAX_INVENTORY))
        self._min_liquidity = float(config.get("min_liquidity", _DEFAULT_MIN_LIQUIDITY))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))

    def analyze(self, markets: list[Market], data: Any) -> list[Signal]:
        signals: list[Signal] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            if market.liquidity < self._min_liquidity:
                continue
            if not market.clob_token_ids:
                continue

            try:
                book = data.get_orderbook(market.clob_token_ids[0])
            except Exception:
                logger.debug("Failed to fetch orderbook for %s, skipping", market.id)
                continue

            midpoint: float = book.midpoint
            if midpoint <= 0:
                continue

            buy_price = round(midpoint - self._spread / 2, 4)
            sell_price = round(midpoint + self._spread / 2, 4)

            buy_price = max(0.01, min(buy_price, 0.99))
            sell_price = max(0.01, min(sell_price, 0.99))

            token_id_yes = market.clob_token_ids[0]
            token_id_no = market.clob_token_ids[1] if len(market.clob_token_ids) > 1 else ""

            signals.append(
                Signal(
                    strategy=self.name,
                    market_id=market.id,
                    token_id=token_id_yes,
                    side="buy",
                    confidence=0.5,
                    target_price=buy_price,
                    size=self._order_size,
                    reason=f"MM bid @ {buy_price:.4f} (mid={midpoint:.4f}, spread={self._spread})",
                )
            )
            signals.append(
                Signal(
                    strategy=self.name,
                    market_id=market.id,
                    token_id=token_id_no if token_id_no else token_id_yes,
                    side="sell",
                    confidence=0.5,
                    target_price=sell_price,
                    size=self._order_size,
                    reason=f"MM ask @ {sell_price:.4f} (mid={midpoint:.4f}, spread={self._spread})",
                )
            )
        return signals
