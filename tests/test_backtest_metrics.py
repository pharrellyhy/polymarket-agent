"""Tests for backtest performance metrics."""

import math

from polymarket_agent.backtest.metrics import BacktestMetrics, PortfolioSnapshot, compute_metrics


class TestComputeMetrics:
    def test_empty_inputs(self) -> None:
        metrics = compute_metrics([], [], 1000.0)
        assert metrics.total_return == 0.0
        assert metrics.total_trades == 0

    def test_total_return_positive(self) -> None:
        snapshots = [
            PortfolioSnapshot(timestamp="t0", balance=1000.0, total_value=1000.0),
            PortfolioSnapshot(timestamp="t1", balance=1100.0, total_value=1100.0),
        ]
        metrics = compute_metrics([], snapshots, 1000.0)
        assert abs(metrics.total_return - 0.10) < 1e-6

    def test_total_return_negative(self) -> None:
        snapshots = [
            PortfolioSnapshot(timestamp="t0", balance=1000.0, total_value=1000.0),
            PortfolioSnapshot(timestamp="t1", balance=800.0, total_value=800.0),
        ]
        metrics = compute_metrics([], snapshots, 1000.0)
        assert abs(metrics.total_return - (-0.20)) < 1e-6

    def test_max_drawdown(self) -> None:
        snapshots = [
            PortfolioSnapshot(timestamp="t0", balance=1000.0, total_value=1000.0),
            PortfolioSnapshot(timestamp="t1", balance=1200.0, total_value=1200.0),
            PortfolioSnapshot(timestamp="t2", balance=900.0, total_value=900.0),
            PortfolioSnapshot(timestamp="t3", balance=1100.0, total_value=1100.0),
        ]
        metrics = compute_metrics([], snapshots, 1000.0)
        # Peak was 1200, trough was 900 => drawdown = 300/1200 = 0.25
        assert abs(metrics.max_drawdown - 0.25) < 1e-6

    def test_max_drawdown_no_drawdown(self) -> None:
        snapshots = [
            PortfolioSnapshot(timestamp="t0", balance=1000.0, total_value=1000.0),
            PortfolioSnapshot(timestamp="t1", balance=1050.0, total_value=1050.0),
            PortfolioSnapshot(timestamp="t2", balance=1100.0, total_value=1100.0),
        ]
        metrics = compute_metrics([], snapshots, 1000.0)
        assert metrics.max_drawdown == 0.0

    def test_sharpe_ratio_positive(self) -> None:
        snapshots = [
            PortfolioSnapshot(timestamp=f"t{i}", balance=1000.0 + i * 10, total_value=1000.0 + i * 10)
            for i in range(10)
        ]
        metrics = compute_metrics([], snapshots, 1000.0)
        assert metrics.sharpe_ratio > 0

    def test_sharpe_ratio_flat(self) -> None:
        snapshots = [
            PortfolioSnapshot(timestamp=f"t{i}", balance=1000.0, total_value=1000.0) for i in range(5)
        ]
        metrics = compute_metrics([], snapshots, 1000.0)
        assert metrics.sharpe_ratio == 0.0

    def test_sharpe_ratio_single_snapshot(self) -> None:
        snapshots = [PortfolioSnapshot(timestamp="t0", balance=1000.0, total_value=1000.0)]
        metrics = compute_metrics([], snapshots, 1000.0)
        assert metrics.sharpe_ratio == 0.0

    def test_win_rate_and_profit_factor(self) -> None:
        trades = [
            {"side": "buy", "token_id": "0x1", "price": 0.50, "size": 50},
            {"side": "sell", "token_id": "0x1", "price": 0.60, "size": 60},  # win: +0.10
            {"side": "buy", "token_id": "0x2", "price": 0.70, "size": 70},
            {"side": "sell", "token_id": "0x2", "price": 0.65, "size": 65},  # loss: -0.05
        ]
        snapshots = [PortfolioSnapshot(timestamp="t0", balance=1000.0, total_value=1000.0)]
        metrics = compute_metrics(trades, snapshots, 1000.0)
        assert abs(metrics.win_rate - 0.50) < 1e-6
        assert abs(metrics.profit_factor - (0.10 / 0.05)) < 1e-6
        assert metrics.total_trades == 4

    def test_all_wins_infinite_profit_factor(self) -> None:
        trades = [
            {"side": "buy", "token_id": "0x1", "price": 0.40, "size": 40},
            {"side": "sell", "token_id": "0x1", "price": 0.60, "size": 60},
        ]
        snapshots = [PortfolioSnapshot(timestamp="t0", balance=1000.0, total_value=1000.0)]
        metrics = compute_metrics(trades, snapshots, 1000.0)
        assert metrics.win_rate == 1.0
        assert metrics.profit_factor == float("inf")

    def test_no_round_trips(self) -> None:
        trades = [
            {"side": "buy", "token_id": "0x1", "price": 0.50, "size": 50},
        ]
        snapshots = [PortfolioSnapshot(timestamp="t0", balance=1000.0, total_value=1000.0)]
        metrics = compute_metrics(trades, snapshots, 1000.0)
        assert metrics.win_rate == 0.0
        assert metrics.profit_factor == 0.0
        assert metrics.total_trades == 1

    def test_metrics_dataclass(self) -> None:
        m = BacktestMetrics(
            total_return=0.1, sharpe_ratio=1.5, max_drawdown=0.05,
            win_rate=0.6, profit_factor=2.0, total_trades=10,
        )
        assert m.total_return == 0.1
        assert m.total_trades == 10
