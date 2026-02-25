"""Pydantic data models for Polymarket CLI JSON output."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


def _parse_json_field(data: dict[str, Any], key: str) -> list[Any]:
    """Parse a CLI field that may be a JSON-encoded string or already a list."""
    raw = data.get(key, "[]")
    return json.loads(raw) if isinstance(raw, str) else raw


def _str_field(data: dict[str, Any], key: str) -> str:
    """Extract an optional string field, defaulting to empty string."""
    return data.get(key) or ""


def _float_field(data: dict[str, Any], key: str) -> float:
    """Extract an optional numeric field, defaulting to 0.0."""
    return float(data.get(key) or 0)


class Market(BaseModel):
    """A single prediction market."""

    id: str
    question: str
    outcomes: list[str]
    outcome_prices: list[float]
    volume: float
    liquidity: float = 0.0
    active: bool
    closed: bool
    condition_id: str = ""
    slug: str = ""
    end_date: str = ""
    description: str = ""
    clob_token_ids: list[str] = Field(default_factory=list)
    volume_24h: float = 0.0
    group_item_title: str = ""

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> Market:
        """Parse a market dict from the polymarket CLI JSON output."""
        return cls(
            id=str(data["id"]),
            question=data["question"],
            outcomes=_parse_json_field(data, "outcomes"),
            outcome_prices=[float(p) for p in _parse_json_field(data, "outcomePrices")],
            volume=_float_field(data, "volume"),
            liquidity=_float_field(data, "liquidity"),
            active=data["active"],
            closed=data["closed"],
            condition_id=_str_field(data, "conditionId"),
            slug=_str_field(data, "slug"),
            end_date=_str_field(data, "endDate"),
            description=_str_field(data, "description"),
            clob_token_ids=_parse_json_field(data, "clobTokenIds"),
            volume_24h=_float_field(data, "volume24hr"),
            group_item_title=_str_field(data, "groupItemTitle"),
        )


class Event(BaseModel):
    """A Polymarket event containing one or more markets."""

    id: str
    title: str
    description: str = ""
    ticker: str = ""
    slug: str = ""
    start_date: str = ""
    end_date: str = ""
    active: bool
    closed: bool
    liquidity: float = 0.0
    volume: float = 0.0
    volume_24h: float = 0.0
    markets: list[Market] = Field(default_factory=list)

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> Event:
        """Parse an event dict from the polymarket CLI JSON output."""
        return cls(
            id=str(data["id"]),
            title=data["title"],
            description=_str_field(data, "description"),
            ticker=_str_field(data, "ticker"),
            slug=_str_field(data, "slug"),
            start_date=_str_field(data, "startDate"),
            end_date=_str_field(data, "endDate"),
            active=data["active"],
            closed=data["closed"],
            liquidity=_float_field(data, "liquidity"),
            volume=_float_field(data, "volume"),
            volume_24h=_float_field(data, "volume24hr"),
            markets=[Market.from_cli(m) for m in data.get("markets", [])],
        )


class Price(BaseModel):
    """A price point for a market outcome."""

    outcome: str
    price: float

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> Price:
        """Parse a price dict from the polymarket CLI JSON output."""
        return cls(
            outcome=data["outcome"],
            price=float(data["price"]),
        )


class PricePoint(BaseModel):
    """A timestamped price observation."""

    timestamp: str
    price: float

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> PricePoint:
        """Parse a price-point dict from the polymarket CLI JSON output."""
        return cls(
            timestamp=data["timestamp"],
            price=float(data["price"]),
        )


class OrderBookLevel(BaseModel):
    """A single level (price + size) in an order book."""

    price: float
    size: float

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> OrderBookLevel:
        """Parse an order-book level dict from the polymarket CLI JSON output."""
        return cls(
            price=float(data["price"]),
            size=float(data["size"]),
        )


class OrderBook(BaseModel):
    """An order book with asks and bids."""

    asks: list[OrderBookLevel]
    bids: list[OrderBookLevel]

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> OrderBook:
        """Parse an order-book dict from the polymarket CLI JSON output."""
        asks = [OrderBookLevel.from_cli(a) for a in data.get("asks", [])]
        bids = [OrderBookLevel.from_cli(b) for b in data.get("bids", [])]
        return cls(asks=asks, bids=bids)

    @property
    def best_ask(self) -> float:
        """Lowest ask price."""
        if not self.asks:
            return 0.0
        return min(level.price for level in self.asks)

    @property
    def best_bid(self) -> float:
        """Highest bid price."""
        if not self.bids:
            return 0.0
        return max(level.price for level in self.bids)

    @property
    def midpoint(self) -> float:
        """Midpoint between best bid and best ask."""
        return (self.best_ask + self.best_bid) / 2

    @property
    def spread(self) -> float:
        """Spread between best ask and best bid."""
        return self.best_ask - self.best_bid
