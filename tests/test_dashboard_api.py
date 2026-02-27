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
    return Portfolio(balance=950.0, positions={"tok1": {"shares": 10, "avg_price": 0.6, "current_price": 0.7}})


def _get_trades(limit: int = 50) -> list[dict[str, object]]:
    return [
        {
            "timestamp": "2026-01-01",
            "strategy": "test",
            "market_id": "m1",
            "token_id": "t1",
            "side": "buy",
            "price": 0.5,
            "size": 25.0,
            "reason": "test",
        },
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
            strategy="arb",
            market_id="m1",
            token_id="t1",
            side="buy",
            confidence=0.9,
            size=10.0,
        )
        self.db.record_signal(
            strategy="signal_trader",
            market_id="m2",
            token_id="t2",
            side="sell",
            confidence=0.7,
            size=20.0,
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

    def test_positions_endpoint(self) -> None:
        resp = self.client.get("/api/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        pos = data[0]
        assert pos["token_id"] == "tok1"
        assert pos["shares"] == 10
        assert pos["avg_price"] == 0.6
        assert pos["current_price"] == 0.7
        assert pos["unrealized_pnl"] == 1.0  # 10 * 0.7 - 10 * 0.6
        assert pos["unrealized_pnl_pct"] > 0

    def test_positions_endpoint_empty(self) -> None:
        try:
            from fastapi.testclient import TestClient  # noqa: PLC0415

            from polymarket_agent.dashboard.api import create_app  # noqa: PLC0415
        except ImportError:
            pytest.skip("fastapi not installed")

        empty_portfolio = lambda: Portfolio(balance=1000.0, positions={})  # noqa: E731
        app = create_app(db=self.db, get_portfolio=empty_portfolio, get_recent_trades=_get_trades)
        client = TestClient(app)
        resp = client.get("/api/positions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_strategy_performance_endpoint_empty(self) -> None:
        resp = self.client.get("/api/strategy-performance")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_strategy_performance_endpoint_with_data(self) -> None:
        from polymarket_agent.db import Trade  # noqa: PLC0415

        self.db.record_trade(
            Trade(
                strategy="signal_trader",
                market_id="m1",
                token_id="t1",
                side="buy",
                price=0.5,
                size=25.0,
                reason="test buy",
            )
        )
        self.db.record_trade(
            Trade(
                strategy="signal_trader",
                market_id="m1",
                token_id="t1",
                side="sell",
                price=0.6,
                size=30.0,
                reason="test sell",
            )
        )
        self.db.record_signal(
            strategy="signal_trader",
            market_id="m1",
            token_id="t1",
            side="buy",
            confidence=0.8,
            size=25.0,
            status="generated",
        )
        resp = self.client.get("/api/strategy-performance")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        strat = data[0]
        assert strat["strategy"] == "signal_trader"
        assert strat["trade_count"] == 2
        assert strat["win_rate"] == 50.0
        assert strat["net_pnl"] == 5.0  # -25 + 30
        assert strat["signal_count"] == 1

    def test_strategy_performance_handles_non_numeric_trade_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _mock_trades() -> list[dict[str, object]]:
            return [
                {"strategy": "signal_trader", "side": "buy", "size": None},
                {"strategy": "signal_trader", "side": "sell", "size": "bad"},
            ]

        monkeypatch.setattr(self.db, "get_trades", _mock_trades)

        resp = self.client.get("/api/strategy-performance")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["strategy"] == "signal_trader"
        assert data[0]["trade_count"] == 2
        assert data[0]["win_rate"] == 50.0
        assert data[0]["net_pnl"] == 0.0

    def test_conditional_orders_endpoint_empty(self) -> None:
        resp = self.client.get("/api/conditional-orders")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_conditional_orders_endpoint_with_data(self) -> None:
        from polymarket_agent.orders import OrderType  # noqa: PLC0415

        self.db.create_conditional_order(
            token_id="t1",
            market_id="m1",
            order_type=OrderType.STOP_LOSS,
            trigger_price=0.4,
            size=25.0,
            parent_strategy="signal_trader",
            reason="Auto stop-loss",
        )
        resp = self.client.get("/api/conditional-orders")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["order_type"] == "stop_loss"
        assert data[0]["status"] == "active"

    def test_config_changes_endpoint_empty(self) -> None:
        resp = self.client.get("/api/config-changes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_config_changes_endpoint_with_data(self) -> None:
        import json as _json  # noqa: PLC0415

        self.db.record_config_change(
            changed_by="hot_reload",
            diff_json=_json.dumps({"poll_interval": {"old": 60, "new": 120}}),
            full_config_json="{}",
        )
        resp = self.client.get("/api/config-changes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["changed_by"] == "hot_reload"
        assert "diff" in data[0]
        assert data[0]["diff"]["poll_interval"]["old"] == 60
        assert "full_config_json" not in data[0]

    def test_dashboard_page(self) -> None:
        resp = self.client.get("/")
        assert resp.status_code == 200
        assert "Polymarket Agent" in resp.text
