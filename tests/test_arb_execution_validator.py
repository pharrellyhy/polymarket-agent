"""Tests for the execution validator module."""

from unittest.mock import MagicMock

import pytest

from polymarket_agent.data.models import OrderBook, OrderBookLevel
from polymarket_agent.strategies.arb.execution_validator import (
    estimate_slippage,
    estimate_vwap,
    validate_execution,
)


def _make_orderbook(
    asks: list[tuple[float, float]] | None = None,
    bids: list[tuple[float, float]] | None = None,
) -> OrderBook:
    return OrderBook(
        asks=[OrderBookLevel(price=p, size=s) for p, s in (asks or [])],
        bids=[OrderBookLevel(price=p, size=s) for p, s in (bids or [])],
    )


class TestEstimateVwap:
    def test_single_level_sufficient(self) -> None:
        book = _make_orderbook(asks=[(0.50, 100.0)])
        vwap, available = estimate_vwap(book, 25.0, "buy")
        assert abs(vwap - 0.50) < 1e-6
        assert abs(available - 25.0) < 1e-6

    def test_multiple_levels(self) -> None:
        book = _make_orderbook(asks=[(0.50, 20.0), (0.55, 20.0)])
        # Buy $5: fits in first level (0.50 * 20 = $10 available)
        vwap, available = estimate_vwap(book, 5.0, "buy")
        assert abs(vwap - 0.50) < 1e-6
        assert abs(available - 5.0) < 1e-6

    def test_insufficient_depth(self) -> None:
        book = _make_orderbook(asks=[(0.50, 10.0)])  # only $5 available (0.50 * 10)
        vwap, available = estimate_vwap(book, 25.0, "buy")
        assert available < 25.0

    def test_sell_side(self) -> None:
        book = _make_orderbook(bids=[(0.60, 100.0), (0.55, 100.0)])
        vwap, available = estimate_vwap(book, 30.0, "sell")
        # Should use highest bid first
        assert abs(vwap - 0.60) < 1e-6

    def test_empty_book(self) -> None:
        book = _make_orderbook()
        vwap, available = estimate_vwap(book, 10.0, "buy")
        assert vwap == 0.0
        assert available == 0.0


class TestEstimateSlippage:
    def test_no_slippage(self) -> None:
        assert estimate_slippage(0.50, 0.50) == 0.0

    def test_positive_slippage(self) -> None:
        slippage = estimate_slippage(0.52, 0.50)
        assert abs(slippage - 0.04) < 1e-6

    def test_zero_mid(self) -> None:
        assert estimate_slippage(0.50, 0.0) == 0.0


class TestValidateExecution:
    def test_valid_execution(self) -> None:
        book = _make_orderbook(
            asks=[(0.50, 200.0)],
            bids=[(0.49, 200.0)],
        )
        data = MagicMock()
        data.get_orderbook.return_value = book

        valid, reason = validate_execution(
            data, "tok1", 25.0, "buy",
            expected_profit=1.0,
            min_profit=0.05,
            max_slippage=0.05,
        )
        assert valid is True
        assert reason.startswith("ok:")

    def test_insufficient_depth_fails(self) -> None:
        book = _make_orderbook(
            asks=[(0.50, 5.0)],  # only $2.50 available
            bids=[(0.49, 5.0)],
        )
        data = MagicMock()
        data.get_orderbook.return_value = book

        valid, reason = validate_execution(
            data, "tok1", 25.0, "buy",
            expected_profit=1.0,
        )
        assert valid is False
        assert "insufficient_depth" in reason

    def test_high_slippage_fails(self) -> None:
        book = _make_orderbook(
            asks=[(0.50, 10.0), (0.70, 100.0)],
            bids=[(0.49, 100.0)],
        )
        data = MagicMock()
        data.get_orderbook.return_value = book

        valid, reason = validate_execution(
            data, "tok1", 50.0, "buy",
            expected_profit=1.0,
            max_slippage=0.01,
        )
        assert valid is False
        assert "slippage" in reason

    def test_orderbook_unavailable(self) -> None:
        data = MagicMock()
        data.get_orderbook.side_effect = Exception("network error")

        valid, reason = validate_execution(
            data, "tok1", 25.0, "buy",
            expected_profit=1.0,
        )
        assert valid is False
        assert reason == "orderbook_unavailable"

    def test_no_liquidity(self) -> None:
        book = _make_orderbook()  # empty book
        data = MagicMock()
        data.get_orderbook.return_value = book

        valid, reason = validate_execution(
            data, "tok1", 25.0, "buy",
            expected_profit=1.0,
        )
        assert valid is False
        assert reason == "no_liquidity"
