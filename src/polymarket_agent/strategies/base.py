"""Strategy base class and Signal model."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from polymarket_agent.data.models import Market


@dataclass
class Signal:
    """A trade signal emitted by a strategy."""

    strategy: str
    market_id: str
    token_id: str
    side: Literal["buy", "sell"]
    confidence: float
    target_price: float
    size: float
    reason: str


class Strategy(ABC):
    """Base class for all trading strategies."""

    name: str

    @abstractmethod
    def analyze(self, markets: list[Market], data: Any) -> list[Signal]:
        """Analyze markets and return trade signals."""

    def configure(self, config: dict[str, Any]) -> None:  # noqa: B027
        """Load strategy-specific config. Override in subclasses."""
