"""MarketMaker strategy — provides liquidity by quoting around the midpoint."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy

if TYPE_CHECKING:
    from polymarket_agent.data.provider import DataProvider

logger = logging.getLogger(__name__)

_DEFAULT_SPREAD: float = 0.05
_DEFAULT_MIN_LIQUIDITY: float = 1000.0
_DEFAULT_ORDER_SIZE: float = 50.0


def _clamp(value: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(value, hi))


class MarketMaker(Strategy):
    """Quote bid/ask around order book midpoint for active, liquid markets.

    For each qualifying market, emits a buy signal below midpoint and a sell
    signal above midpoint, separated by the configured spread.
    """

    name: str = "market_maker"

    def __init__(self) -> None:
        self._spread: float = _DEFAULT_SPREAD
        self._min_liquidity: float = _DEFAULT_MIN_LIQUIDITY
        self._order_size: float = _DEFAULT_ORDER_SIZE

    def configure(self, config: dict[str, Any]) -> None:
        self._spread = float(config.get("spread", _DEFAULT_SPREAD))
        self._min_liquidity = float(config.get("min_liquidity", _DEFAULT_MIN_LIQUIDITY))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))

    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        signals: list[Signal] = []
        for market in markets:
            signals.extend(self._quote_market(market, data))
        return signals

    def _quote_market(self, market: Market, data: DataProvider) -> list[Signal]:
        if not market.active or market.closed:
            return []
        if market.liquidity < self._min_liquidity:
            return []
        if len(market.clob_token_ids) < 2:
            return []

        try:
            book = data.get_orderbook(market.clob_token_ids[0])
        except RuntimeError:
            logger.debug("Failed to fetch orderbook for %s, skipping", market.id)
            return []

        if book.best_bid <= 0 or book.best_ask <= 0:
            return []

        midpoint: float = book.midpoint
        if midpoint <= 0:
            return []

        half_spread = self._spread / 2
        buy_price = _clamp(round(midpoint - half_spread, 4))
        sell_price = _clamp(round(midpoint + half_spread, 4))

        token_yes = market.clob_token_ids[0]
        token_sell = market.clob_token_ids[1]

        def _signal(side: Literal["buy", "sell"], price: float, token_id: str) -> Signal:
            label = "bid" if side == "buy" else "ask"
            return Signal(
                strategy=self.name,
                market_id=market.id,
                token_id=token_id,
                side=side,
                confidence=0.5,
                target_price=price,
                size=self._order_size,
                reason=f"MM {label} @ {price:.4f} (mid={midpoint:.4f}, spread={self._spread})",
            )

        return [
            _signal("buy", buy_price, token_yes),
            _signal("sell", sell_price, token_sell),
        ]
