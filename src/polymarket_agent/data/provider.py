"""DataProvider protocol for data layer abstraction.

Both the live CLI wrapper (PolymarketData) and the historical backtest
provider satisfy this protocol via structural typing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from polymarket_agent.data.models import Market, OrderBook, PricePoint, Spread


class DataProvider(Protocol):
    """Structural protocol for market data providers.

    Any class that implements these four methods can be used by the
    orchestrator and strategy engine without modification.
    """

    def get_active_markets(self, *, tag: str | None = None, limit: int = 50) -> list[Market]: ...

    def get_orderbook(self, token_id: str) -> OrderBook: ...

    def get_price(self, token_id: str) -> Spread: ...

    def get_price_history(
        self,
        token_id: str,
        *,
        interval: str = "1d",
        fidelity: int = 60,
    ) -> list[PricePoint]: ...
