"""Tests for the BacktestEngine."""

import csv
from pathlib import Path
from typing import Any

from polymarket_agent.backtest.engine import BacktestEngine, BacktestResult
from polymarket_agent.backtest.historical import HistoricalDataProvider
from polymarket_agent.config import AppConfig
from polymarket_agent.strategies.base import Signal, Strategy


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _sample_rows() -> list[dict[str, str]]:
    """Two markets over 3 days with prices moving to generate signals."""
    return [
        {"timestamp": "2024-01-01", "market_id": "100", "question": "Will X?", "yes_price": "0.30", "volume": "50000", "token_id": "0xtok1"},
        {"timestamp": "2024-01-01", "market_id": "200", "question": "Will Y?", "yes_price": "0.70", "volume": "30000", "token_id": "0xtok2"},
        {"timestamp": "2024-01-02", "market_id": "100", "question": "Will X?", "yes_price": "0.35", "volume": "55000", "token_id": "0xtok1"},
        {"timestamp": "2024-01-02", "market_id": "200", "question": "Will Y?", "yes_price": "0.65", "volume": "32000", "token_id": "0xtok2"},
        {"timestamp": "2024-01-03", "market_id": "100", "question": "Will X?", "yes_price": "0.40", "volume": "60000", "token_id": "0xtok1"},
        {"timestamp": "2024-01-03", "market_id": "200", "question": "Will Y?", "yes_price": "0.60", "volume": "35000", "token_id": "0xtok2"},
    ]


class _AlwaysBuyStrategy(Strategy):
    """Test strategy that always buys the first market."""

    name = "always_buy"

    def analyze(self, markets: list[Any], data: Any) -> list[Signal]:
        signals: list[Signal] = []
        for market in markets:
            if market.clob_token_ids:
                signals.append(
                    Signal(
                        strategy=self.name,
                        market_id=market.id,
                        token_id=market.clob_token_ids[0],
                        side="buy",
                        confidence=0.8,
                        target_price=market.outcome_prices[0] if market.outcome_prices else 0.5,
                        size=10.0,
                        reason="Test buy",
                    )
                )
        return signals


class _NoopStrategy(Strategy):
    """Test strategy that never generates signals."""

    name = "noop"

    def analyze(self, markets: list[Any], data: Any) -> list[Signal]:
        return []


class TestBacktestEngine:
    def test_run_with_no_data(self, tmp_path: Path) -> None:
        cfg = AppConfig(mode="paper")
        provider = HistoricalDataProvider(tmp_path)
        engine = BacktestEngine(config=cfg, strategies=[], data_provider=provider)
        result = engine.run()
        assert isinstance(result, BacktestResult)
        assert result.metrics.total_trades == 0
        assert result.metrics.total_return == 0.0

    def test_run_with_noop_strategy(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        cfg = AppConfig(mode="paper", starting_balance=1000.0)
        provider = HistoricalDataProvider(tmp_path)
        engine = BacktestEngine(config=cfg, strategies=[_NoopStrategy()], data_provider=provider)
        result = engine.run()
        assert result.metrics.total_trades == 0
        assert len(result.snapshots) == 3  # 3 unique timestamps
        # Balance should be unchanged
        assert result.snapshots[-1].balance == 1000.0

    def test_run_with_buying_strategy(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        cfg = AppConfig(mode="paper", starting_balance=1000.0)
        provider = HistoricalDataProvider(tmp_path)
        engine = BacktestEngine(config=cfg, strategies=[_AlwaysBuyStrategy()], data_provider=provider)
        result = engine.run()
        assert result.metrics.total_trades > 0
        assert len(result.snapshots) == 3
        # Balance should decrease due to buys
        assert result.snapshots[-1].balance < 1000.0

    def test_snapshots_track_value(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        cfg = AppConfig(mode="paper", starting_balance=500.0)
        provider = HistoricalDataProvider(tmp_path)
        engine = BacktestEngine(config=cfg, strategies=[_NoopStrategy()], data_provider=provider)
        result = engine.run()
        for snap in result.snapshots:
            assert snap.total_value == 500.0

    def test_to_dict(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        cfg = AppConfig(mode="paper")
        provider = HistoricalDataProvider(tmp_path)
        engine = BacktestEngine(config=cfg, strategies=[_NoopStrategy()], data_provider=provider)
        result = engine.run()
        d = result.to_dict()
        assert "metrics" in d
        assert "snapshots" in d
        assert "total_trades" in d
        assert d["metrics"]["total_trades"] == 0

    def test_multiple_strategies(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        cfg = AppConfig(mode="paper", starting_balance=1000.0)
        provider = HistoricalDataProvider(tmp_path)
        strategies = [_AlwaysBuyStrategy(), _NoopStrategy()]
        engine = BacktestEngine(config=cfg, strategies=strategies, data_provider=provider)
        result = engine.run()
        assert result.metrics.total_trades > 0

    def test_aggregation_config_respected(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        # Set min_confidence higher than the strategy's 0.8
        cfg = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            aggregation={"min_confidence": 0.9, "min_strategies": 1},
        )
        provider = HistoricalDataProvider(tmp_path)
        engine = BacktestEngine(config=cfg, strategies=[_AlwaysBuyStrategy()], data_provider=provider)
        result = engine.run()
        # With min_confidence=0.9, the 0.8 confidence signals should be filtered out
        assert result.metrics.total_trades == 0

    def test_start_end_filters_timestamps(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        cfg = AppConfig(mode="paper", starting_balance=1000.0)
        provider = HistoricalDataProvider(tmp_path)
        engine = BacktestEngine(config=cfg, strategies=[_NoopStrategy()], data_provider=provider)
        # Only include day 2
        result = engine.run(start="2024-01-02", end="2024-01-02")
        assert len(result.snapshots) == 1
        assert result.snapshots[0].timestamp == "2024-01-02"

    def test_start_filter_only(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        cfg = AppConfig(mode="paper", starting_balance=1000.0)
        provider = HistoricalDataProvider(tmp_path)
        engine = BacktestEngine(config=cfg, strategies=[_NoopStrategy()], data_provider=provider)
        result = engine.run(start="2024-01-02")
        assert len(result.snapshots) == 2  # day 2 and day 3

    def test_end_filter_only(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "data.csv", _sample_rows())
        cfg = AppConfig(mode="paper", starting_balance=1000.0)
        provider = HistoricalDataProvider(tmp_path)
        engine = BacktestEngine(config=cfg, strategies=[_NoopStrategy()], data_provider=provider)
        result = engine.run(end="2024-01-01")
        assert len(result.snapshots) == 1
        assert result.snapshots[0].timestamp == "2024-01-01"
