"""Tests for the LiveTrader executor."""

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polymarket_agent.db import Database
from polymarket_agent.strategies.base import Signal


def _make_signal(
    market_id: str = "100",
    side: str = "buy",
    price: float = 0.5,
    size: float = 25.0,
) -> Signal:
    return Signal(
        strategy="test",
        market_id=market_id,
        token_id=f"0xtok_{market_id}",
        side=side,
        confidence=0.8,
        target_price=price,
        size=size,
        reason="test signal",
    )


def _mock_clob_client() -> MagicMock:
    client = MagicMock()
    client.create_or_derive_api_creds.return_value = MagicMock()
    client.create_order.return_value = {"signed": True}
    client.post_order.return_value = {"orderID": "0xabc123", "success": True}
    client.get_orders.return_value = []
    return client


def test_live_trader_requires_py_clob_client() -> None:
    """LiveTrader raises ImportError if py-clob-client is not installed."""
    with patch.dict("sys.modules", {"py_clob_client": None, "py_clob_client.client": None}):
        from polymarket_agent.execution.live import LiveTrader  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            with pytest.raises(ImportError, match="py-clob-client"):
                LiveTrader(private_key="0xtest", db=db)


def test_live_trader_from_env_requires_key() -> None:
    """from_env raises ValueError without POLYMARKET_PRIVATE_KEY."""
    from polymarket_agent.execution.live import LiveTrader  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="POLYMARKET_PRIVATE_KEY"):
                LiveTrader.from_env(db=db)


def _mock_clob_modules() -> dict[str, MagicMock]:
    """Create mock modules for py_clob_client imports."""
    mock_clob_types = MagicMock()
    mock_clob_types.OrderArgs = MagicMock()
    mock_clob_types.OrderType = MagicMock()
    mock_clob_types.OrderType.GTC = "GTC"
    mock_clob_types.OpenOrderParams = MagicMock(return_value=MagicMock())

    mock_constants = MagicMock()
    mock_constants.BUY = "BUY"
    mock_constants.SELL = "SELL"

    mock_order_builder = MagicMock()
    mock_order_builder.constants = mock_constants

    mock_py_clob = MagicMock()
    mock_py_clob.clob_types = mock_clob_types
    mock_py_clob.order_builder = mock_order_builder
    mock_py_clob.order_builder.constants = mock_constants

    return {
        "py_clob_client": mock_py_clob,
        "py_clob_client.client": MagicMock(),
        "py_clob_client.clob_types": mock_clob_types,
        "py_clob_client.order_builder": mock_order_builder,
        "py_clob_client.order_builder.constants": mock_constants,
    }


def _make_live_trader(db: "Database", mock_client: MagicMock | None = None) -> Any:
    """Create a LiveTrader instance bypassing __init__ (no py-clob-client needed)."""
    from polymarket_agent.execution.live import LiveTrader  # noqa: PLC0415

    trader = LiveTrader.__new__(LiveTrader)
    trader._client = mock_client or _mock_clob_client()
    trader._db = db
    return trader


def test_live_trader_place_order_success() -> None:
    """Successful order placement returns an Order and logs trade."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        trader = _make_live_trader(db)

        mock_modules = _mock_clob_modules()
        with patch.dict("sys.modules", mock_modules):
            signal = _make_signal(side="buy", price=0.5, size=25.0)
            order = trader.place_order(signal)

        assert order is not None
        assert order.market_id == "100"
        assert order.side == "buy"
        assert order.price == 0.5
        assert order.shares == 50.0
        mock_modules["py_clob_client.clob_types"].OrderArgs.assert_called_once()
        assert mock_modules["py_clob_client.clob_types"].OrderArgs.call_args.kwargs["size"] == 50.0
        assert len(db.get_trades()) == 1


def test_live_trader_rejects_non_positive_price() -> None:
    """Non-positive prices are rejected before API calls."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        mock_client = _mock_clob_client()
        trader = _make_live_trader(db, mock_client)

        order = trader.place_order(_make_signal(price=0.0))
        assert order is None
        mock_client.create_order.assert_not_called()
        assert len(db.get_trades()) == 0


def test_live_trader_place_order_api_failure() -> None:
    """API failures return None without crashing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        mock_client = _mock_clob_client()
        mock_client.post_order.side_effect = RuntimeError("API error")
        trader = _make_live_trader(db, mock_client)

        with patch.dict("sys.modules", _mock_clob_modules()):
            order = trader.place_order(_make_signal())
        assert order is None
        assert len(db.get_trades()) == 0


def test_live_trader_place_order_rejected() -> None:
    """Rejected orders (errorMsg) return None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        mock_client = _mock_clob_client()
        mock_client.post_order.return_value = {"errorMsg": "insufficient balance"}
        trader = _make_live_trader(db, mock_client)

        with patch.dict("sys.modules", _mock_clob_modules()):
            order = trader.place_order(_make_signal())
        assert order is None


def test_live_trader_cancel_order() -> None:
    """cancel_order delegates to ClobClient."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        mock_client = _mock_clob_client()
        trader = _make_live_trader(db, mock_client)

        assert trader.cancel_order("0xorder1") is True
        mock_client.cancel.assert_called_once_with("0xorder1")


def test_live_trader_cancel_order_failure() -> None:
    """cancel_order returns False on exception."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        mock_client = _mock_clob_client()
        mock_client.cancel.side_effect = RuntimeError("cancel failed")
        trader = _make_live_trader(db, mock_client)

        assert trader.cancel_order("0xorder1") is False


def test_live_trader_get_open_orders() -> None:
    """get_open_orders returns orders from ClobClient."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        mock_client = _mock_clob_client()
        mock_client.get_orders.return_value = [{"id": "0x1"}, {"id": "0x2"}]
        trader = _make_live_trader(db, mock_client)

        with patch.dict("sys.modules", _mock_clob_modules()):
            orders = trader.get_open_orders()
        assert len(orders) == 2
