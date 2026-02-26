"""Tests for paper trading executor."""

import tempfile
from pathlib import Path

import pytest
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


@pytest.fixture
def trader():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        yield PaperTrader(starting_balance=1000.0, db=db), db


def test_paper_trader_initial_balance(trader) -> None:
    paper, _db = trader
    portfolio = paper.get_portfolio()
    assert portfolio.balance == 1000.0
    assert portfolio.positions == {}
    assert portfolio.total_value == 1000.0


def test_paper_trader_buy(trader) -> None:
    paper, _db = trader
    order = paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
    assert order is not None
    portfolio = paper.get_portfolio()
    assert portfolio.balance == 950.0
    assert "0xtok_100" in portfolio.positions


def test_paper_trader_insufficient_balance() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        paper = PaperTrader(starting_balance=10.0, db=db)
        order = paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        assert order is None


def test_paper_trader_logs_trades(trader) -> None:
    paper, db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
    trades = db.get_trades()
    assert len(trades) == 1
    assert trades[0]["side"] == "buy"


def test_paper_trader_total_value_respects_zero_current_price(trader) -> None:
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
    paper._positions["0xtok_100"]["current_price"] = 0.0

    portfolio = paper.get_portfolio()
    assert portfolio.balance == 950.0
    assert portfolio.total_value == 950.0


def test_paper_trader_invalid_side_does_not_execute_sell(trader) -> None:
    paper, db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))

    order = paper.place_order(_make_signal(side="hold", price=0.5, size=25.0))

    assert order is None
    portfolio = paper.get_portfolio()
    assert portfolio.balance == 950.0
    assert portfolio.positions["0xtok_100"]["shares"] == 100.0
    assert len(db.get_trades()) == 1


def test_paper_trader_sell_reduces_position(trader) -> None:
    """Selling part of a position reduces shares and increases balance."""
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))

    order = paper.place_order(_make_signal(side="sell", price=0.6, size=30.0))
    assert order is not None
    assert order.side == "sell"

    portfolio = paper.get_portfolio()
    assert portfolio.balance == pytest.approx(980.0)  # 950 + 30
    assert portfolio.positions["0xtok_100"]["shares"] == pytest.approx(50.0)  # 100 - 50


def test_paper_trader_sell_closes_position(trader) -> None:
    """Selling the full position removes it entirely."""
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))

    order = paper.place_order(_make_signal(side="sell", price=0.5, size=50.0))
    assert order is not None

    portfolio = paper.get_portfolio()
    assert portfolio.balance == 1000.0
    assert "0xtok_100" not in portfolio.positions


def test_paper_trader_sell_no_position(trader) -> None:
    """Selling with no position returns None."""
    paper, _db = trader
    order = paper.place_order(_make_signal(side="sell", price=0.5, size=25.0))
    assert order is None
    assert paper.get_portfolio().balance == 1000.0


def test_paper_trader_sell_insufficient_shares(trader) -> None:
    """Selling more shares than held returns None."""
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=25.0))

    order = paper.place_order(_make_signal(side="sell", price=0.5, size=50.0))
    assert order is None
    assert paper.get_portfolio().balance == 975.0


def test_paper_trader_sell_logs_trade(trader) -> None:
    """Sell trades are logged to the database."""
    paper, db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
    paper.place_order(_make_signal(side="sell", price=0.6, size=30.0))

    trades = db.get_trades()
    assert len(trades) == 2
    assert trades[0]["side"] == "buy"
    assert trades[1]["side"] == "sell"
