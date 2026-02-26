"""MCP server exposing Polymarket data and trading as tools for AI agents."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar, cast

from mcp.server.fastmcp import FastMCP

from polymarket_agent.config import AppConfig, load_config
from polymarket_agent.data.client import PolymarketData
from polymarket_agent.data.models import Market
from polymarket_agent.orchestrator import Orchestrator
from polymarket_agent.strategies.base import Signal

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Module-level config paths — override via configure() before calling mcp.run().
_config_path: Path = Path("config.yaml")
_db_path: Path = Path("polymarket_agent.db")


@dataclass
class AppContext:
    """Shared state for all MCP tools."""

    orchestrator: Orchestrator
    data: PolymarketData
    config: AppConfig


@asynccontextmanager
async def _app_lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize orchestrator and data client on startup."""
    cfg = load_config(_config_path) if _config_path.exists() else AppConfig()
    orch = Orchestrator(config=cfg, db_path=_db_path)
    try:
        yield AppContext(orchestrator=orch, data=orch.data, config=cfg)
    finally:
        orch.close()


mcp = FastMCP("polymarket-agent", lifespan=_app_lifespan)


def configure(config_path: Path, db_path: Path) -> None:
    """Set config and database paths before running the server."""
    global _config_path, _db_path  # noqa: PLW0603
    _config_path = config_path
    _db_path = db_path


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _get_ctx() -> AppContext:
    """Retrieve the shared AppContext from the MCP lifespan."""
    ctx: AppContext = mcp.get_context().request_context.lifespan_context
    return ctx


def _find_market(ctx: AppContext, market_id: str) -> Market | None:
    """Look up a market by ID, trying direct fetch first then list scan."""
    market = ctx.data.get_market(market_id)
    if market is not None:
        return market
    # Fallback: scan active markets list
    markets = ctx.data.get_active_markets(limit=100)
    return next((m for m in markets if m.id == market_id), None)


def _yes_price(market: Market) -> float | None:
    """Return the current Yes price when present."""
    return market.outcome_prices[0] if market.outcome_prices else None


def _validate_trade_inputs(side: str, size: float, price: float) -> str | None:
    """Validate manual trade inputs and return an error message if invalid."""
    if side not in ("buy", "sell"):
        return f"Invalid side '{side}', must be 'buy' or 'sell'"
    if size <= 0:
        return "Size must be greater than 0"
    if price <= 0 or price > 1:
        return "Price must be greater than 0 and at most 1"
    return None


def _serialize_signals(signals: list[Signal]) -> list[dict[str, Any]]:
    """Convert Signal dataclasses to MCP-friendly dicts."""
    return [asdict(signal) for signal in signals]


def _signals_snapshot(
    *,
    signals: list[Signal],
    updated_at: datetime | None,
    source: Literal["cache", "refresh"],
) -> dict[str, Any]:
    """Build a signal snapshot payload with freshness metadata."""
    freshness_seconds: float | None = None
    if updated_at is not None:
        freshness_seconds = max((datetime.now(timezone.utc) - updated_at).total_seconds(), 0.0)

    return {
        "signals": _serialize_signals(signals),
        "source": source,
        "last_updated": updated_at.isoformat() if updated_at is not None else None,
        "freshness_seconds": freshness_seconds,
    }


def _runtime_safe_tool(call: Callable[[], T], *, error_result: T) -> T:
    """Execute a tool payload builder and return a fallback on CLI RuntimeError."""
    try:
        return call()
    except RuntimeError:
        return error_result


# ------------------------------------------------------------------
# Read-only data tools
# ------------------------------------------------------------------


