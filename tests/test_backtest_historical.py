"""Tests for the HistoricalDataProvider."""

import csv
from pathlib import Path

import pytest
from polymarket_agent.backtest.historical import HistoricalDataProvider
from polymarket_agent.data.models import Market, OrderBook, Spread


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a list of dicts to a CSV file."""
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _sample_rows() -> list[dict[str, str]]:
    return [
        {
            "timestamp": "2024-01-01T00:00:00Z",
            "market_id": "100",
            "question": "Will it rain?",
            "yes_price": "0.60",
            "volume": "50000",
            "token_id": "0xtok1",
        },
        {
            "timestamp": "2024-01-01T00:00:00Z",
            "market_id": "200",
            "question": "Will it snow?",
            "yes_price": "0.30",
            "volume": "25000",
            "token_id": "0xtok2",
        },
        {
            "timestamp": "2024-01-02T00:00:00Z",
            "market_id": "100",
            "question": "Will it rain?",
            "yes_price": "0.65",
            "volume": "55000",
            "token_id": "0xtok1",
        },
        {
            "timestamp": "2024-01-02T00:00:00Z",
            "market_id": "200",
            "question": "Will it snow?",
            "yes_price": "0.25",
            "volume": "20000",
            "token_id": "0xtok2",
        },
        {
            "timestamp": "2024-01-03T00:00:00Z",
            "market_id": "100",
            "question": "Will it rain?",
            "yes_price": "0.70",
            "volume": "60000",
            "token_id": "0xtok1",
        },
    ]


class TestHistoricalDataProvider:
    def test_load_csv(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path)
        assert provider.total_steps == 5

    def test_empty_dir(self, tmp_path: Path) -> None:
        provider = HistoricalDataProvider(tmp_path)
        assert provider.total_steps == 0
        assert provider.get_active_markets() == []

    def test_unique_timestamps(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path)
        timestamps = provider.unique_timestamps
        assert len(timestamps) == 3
        assert timestamps[0] == "2024-01-01T00:00:00Z"
        assert timestamps[2] == "2024-01-03T00:00:00Z"

    def test_get_active_markets_at_cursor(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path)
        # Cursor starts at 0 (first row)
        markets = provider.get_active_markets()
        assert len(markets) >= 1
        assert all(isinstance(m, Market) for m in markets)

    def test_advance_and_get_markets(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path)
        provider.advance("2024-01-02T00:00:00Z")
        markets = provider.get_active_markets()
        assert len(markets) == 2  # Two markets at day 2

    def test_get_orderbook(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path)
        book = provider.get_orderbook("0xtok1")
        assert isinstance(book, OrderBook)
        assert book.best_bid > 0
        assert book.best_ask > book.best_bid

    def test_get_price(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path)
        spread = provider.get_price("0xtok1")
        assert isinstance(spread, Spread)
        assert spread.bid > 0
        assert spread.ask > spread.bid

    def test_get_price_unknown_token_raises(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path)
        with pytest.raises(RuntimeError, match="No price data"):
            provider.get_price("0xunknown")

    def test_get_price_history(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path)
        provider.advance("2024-01-03T00:00:00Z")
        history = provider.get_price_history("0xtok1")
        assert len(history) == 3  # Three entries for tok1

    def test_default_spread_param(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path, default_spread=0.10)
        spread = provider.get_price("0xtok1")
        assert abs(spread.spread - 0.10) < 0.01

    def test_market_has_clob_token_ids(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        provider = HistoricalDataProvider(tmp_path)
        markets = provider.get_active_markets()
        assert len(markets[0].clob_token_ids) == 1

    def test_malformed_rows_skipped(self, tmp_path: Path) -> None:
        rows = [
            {"timestamp": "2024-01-01", "market_id": "100", "question": "Q?", "yes_price": "bad", "volume": "100", "token_id": "0x1"},
            {"timestamp": "2024-01-01", "market_id": "101", "question": "Q2?", "yes_price": "0.5", "volume": "200", "token_id": "0x2"},
        ]
        _write_csv(tmp_path / "data.csv", rows)
        provider = HistoricalDataProvider(tmp_path)
        assert provider.total_steps == 1

    def test_multiple_csv_files(self, tmp_path: Path) -> None:
        rows1 = [_sample_rows()[0]]
        rows2 = [_sample_rows()[2]]
        _write_csv(tmp_path / "a.csv", rows1)
        _write_csv(tmp_path / "b.csv", rows2)
        provider = HistoricalDataProvider(tmp_path)
        assert provider.total_steps == 2
