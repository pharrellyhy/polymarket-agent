"""Pydantic data models for Polymarket CLI JSON output."""

import json
from typing import Any

from pydantic import BaseModel, Field


def _parse_json_field(data: dict[str, Any], key: str) -> list[Any]:
    """Parse a CLI field that may be a JSON-encoded string or already a list."""
    raw = data.get(key)
    if raw is None:
        return []
    if isinstance(raw, str):
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    return raw if isinstance(raw, list) else []


def _str_field(data: dict[str, Any], key: str) -> str:
    """Extract an optional string field, defaulting to empty string."""
    return data.get(key) or ""


def _float_field(data: dict[str, Any], key: str) -> float:
    """Extract an optional numeric field, defaulting to 0.0."""
    return float(data.get(key) or 0)


def _float_field_first(data: dict[str, Any], *keys: str) -> float:
    """Extract the first present numeric field, preserving valid 0.0 values."""
    for key in keys:
        if key in data:
            return float(data.get(key) or 0)
    return 0.0


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
    def from_cli(cls, data: dict[str, Any]) -> "Market":
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
    def from_cli(cls, data: dict[str, Any]) -> "Event":
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
            markets=[Market.from_cli(m) for m in (data.get("markets") or [])],
        )


class Price(BaseModel):
    """A price point for a market outcome."""

    outcome: str
    price: float

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> "Price":
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
    def from_cli(cls, data: dict[str, Any]) -> "PricePoint":
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
    def from_cli(cls, data: dict[str, Any]) -> "OrderBookLevel":
        """Parse an order-book level dict from the polymarket CLI JSON output."""
        return cls(
            price=float(data["price"]),
            size=float(data["size"]),
        )


class Trader(BaseModel):
    """A trader from the Polymarket leaderboard."""

    rank: int
    name: str
    volume: float = 0.0
    pnl: float = 0.0
    markets_traded: int = 0

    @classmethod
    def from_cli(cls, data: dict[str, Any], rank: int = 0) -> "Trader":
        """Parse a trader dict from the polymarket CLI JSON output."""
        return cls(
            rank=rank,
            name=_str_field(data, "name") or _str_field(data, "username") or "unknown",
            volume=_float_field(data, "volume"),
            pnl=float(data["pnl"]) if "pnl" in data else _float_field(data, "profit"),
            markets_traded=int(data["marketsTraded"])
            if "marketsTraded" in data
            else int(_float_field(data, "markets_traded")),
        )


class Spread(BaseModel):
    """Bid-ask spread for a CLOB token."""

    token_id: str
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0

    @classmethod
    def from_cli(cls, token_id: str, data: dict[str, Any]) -> "Spread":
        """Parse a spread dict from the polymarket CLI JSON output."""
        return cls(
            token_id=token_id,
            bid=_float_field_first(data, "bid", "bestBid", "best_bid"),
            ask=_float_field_first(data, "ask", "bestAsk", "best_ask"),
            spread=float(data.get("spread", 0)),
        )

    @classmethod
    def from_orderbook(cls, token_id: str, book: "OrderBook") -> "Spread":
        """Derive spread from an order book."""
        return cls(
            token_id=token_id,
            bid=book.best_bid,
            ask=book.best_ask,
            spread=book.spread,
        )


class Volume(BaseModel):
    """Aggregated volume for an event."""

    event_id: str
    total: float

    @classmethod
    def from_cli(cls, event_id: str, data: list[dict[str, Any]]) -> "Volume":
        """Parse volume data from the polymarket CLI JSON output."""
        if data and isinstance(data, list) and len(data) > 0:
            return cls(event_id=event_id, total=float(data[0].get("total", 0)))
        return cls(event_id=event_id, total=0.0)


class Position(BaseModel):
    """An open position for a wallet address."""

    market: str = ""
    outcome: str = ""
    shares: float = 0.0
    avg_price: float = 0.0
    current_price: float = 0.0
    pnl: float = 0.0

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> "Position":
        """Parse a position dict from the polymarket CLI JSON output."""
        return cls(
            market=_str_field(data, "market") or _str_field(data, "conditionId") or _str_field(data, "title"),
            outcome=_str_field(data, "outcome") or _str_field(data, "asset"),
            shares=_float_field_first(data, "size", "shares"),
            avg_price=_float_field_first(data, "avgPrice", "avg_price"),
            current_price=_float_field_first(data, "currentPrice", "current_price"),
            pnl=_float_field_first(data, "pnl", "profit"),
        )


class OrderBook(BaseModel):
    """An order book with asks and bids."""

    asks: list[OrderBookLevel]
    bids: list[OrderBookLevel]

    @classmethod
    def from_cli(cls, data: dict[str, Any]) -> "OrderBook":
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
        """Midpoint between best bid and best ask. Returns 0.0 if either side is empty."""
        if not self.asks or not self.bids:
            return 0.0
        return (self.best_ask + self.best_bid) / 2

    @property
    def spread(self) -> float:
        """Spread between best ask and best bid. Returns 0.0 if either side is empty."""
        if not self.asks or not self.bids:
            return 0.0
        return self.best_ask - self.best_bid
