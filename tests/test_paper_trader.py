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


def test_paper_trader_recovers_balance_and_positions_from_latest_snapshot() -> None:
    """recover_from_db() should restore balance and positions from the latest snapshot row."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.record_portfolio_snapshot(
            balance=876.5,
            total_value=912.5,
            positions_json='{"0xtok_100":{"market_id":"100","shares":72.0,"avg_price":0.5,"current_price":0.55}}',
        )
        paper = PaperTrader(starting_balance=1000.0, db=db)

        paper.recover_from_db()

        portfolio = paper.get_portfolio()
        assert portfolio.balance == 876.5
        assert "0xtok_100" in portfolio.positions
        assert portfolio.positions["0xtok_100"]["shares"] == 72.0


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
    """Selling more shares than held should sell all available shares."""
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=25.0))

    order = paper.place_order(_make_signal(side="sell", price=0.5, size=50.0))
    assert order is not None
    assert order.side == "sell"
    assert order.size == pytest.approx(25.0)
    assert order.shares == pytest.approx(50.0)
    portfolio = paper.get_portfolio()
    assert portfolio.balance == pytest.approx(1000.0)
    assert "0xtok_100" not in portfolio.positions


def test_paper_trader_sell_logs_trade(trader) -> None:
    """Sell trades are logged to the database."""
    paper, db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
    paper.place_order(_make_signal(side="sell", price=0.6, size=30.0))

    trades = db.get_trades()
    assert len(trades) == 2
    assert trades[0]["side"] == "buy"
    assert trades[1]["side"] == "sell"


def test_paper_trader_buy_sets_metadata(trader) -> None:
    """Buy orders should record opened_at and entry_strategy in position."""
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
    pos = paper.get_portfolio().positions["0xtok_100"]
    assert "opened_at" in pos
    assert pos["entry_strategy"] == "test"


def test_paper_trader_sell_writeoff_at_zero_price(trader) -> None:
    """Selling at price=0 writes off the position and logs cost basis."""
    paper, db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))

    order = paper.place_order(_make_signal(side="sell", price=0.0, size=0.0))
    assert order is not None
    assert order.shares == pytest.approx(100.0)
    assert order.price == 0.0

    portfolio = paper.get_portfolio()
    assert portfolio.balance == 950.0  # no proceeds added
    assert "0xtok_100" not in portfolio.positions

    trades = db.get_trades()
    assert len(trades) == 2
    assert "writeoff" in trades[1]["reason"]
    assert "cost_basis=50.00" in trades[1]["reason"]


def test_paper_trader_sell_negative_price_rejected(trader) -> None:
    """Selling at negative price is rejected (indicates upstream bug)."""
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))

    order = paper.place_order(_make_signal(side="sell", price=-0.1, size=25.0))
    assert order is None

    portfolio = paper.get_portfolio()
    assert "0xtok_100" in portfolio.positions  # position still open


def test_paper_trader_recover_sets_default_metadata() -> None:
    """Recovered positions without metadata get sensible defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.record_portfolio_snapshot(
            balance=900.0,
            total_value=950.0,
            positions_json='{"0xtok_100":{"market_id":"100","shares":50.0,"avg_price":0.5,"current_price":0.55}}',
        )
        paper = PaperTrader(starting_balance=1000.0, db=db)
        paper.recover_from_db()
        pos = paper.get_portfolio().positions["0xtok_100"]
        assert "opened_at" in pos
        assert pos["entry_strategy"] == "unknown"


# ------------------------------------------------------------------
# Slippage tests (Phase B)
# ------------------------------------------------------------------


def test_paper_trader_buy_with_slippage() -> None:
    """Buy with slippage fills at a higher price, yielding fewer shares."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        paper = PaperTrader(starting_balance=1000.0, db=db, slippage_bps=50)

        order = paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        assert order is not None

        # Fill price = 0.5 + 0.5 * 50/10000 = 0.5 + 0.0025 = 0.5025
        assert order.price == pytest.approx(0.5025)
        # Shares = 50.0 / 0.5025 < 100 (fewer shares due to slippage)
        assert order.shares < 100.0
        assert order.shares == pytest.approx(50.0 / 0.5025, rel=1e-4)

        portfolio = paper.get_portfolio()
        assert portfolio.balance == pytest.approx(950.0)


def test_paper_trader_logs_fill_price_with_slippage() -> None:
    """Trade log should persist the actual fill price under slippage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        paper = PaperTrader(starting_balance=1000.0, db=db, slippage_bps=50)

        order = paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        assert order is not None

        trades = db.get_trades()
        assert len(trades) == 1
        assert trades[0]["price"] == pytest.approx(order.price)


def test_paper_trader_sell_with_slippage() -> None:
    """Sell with slippage fills at a lower price, yielding less proceeds."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        paper = PaperTrader(starting_balance=1000.0, db=db, slippage_bps=50)

        paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        order = paper.place_order(_make_signal(side="sell", price=0.6, size=30.0))
        assert order is not None

        # Sell fill price = 0.6 - 0.6 * 50/10000 = 0.6 - 0.003 = 0.597
        assert order.price == pytest.approx(0.597)


def test_paper_trader_zero_slippage_unchanged() -> None:
    """With slippage_bps=0, behavior matches original."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        paper = PaperTrader(starting_balance=1000.0, db=db, slippage_bps=0)

        order = paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
        assert order is not None
        assert order.price == 0.5
        assert order.shares == 100.0


# ------------------------------------------------------------------
# Mark-to-market tests (Phase C)
# ------------------------------------------------------------------


def test_paper_trader_mark_to_market(trader) -> None:
    """mark_to_market updates current_price on held positions."""
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))

    portfolio_before = paper.get_portfolio()
    assert portfolio_before.total_value == pytest.approx(1000.0)  # 950 + 100 * 0.5

    # Price goes up
    paper.mark_to_market({"0xtok_100": 0.7})
    portfolio_after = paper.get_portfolio()
    # total_value = 950 + 100 * 0.7 = 1020
    assert portfolio_after.total_value == pytest.approx(1020.0)


def test_paper_trader_mark_to_market_price_drop(trader) -> None:
    """mark_to_market reflects unrealized losses."""
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))

    paper.mark_to_market({"0xtok_100": 0.3})
    portfolio = paper.get_portfolio()
    # total_value = 950 + 100 * 0.3 = 980
    assert portfolio.total_value == pytest.approx(980.0)
