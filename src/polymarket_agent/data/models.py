"""Pydantic data models for Polymarket CLI JSON output."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


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
        outcomes_raw = data.get("outcomes", "[]")
        outcomes: list[str] = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw

        prices_raw = data.get("outcomePrices", "[]")
        prices_parsed: list[str] = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        outcome_prices: list[float] = [float(p) for p in prices_parsed]

        token_ids_raw = data.get("clobTokenIds", "[]")
        clob_token_ids: list[str] = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw

        return cls(
            id=str(data["id"]),
            question=data["question"],
            outcomes=outcomes,
            outcome_prices=outcome_prices,
            volume=float(data.get("volume") or 0),
            liquidity=float(data.get("liquidity") or 0),
            active=data["active"],
            closed=data["closed"],
            condition_id=data.get("conditionId") or "",
            slug=data.get("slug") or "",
            end_date=data.get("endDate") or "",
            description=data.get("description") or "",
            clob_token_ids=clob_token_ids,
            volume_24h=float(data.get("volume24hr") or 0),
            group_item_title=data.get("groupItemTitle") or "",
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
        raw_markets: list[dict[str, Any]] = data.get("markets", [])
        markets = [Market.from_cli(m) for m in raw_markets]

        return cls(
            id=str(data["id"]),
            title=data["title"],
            description=data.get("description") or "",
            ticker=data.get("ticker") or "",
            slug=data.get("slug") or "",
            start_date=data.get("startDate") or "",
            end_date=data.get("endDate") or "",
            active=data["active"],
            closed=data["closed"],
            liquidity=float(data.get("liquidity") or 0),
            volume=float(data.get("volume") or 0),
            volume_24h=float(data.get("volume24hr") or 0),
            markets=markets,
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
