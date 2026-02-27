"""FastAPI HTTP API for the monitoring dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from polymarket_agent import __version__
from polymarket_agent.db import Database
from polymarket_agent.execution.base import Portfolio

_STATIC_DIR = Path(__file__).parent / "static"


class StrategyStats(TypedDict):
    trade_count: int
    wins: int
    net_pnl: float
    signal_count: int


def create_app(db: Database, get_portfolio: Any, get_recent_trades: Any) -> Any:
    """Create and return the FastAPI application.

    Args:
        db: Database instance for signal/snapshot queries.
        get_portfolio: Callable returning a Portfolio.
        get_recent_trades: Callable returning recent trades.

    Returns:
        A FastAPI application instance.
    """
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.responses import FileResponse, JSONResponse  # noqa: PLC0415

    app = FastAPI(title="Polymarket Agent Dashboard", version=__version__)

    @app.get("/api/health")
    def api_health() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    @app.get("/api/portfolio")
    def api_portfolio() -> JSONResponse:
        portfolio: Portfolio = get_portfolio()
        return JSONResponse(
            {
                "balance": portfolio.balance,
                "total_value": portfolio.total_value,
                "positions": portfolio.positions,
            }
        )

    @app.get("/api/trades")
    def api_trades(limit: int = 50) -> JSONResponse:
        trades = get_recent_trades(limit=limit)
        return JSONResponse(trades)

    @app.get("/api/signals")
    def api_signals(strategy: str | None = None, limit: int = 100) -> JSONResponse:
        signals = db.get_signal_log(strategy=strategy, limit=limit)
        return JSONResponse([_serialize_row(s) for s in signals])

    @app.get("/api/snapshots")
    def api_snapshots(limit: int = 100) -> JSONResponse:
        snapshots = db.get_portfolio_snapshots(limit=limit)
        result = []
        for snap in snapshots:
            row = _serialize_row(snap)
            if "positions_json" in row and isinstance(row["positions_json"], str):
                try:
                    row["positions"] = json.loads(row["positions_json"])
                except (json.JSONDecodeError, TypeError):
                    row["positions"] = {}
                del row["positions_json"]
            result.append(row)
        return JSONResponse(result)

    @app.get("/api/positions")
    def api_positions() -> JSONResponse:
        portfolio: Portfolio = get_portfolio()
        positions = []
        for token_id, pos in portfolio.positions.items():
            shares = _to_float(pos.get("shares", 0))
            avg_price = _to_float(pos.get("avg_price", 0))
            current_price = _to_float(pos.get("current_price", avg_price), default=avg_price)
            cost_basis = shares * avg_price
            market_value = shares * current_price
            unrealized_pnl = market_value - cost_basis
            unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis else 0.0
            positions.append(
                {
                    "token_id": token_id,
                    "shares": shares,
                    "avg_price": avg_price,
                    "current_price": current_price,
                    "unrealized_pnl": round(unrealized_pnl, 4),
                    "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                }
            )
        return JSONResponse(positions)

    @app.get("/api/strategy-performance")
    def api_strategy_performance() -> JSONResponse:
        trades = db.get_trades()
        signals = db.get_signal_log(limit=10000)

        # Group trades by strategy
        stats: dict[str, StrategyStats] = {}
        for t in trades:
            strat = str(t.get("strategy", "unknown"))
            bucket = _get_strategy_stats_bucket(stats, strat)
            bucket["trade_count"] += 1
            size = _to_float(t.get("size", 0))
            side = str(t.get("side", "")).lower()
            if side == "sell":
                bucket["net_pnl"] += size
                bucket["wins"] += 1
            elif side == "buy":
                bucket["net_pnl"] -= size

        # Count signals per strategy
        for s in signals:
            strat = str(s.get("strategy", "unknown"))
            bucket = _get_strategy_stats_bucket(stats, strat)
            bucket["signal_count"] += 1

        result = []
        for strat, bucket_stats in stats.items():
            trade_count = bucket_stats["trade_count"]
            win_rate = (bucket_stats["wins"] / trade_count * 100) if trade_count else 0.0
            result.append(
                {
                    "strategy": strat,
                    "trade_count": trade_count,
                    "win_rate": round(win_rate, 1),
                    "net_pnl": round(bucket_stats["net_pnl"], 4),
                    "signal_count": bucket_stats["signal_count"],
                }
            )
        return JSONResponse(result)

    @app.get("/api/config-changes")
    def api_config_changes(limit: int = 20) -> JSONResponse:
        changes = db.get_config_changes(limit=limit)
        result = []
        for c in changes:
            row = _serialize_row(c)
            if "diff_json" in row and isinstance(row["diff_json"], str):
                try:
                    row["diff"] = json.loads(row["diff_json"])
                except (json.JSONDecodeError, TypeError):
                    row["diff"] = {}
                del row["diff_json"]
            if "full_config_json" in row:
                del row["full_config_json"]
            result.append(row)
        return JSONResponse(result)

    @app.get("/api/conditional-orders")
    def api_conditional_orders(limit: int = 50) -> JSONResponse:
        orders = db.get_all_conditional_orders(limit=limit)
        return JSONResponse([_serialize_row(o) for o in orders])

    @app.get("/")
    def dashboard_page() -> FileResponse:
        return FileResponse(_STATIC_DIR / "dashboard.html", media_type="text/html")

    return app


def _serialize_row(row: dict[str, object]) -> dict[str, Any]:
    """Convert a DB row dict to JSON-serializable form."""
    return {k: str(v) if not isinstance(v, (int, float, bool, str, type(None))) else v for k, v in row.items()}


def _to_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float conversion for dashboard payload calculations."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_strategy_stats_bucket(stats: dict[str, StrategyStats], strategy: str) -> StrategyStats:
    """Return mutable aggregate bucket for a strategy, creating it if absent."""
    if strategy not in stats:
        stats[strategy] = {"trade_count": 0, "wins": 0, "net_pnl": 0.0, "signal_count": 0}
    return stats[strategy]
