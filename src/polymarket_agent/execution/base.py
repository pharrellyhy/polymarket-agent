"""Executor base class and portfolio model."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from polymarket_agent.strategies.base import Signal


@dataclass
class Portfolio:
    """Current portfolio state."""

    balance: float
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def total_value(self) -> float:
        """Calculate total portfolio value including open positions."""
        position_value = sum(self._position_value(p) for p in self.positions.values())
        return self.balance + position_value

    @staticmethod
    def _position_value(pos: dict[str, Any]) -> float:
        shares: float = float(pos.get("shares", 0))
        price = pos.get("current_price")
        if price is None:
            price = pos.get("avg_price", 0)
        return shares * float(price)


@dataclass
class Order:
    """A filled order."""

    market_id: str
    token_id: str
    side: str
    price: float
    size: float
    shares: float


class Executor(ABC):
    """Base class for trade execution."""

    @abstractmethod
    def place_order(self, signal: Signal) -> Order | None:
        """Place an order based on a trade signal. Returns None if the order cannot be filled."""

    @abstractmethod
    def get_portfolio(self) -> Portfolio:
        """Return the current portfolio state."""

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if cancelled, False otherwise."""
        return False

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Return currently open (unfilled) orders."""
        return []