@mcp.tool()
def search_markets(query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Search active Polymarket prediction markets by keyword.

    Returns a list of matching markets with id, question, prices, and volume.
    """
    ctx = _get_ctx()
    markets = ctx.data.search_markets(query, limit=limit)
    return [
        {
            "id": m.id,
            "question": m.question,
            "outcomes": m.outcomes,
            "outcome_prices": m.outcome_prices,
            "volume": m.volume,
            "volume_24h": m.volume_24h,
            "liquidity": m.liquidity,
        }
        for m in markets
    ]


@mcp.tool()
def get_market_detail(market_id: str) -> dict[str, Any]:
    """Get full details for a specific market including orderbook data.

    Provide the market ID to get description, prices, volume, and live order book.
    """
    ctx = _get_ctx()
    market = _find_market(ctx, market_id)
    if market is None:
        return {"error": f"Market {market_id} not found"}

    result: dict[str, Any] = {
        "id": market.id,
        "question": market.question,
        "description": market.description,
        "outcomes": market.outcomes,
        "outcome_prices": market.outcome_prices,
        "volume": market.volume,
        "volume_24h": market.volume_24h,
        "liquidity": market.liquidity,
        "end_date": market.end_date,
        "clob_token_ids": market.clob_token_ids,
    }

    # Fetch orderbook for the first token if available
    if market.clob_token_ids:
        try:
            book = ctx.data.get_orderbook(market.clob_token_ids[0])
            result["orderbook"] = {
                "best_bid": book.best_bid,
                "best_ask": book.best_ask,
                "midpoint": book.midpoint,
                "spread": book.spread,
            }
        except RuntimeError:
            result["orderbook"] = {"error": "Failed to fetch orderbook"}

    return result


@mcp.tool()
def get_price_history(token_id: str, interval: str = "1d") -> list[dict[str, Any]]:
    """Get historical price data for a CLOB token.

    Use a token_id from get_market_detail's clob_token_ids field.
    Interval options: 1h, 6h, 1d, 1w, 1m, all.
    """
    ctx = _get_ctx()
    points = ctx.data.get_price_history(token_id, interval=interval)
    return [{"timestamp": p.timestamp, "price": p.price} for p in points]


@mcp.tool()
def get_event(event_id: str) -> dict[str, Any]:
    """Get details for a specific event by ID or slug.

    Returns event metadata including title, description, volume, and nested markets.
    """
    ctx = _get_ctx()

    def _build_payload() -> dict[str, Any]:
        event = ctx.data.get_event(event_id)
        if event is None:
            return {"error": f"Event {event_id} not found"}
        return {
            "id": event.id,
            "title": event.title,
            "description": event.description,
            "active": event.active,
            "closed": event.closed,
            "volume": event.volume,
            "volume_24h": event.volume_24h,
            "liquidity": event.liquidity,
            "start_date": event.start_date,
            "end_date": event.end_date,
            "markets": [
                {"id": m.id, "question": m.question, "outcome_prices": m.outcome_prices}
                for m in event.markets
            ],
        }

    return _runtime_safe_tool(_build_payload, error_result={"error": f"Failed to fetch event {event_id}"})


@mcp.tool()
def get_price(token_id: str) -> dict[str, Any]:
    """Get current bid/ask/spread for a CLOB token from the order book.

    Derives bid and ask from the live order book. Use a token_id from
    get_market_detail's clob_token_ids field.
    """
    ctx = _get_ctx()
    return _runtime_safe_tool(
        lambda: {
            "token_id": (spread := ctx.data.get_price(token_id)).token_id,
            "bid": spread.bid,
            "ask": spread.ask,
            "spread": spread.spread,
        },
        error_result={"error": f"Failed to fetch price for token {token_id}"},
    )


@mcp.tool()
def get_spread(token_id: str) -> dict[str, Any]:
    """Get the bid-ask spread for a CLOB token.

    Uses the CLOB spread endpoint. Returns the spread value.
    """
    ctx = _get_ctx()
    return _runtime_safe_tool(
        lambda: {"token_id": (spread := ctx.data.get_spread(token_id)).token_id, "spread": spread.spread},
        error_result={"error": f"Failed to fetch spread for token {token_id}"},
    )


@mcp.tool()
def get_volume(event_id: str) -> dict[str, Any]:
    """Get aggregated trading volume for an event.

    Returns total volume across all markets in the event.
    """
    ctx = _get_ctx()
    return _runtime_safe_tool(
        lambda: {"event_id": (volume := ctx.data.get_volume(event_id)).event_id, "total": volume.total},
        error_result={"error": f"Failed to fetch volume for event {event_id}"},
    )


@mcp.tool()
def get_positions(address: str, limit: int = 25) -> list[dict[str, Any]]:
    """Get open positions for a wallet address.

    Returns a list of positions with market, outcome, shares, and P&L data.
    """
    ctx = _get_ctx()
    return _runtime_safe_tool(
        lambda: [
            {
                "market": p.market,
                "outcome": p.outcome,
                "shares": p.shares,
                "avg_price": p.avg_price,
                "current_price": p.current_price,
                "pnl": p.pnl,
            }
            for p in ctx.data.get_positions(address, limit=limit)
        ],
        error_result=[{"error": f"Failed to fetch positions for {address}"}],
    )


@mcp.tool()
def get_leaderboard(period: str = "month") -> list[dict[str, Any]]:
    """Get top Polymarket traders ranked by performance.

    Period options: day, week, month, all.
    """
    ctx = _get_ctx()
    traders = ctx.data.get_leaderboard(period=period)
    return [
        {
            "rank": t.rank,
            "name": t.name,
            "volume": t.volume,
            "pnl": t.pnl,
            "markets_traded": t.markets_traded,
        }
        for t in traders
    ]


# ------------------------------------------------------------------
# Portfolio and strategy tools
# ------------------------------------------------------------------


@mcp.tool()
def get_portfolio() -> dict[str, Any]:
    """Get current portfolio state including balance, positions, and total value."""
    ctx = _get_ctx()
    portfolio = ctx.orchestrator.get_portfolio()
    return {
        "balance": portfolio.balance,
        "total_value": portfolio.total_value,
        "positions": portfolio.positions,
        "recent_trades": ctx.orchestrator.get_recent_trades(limit=10),
    }


@mcp.tool()
def get_signals() -> dict[str, Any]:
    """Return the latest cached aggregated signals without recomputing.

    This is a read-only snapshot from the most recent tick/refresh. It does not
    call strategies (including AIAnalyst), so it will not consume AI quota.
    """
    ctx = _get_ctx()
    return _signals_snapshot(
        signals=ctx.orchestrator.get_cached_signals(),
        updated_at=ctx.orchestrator.get_cached_signals_updated_at(),
        source="cache",
    )


@mcp.tool()
def refresh_signals() -> dict[str, Any]:
    """Recompute aggregated signals now and return a fresh snapshot.

    This may call AI-backed strategies (for example AIAnalyst) and consume API
    quota/cost depending on the active strategy configuration.
    """
    ctx = _get_ctx()
    signals = ctx.orchestrator.generate_signals()
    return _signals_snapshot(
        signals=signals,
        updated_at=ctx.orchestrator.get_cached_signals_updated_at(),
        source="refresh",
    )


@mcp.tool()
def place_trade(
    market_id: str,
    token_id: str,
    side: str,
    size: float,
    price: float,
) -> dict[str, Any]:
    """Execute a trade (paper or live depending on config mode).

    Args:
        market_id: The market to trade in.
        token_id: The CLOB token ID for the outcome.
        side: 'buy' or 'sell'.
        size: Amount in USDC to trade.
        price: Target entry price per share.
    """
    if error := _validate_trade_inputs(side, size, price):
        return {"error": error}

    ctx = _get_ctx()

    if ctx.config.mode == "monitor":
        return {"error": "Trading is disabled in monitor mode"}

    trade_side = cast(Literal["buy", "sell"], side)
    signal = Signal(
        strategy="mcp_manual",
        market_id=market_id,
        token_id=token_id,
        side=trade_side,
        confidence=1.0,
        target_price=price,
        size=size,
        reason="Manual trade via MCP tool",
    )

    order = ctx.orchestrator.place_order(signal)
    if order is None:
        return {"error": "Order could not be filled"}

    return {
        "status": "filled",
        "market_id": order.market_id,
        "token_id": order.token_id,
        "side": order.side,
        "price": order.price,
        "size": order.size,
        "shares": order.shares,
    }


# ------------------------------------------------------------------
# AI analysis tool
# ------------------------------------------------------------------


@mcp.tool()
def analyze_market(market_id: str) -> dict[str, Any]:
    """Run AI probability analysis on a specific market.

    Uses Claude to estimate the probability of the market resolving Yes.
    Returns the AI estimate, current market price, and divergence.
    Requires ANTHROPIC_API_KEY to be set.
    """
    ctx = _get_ctx()

    # Find the AIAnalyst strategy instance
    from polymarket_agent.strategies.ai_analyst import AIAnalyst  # noqa: PLC0415

    analyst = next((s for s in ctx.orchestrator.strategies if isinstance(s, AIAnalyst)), None)

    if analyst is None:
        return {"error": "AIAnalyst strategy is not enabled in config"}

    if analyst._client is None:
        return {"error": "AI analysis unavailable (missing ANTHROPIC_API_KEY or anthropic package)"}

    # Find the market
    market = _find_market(ctx, market_id)
    if market is None:
        return {"error": f"Market {market_id} not found"}

    # Run analysis on this single market
    signals = analyst.analyze([market], ctx.data)
    if not signals:
        return {
            "market_id": market.id,
            "question": market.question,
            "current_price": _yes_price(market),
            "result": "No divergence detected — AI estimate is close to market price",
        }

    signal = signals[0]
    return {
        "market_id": market.id,
        "question": market.question,
        "current_price": _yes_price(market),
        "signal": asdict(signal),
    }
