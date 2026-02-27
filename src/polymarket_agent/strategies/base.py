"""Strategy base class and Signal model."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from polymarket_agent.data.models import Market
    from polymarket_agent.data.provider import DataProvider


@dataclass
class Signal:
    """A trade signal emitted by a strategy.

    ``size`` is always denominated in USDC (not shares). The execution layer
    converts to share quantity internally (``shares = size / target_price``).
    """

    strategy: str
    market_id: str
    token_id: str
    side: Literal["buy", "sell"]
    confidence: float  # 0-1
    target_price: float  # desired entry price per share
    size: float  # USDC amount to trade
    reason: str
    stop_loss: float | None = None
    take_profit: float | None = None


class Strategy(ABC):
    """Base class for all trading strategies."""

    name: str

    @abstractmethod
    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        """Analyze markets and return trade signals."""

    def configure(self, config: dict[str, Any]) -> None:  # noqa: B027
        """Load strategy-specific config. Override in subclasses."""
