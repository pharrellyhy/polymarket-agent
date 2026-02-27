"""FastAPI HTTP API for the monitoring dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polymarket_agent import __version__
from polymarket_agent.db import Database
from polymarket_agent.execution.base import Portfolio

_STATIC_DIR = Path(__file__).parent / "static"


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

    @app.get("/")
    def dashboard_page() -> FileResponse:
        return FileResponse(_STATIC_DIR / "dashboard.html", media_type="text/html")

    return app


def _serialize_row(row: dict[str, object]) -> dict[str, Any]:
    """Convert a DB row dict to JSON-serializable form."""
    return {k: str(v) if not isinstance(v, (int, float, bool, str, type(None))) else v for k, v in row.items()}
