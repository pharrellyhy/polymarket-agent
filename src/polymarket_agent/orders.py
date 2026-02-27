"""Conditional order types: stop-loss, take-profit, trailing stop."""

from dataclasses import dataclass
from enum import Enum


class OrderType(str, Enum):
    """Type of conditional order."""

    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"


class OrderStatus(str, Enum):
    """Lifecycle status of a conditional order."""

    ACTIVE = "active"
    TRIGGERED = "triggered"
    CANCELLED = "cancelled"


@dataclass
class ConditionalOrder:
    """A conditional order that triggers when price conditions are met."""

    id: int
    token_id: str
    market_id: str
    order_type: OrderType
    status: OrderStatus
    trigger_price: float
    size: float
    parent_strategy: str
    reason: str
    high_watermark: float | None = None
    trail_percent: float | None = None
    created_at: str = ""
    triggered_at: str | None = None
