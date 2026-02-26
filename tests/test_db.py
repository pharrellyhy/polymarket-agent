"""Tests for SQLite database layer."""

import tempfile
from pathlib import Path

import pytest
from polymarket_agent.db import Database, Trade


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Database(Path(tmpdir) / "test.db")


def test_db_initializes_tables(db):
    assert db._conn is not None


def test_record_and_query_trade(db):
    db.record_trade(
        Trade(
            strategy="signal_trader",
            market_id="100",
            token_id="0xtok1",
            side="buy",
            price=0.55,
            size=25.0,
            reason="test trade",
        )
    )
    trades = db.get_trades()
    assert len(trades) == 1
    assert trades[0]["market_id"] == "100"
    assert trades[0]["price"] == 0.55


def test_get_trades_by_strategy(db):
    db.record_trade(Trade(strategy="alpha", market_id="1", token_id="t1", side="buy", price=0.5, size=10, reason="a"))
    db.record_trade(Trade(strategy="beta", market_id="2", token_id="t2", side="sell", price=0.7, size=20, reason="b"))
    trades = db.get_trades(strategy="alpha")
    assert len(trades) == 1
    assert trades[0]["strategy"] == "alpha"


def test_db_context_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        with Database(Path(tmpdir) / "test.db") as db:
            db.record_trade(
                Trade(strategy="ctx", market_id="1", token_id="t1", side="buy", price=0.5, size=10, reason="ctx test")
            )
            assert len(db.get_trades()) == 1
        # After context manager exits, connection should be closed
        try:
            db.get_trades()
            assert False, "Expected error after context manager exit"
        except Exception:
            pass
