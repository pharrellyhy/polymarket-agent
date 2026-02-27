"""Tests for signal_log and portfolio_snapshots DB methods."""

from pathlib import Path

import pytest
from polymarket_agent.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


class TestSignalLog:
    def test_record_and_retrieve(self, db: Database) -> None:
        db.record_signal(
            strategy="signal_trader",
            market_id="m1",
            token_id="t1",
            side="buy",
            confidence=0.85,
            size=25.0,
        )
        log = db.get_signal_log()
        assert len(log) == 1
        assert log[0]["strategy"] == "signal_trader"
        assert log[0]["confidence"] == 0.85
        assert log[0]["status"] == "generated"

    def test_filter_by_strategy(self, db: Database) -> None:
        db.record_signal(strategy="arb", market_id="m1", token_id="t1", side="buy", confidence=0.9, size=10.0)
        db.record_signal(strategy="signal_trader", market_id="m2", token_id="t2", side="sell", confidence=0.7, size=20.0)
        log = db.get_signal_log(strategy="arb")
        assert len(log) == 1
        assert log[0]["strategy"] == "arb"

    def test_limit(self, db: Database) -> None:
        for i in range(5):
            db.record_signal(strategy="test", market_id=f"m{i}", token_id=f"t{i}", side="buy", confidence=0.5, size=10.0)
        log = db.get_signal_log(limit=3)
        assert len(log) == 3

    def test_custom_status(self, db: Database) -> None:
        db.record_signal(
            strategy="test",
            market_id="m1",
            token_id="t1",
            side="buy",
            confidence=0.8,
            size=25.0,
            status="executed",
        )
        log = db.get_signal_log()
        assert log[0]["status"] == "executed"


class TestPortfolioSnapshots:
    def test_record_and_retrieve(self, db: Database) -> None:
        db.record_portfolio_snapshot(balance=1000.0, total_value=1050.0, positions_json='{"t1": {"shares": 10}}')
        snaps = db.get_portfolio_snapshots()
        assert len(snaps) == 1
        assert snaps[0]["balance"] == 1000.0
        assert snaps[0]["total_value"] == 1050.0
        assert snaps[0]["positions_json"] == '{"t1": {"shares": 10}}'

    def test_limit(self, db: Database) -> None:
        for i in range(5):
            db.record_portfolio_snapshot(balance=1000.0 + i, total_value=1050.0 + i)
        snaps = db.get_portfolio_snapshots(limit=2)
        assert len(snaps) == 2

    def test_default_positions_json(self, db: Database) -> None:
        db.record_portfolio_snapshot(balance=500.0, total_value=500.0)
        snaps = db.get_portfolio_snapshots()
        assert snaps[0]["positions_json"] == "{}"

    def test_ordering_most_recent_first(self, db: Database) -> None:
        db.record_portfolio_snapshot(balance=100.0, total_value=100.0)
        db.record_portfolio_snapshot(balance=200.0, total_value=200.0)
        snaps = db.get_portfolio_snapshots()
        # Most recent first
        assert snaps[0]["balance"] == 200.0
        assert snaps[1]["balance"] == 100.0
