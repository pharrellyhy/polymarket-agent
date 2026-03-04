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


# ------------------------------------------------------------------
# Signal outcome methods (Phase A)
# ------------------------------------------------------------------


def test_record_signal_outcome(db):
    row_id = db.record_signal_outcome(
        strategy="ai_analyst",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=0.85,
        predicted_price=0.50,
        entry_price=0.50,
        size=25.0,
    )
    assert row_id > 0

    # Pending outcomes should be retrievable
    pending = db.get_pending_outcomes_by_market()
    assert "100" in pending
    assert len(pending["100"]) == 1
    assert pending["100"][0]["strategy"] == "ai_analyst"
    assert pending["100"][0]["outcome"] == "pending"


def test_resolve_signal_outcomes_buy_win(db):
    """Resolving a buy signal where price went up produces a win."""
    db.record_signal_outcome(
        strategy="ai_analyst",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=0.8,
        predicted_price=0.50,
        entry_price=0.50,
        size=25.0,
    )

    resolved = db.resolve_signal_outcomes("100", resolved_price=1.0)
    assert resolved == 1

    # Verify P&L: shares = 25/0.5 = 50, pnl = (1.0 - 0.5) * 50 = 25.0
    stats = db.get_strategy_accuracy()
    assert len(stats) == 1
    assert stats[0]["strategy"] == "ai_analyst"
    assert stats[0]["wins"] == 1
    assert stats[0]["win_rate"] == 1.0
    assert stats[0]["total_pnl"] == 25.0


def test_resolve_signal_outcomes_buy_loss(db):
    """Resolving a buy signal where price went to 0 produces a loss."""
    db.record_signal_outcome(
        strategy="signal_trader",
        market_id="200",
        token_id="0xtok2",
        side="buy",
        confidence=0.7,
        predicted_price=0.60,
        entry_price=0.60,
        size=30.0,
    )

    resolved = db.resolve_signal_outcomes("200", resolved_price=0.0)
    assert resolved == 1

    stats = db.get_strategy_accuracy()
    assert stats[0]["wins"] == 0
    assert stats[0]["win_rate"] == 0.0
    assert stats[0]["total_pnl"] == pytest.approx(-30.0)


def test_resolve_signal_outcomes_sell_win(db):
    """Resolving a sell signal where price went down produces a win."""
    db.record_signal_outcome(
        strategy="ai_analyst",
        market_id="300",
        token_id="0xtok3",
        side="sell",
        confidence=0.75,
        predicted_price=0.40,
        entry_price=0.40,
        size=20.0,
    )

    resolved = db.resolve_signal_outcomes("300", resolved_price=0.0)
    assert resolved == 1

    stats = db.get_strategy_accuracy()
    assert stats[0]["wins"] == 1
    # P&L: shares = 20/0.4 = 50, pnl = (0.4 - 0.0) * 50 = 20.0
    assert stats[0]["total_pnl"] == pytest.approx(20.0)


def test_resolve_multiple_outcomes_same_market(db):
    """Multiple signals on the same market are resolved together."""
    for strategy in ["ai_analyst", "signal_trader"]:
        db.record_signal_outcome(
            strategy=strategy,
            market_id="100",
            token_id="0xtok1",
            side="buy",
            confidence=0.8,
            predicted_price=0.50,
            entry_price=0.50,
            size=10.0,
        )

    resolved = db.resolve_signal_outcomes("100", resolved_price=1.0)
    assert resolved == 2

    stats = db.get_strategy_accuracy()
    assert len(stats) == 2
    for s in stats:
        assert s["wins"] == 1


def test_resolve_idempotent(db):
    """Resolving the same market twice doesn't re-resolve already resolved outcomes."""
    db.record_signal_outcome(
        strategy="ai_analyst",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=0.8,
        predicted_price=0.50,
        entry_price=0.50,
        size=25.0,
    )

    assert db.resolve_signal_outcomes("100", resolved_price=1.0) == 1
    assert db.resolve_signal_outcomes("100", resolved_price=1.0) == 0


def test_brier_score_computed(db):
    """Brier score should measure calibration of the confidence."""
    db.record_signal_outcome(
        strategy="ai_analyst",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=0.8,
        predicted_price=0.50,
        entry_price=0.50,
        size=25.0,
    )
    db.resolve_signal_outcomes("100", resolved_price=1.0)

    stats = db.get_strategy_accuracy()
    # Brier: (0.8 - 1.0)^2 = 0.04
    assert stats[0]["avg_brier"] == pytest.approx(0.04)


def test_get_strategy_pnl(db):
    """get_strategy_pnl returns P&L summary with profit factor."""
    db.record_signal_outcome(
        strategy="ai_analyst",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=0.8,
        predicted_price=0.50,
        entry_price=0.50,
        size=25.0,
    )
    db.record_signal_outcome(
        strategy="ai_analyst",
        market_id="200",
        token_id="0xtok2",
        side="buy",
        confidence=0.6,
        predicted_price=0.60,
        entry_price=0.60,
        size=30.0,
    )
    db.resolve_signal_outcomes("100", resolved_price=1.0)
    db.resolve_signal_outcomes("200", resolved_price=0.0)

    pnl = db.get_strategy_pnl()
    assert len(pnl) == 1
    assert pnl[0]["strategy"] == "ai_analyst"
    assert pnl[0]["total_trades"] == 2
    assert pnl[0]["gross_profit"] > 0
    assert pnl[0]["gross_loss"] > 0


def test_get_pending_outcomes_empty(db):
    """No pending outcomes returns empty dict."""
    assert db.get_pending_outcomes_by_market() == {}


def test_get_strategy_accuracy_min_samples(db):
    """Min samples filter works."""
    db.record_signal_outcome(
        strategy="ai_analyst",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=0.8,
        predicted_price=0.50,
        entry_price=0.50,
        size=25.0,
    )
    db.resolve_signal_outcomes("100", resolved_price=1.0)

    # 1 sample, but min_samples=5
    stats = db.get_strategy_accuracy(min_samples=5)
    assert len(stats) == 0

    # 1 sample, min_samples=1
    stats = db.get_strategy_accuracy(min_samples=1)
    assert len(stats) == 1
