"""Tests for the dashboard HTTP API."""

from pathlib import Path

import pytest
from polymarket_agent.db import Database
from polymarket_agent.execution.base import Portfolio


def _make_db(tmp_path: Path) -> Database:
    """Create a test database with signal_log and portfolio_snapshots."""
    db = Database(tmp_path / "test.db")
    return db


def _make_portfolio() -> Portfolio:
    return Portfolio(balance=950.0, positions={"tok1": {"shares": 10, "avg_price": 0.6}})


def _get_trades(limit: int = 50) -> list[dict[str, object]]:
    return [
        {"timestamp": "2026-01-01", "strategy": "test", "market_id": "m1", "token_id": "t1",
         "side": "buy", "price": 0.5, "size": 25.0, "reason": "test"},
    ]


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return _make_db(tmp_path)


class TestDashboardAPI:
    """Test the FastAPI dashboard endpoints."""

    @pytest.fixture(autouse=True)
    def _setup_app(self, db: Database) -> None:
        try:
            from fastapi.testclient import TestClient  # noqa: PLC0415
            from polymarket_agent.dashboard.api import create_app  # noqa: PLC0415
        except ImportError:
            pytest.skip("fastapi not installed")

        self.app = create_app(
            db=db,
            get_portfolio=_make_portfolio,
            get_recent_trades=_get_trades,
        )
        self.client = TestClient(self.app)
        self.db = db

    def test_health_endpoint(self) -> None:
        resp = self.client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_portfolio_endpoint(self) -> None:
        resp = self.client.get("/api/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == 950.0
        assert "total_value" in data
        assert "positions" in data

    def test_trades_endpoint(self) -> None:
        resp = self.client.get("/api/trades?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["strategy"] == "test"

    def test_signals_endpoint_empty(self) -> None:
        resp = self.client.get("/api/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    def test_signals_endpoint_with_data(self) -> None:
        self.db.record_signal(
            strategy="signal_trader",
            market_id="m1",
            token_id="t1",
            side="buy",
            confidence=0.8,
            size=25.0,
            status="generated",
        )
        resp = self.client.get("/api/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["strategy"] == "signal_trader"

    def test_signals_endpoint_filter_by_strategy(self) -> None:
        self.db.record_signal(
            strategy="arb", market_id="m1", token_id="t1",
            side="buy", confidence=0.9, size=10.0,
        )
        self.db.record_signal(
            strategy="signal_trader", market_id="m2", token_id="t2",
            side="sell", confidence=0.7, size=20.0,
        )
        resp = self.client.get("/api/signals?strategy=arb")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["strategy"] == "arb"

    def test_snapshots_endpoint_empty(self) -> None:
        resp = self.client.get("/api/snapshots")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_snapshots_endpoint_with_data(self) -> None:
        self.db.record_portfolio_snapshot(
            balance=1000.0,
            total_value=1050.0,
            positions_json='{"tok1": {"shares": 10}}',
        )
        resp = self.client.get("/api/snapshots")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["balance"] == 1000.0
        assert data[0]["total_value"] == 1050.0
        assert data[0]["positions"] == {"tok1": {"shares": 10}}

    def test_dashboard_page(self) -> None:
        resp = self.client.get("/")
        assert resp.status_code == 200
        assert "Polymarket Agent" in resp.text
