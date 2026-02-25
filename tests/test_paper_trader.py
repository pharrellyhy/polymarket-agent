"""Tests for paper trading executor."""

import tempfile
from pathlib import Path

from polymarket_agent.db import Database
from polymarket_agent.execution.paper import PaperTrader
from polymarket_agent.strategies.base import Signal


def _make_signal(market_id: str = "100", side: str = "buy", price: float = 0.5, size: float = 25.0) -> Signal:
    return Signal(
        strategy="test",
        market_id=market_id,
        token_id=f"0xtok_{market_id}",
        side=side,
        confidence=0.8,
        target_price=price,
        size=size,
        reason="test",
    )


def test_paper_trader_initial_balance() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        trader = PaperTrader(starting_balance=1000.0, db=db)
        portfolio = trader.get_portfolio()
        assert portfolio.balance == 1000.0
        assert portfolio.positions == {}
        assert portfolio.total_value == 1000.0


def test_paper_trader_buy() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        trader = PaperTrader(starting_balance=1000.0, db=db)
        order = trader.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        assert order is not None
        portfolio = trader.get_portfolio()
        assert portfolio.balance == 950.0
        assert "0xtok_100" in portfolio.positions


def test_paper_trader_insufficient_balance() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        trader = PaperTrader(starting_balance=10.0, db=db)
        order = trader.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        assert order is None


def test_paper_trader_logs_trades() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        trader = PaperTrader(starting_balance=1000.0, db=db)
        trader.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        trades = db.get_trades()
        assert len(trades) == 1
        assert trades[0]["side"] == "buy"
