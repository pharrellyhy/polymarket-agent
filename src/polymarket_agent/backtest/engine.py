"""Backtest engine — replays historical data through strategies and execution."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from polymarket_agent.backtest.historical import HistoricalDataProvider
from polymarket_agent.backtest.metrics import BacktestMetrics, PortfolioSnapshot, compute_metrics
from polymarket_agent.config import AppConfig
from polymarket_agent.db import Database
from polymarket_agent.execution.paper import PaperTrader
from polymarket_agent.strategies.aggregator import aggregate_signals
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Replays historical market data through strategies and paper execution.

    Args:
        config: Application configuration (used for strategy params, aggregation, risk).
        strategies: Pre-configured strategy instances to run.
        data_provider: Historical data provider loaded with CSV data.
    """

    def __init__(
        self,
        config: AppConfig,
        strategies: list[Strategy],
        data_provider: HistoricalDataProvider,
    ) -> None:
        self._config = config
        self._strategies = strategies
        self._provider = data_provider

    def run(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> "BacktestResult":
        """Execute the backtest across all timestamps in the data provider.

        Args:
            start: Include only timestamps >= this value (lexicographic).
            end: Include only timestamps <= this value (lexicographic).

        Returns a :class:`BacktestResult` with metrics, snapshots, and trade log.
        """
        timestamps = self._provider.unique_timestamps
        if start is not None:
            timestamps = [ts for ts in timestamps if ts >= start]
        if end is not None:
            timestamps = [ts for ts in timestamps if ts <= end]

        if not timestamps:
            logger.warning("No timestamps in data provider — nothing to backtest")
            return BacktestResult(
                metrics=BacktestMetrics(
                    total_return=0.0,
                    sharpe_ratio=0.0,
                    max_drawdown=0.0,
                    win_rate=0.0,
                    profit_factor=0.0,
                    total_trades=0,
                ),
                snapshots=[],
                trades=[],
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "backtest.db"
            db = Database(db_path)
            executor = PaperTrader(starting_balance=self._config.starting_balance, db=db)

            snapshots: list[PortfolioSnapshot] = []
            for ts in timestamps:
                self._provider.advance(ts)
                markets = self._provider.get_active_markets()

                raw_signals: list[Signal] = []
                for strategy in self._strategies:
                    try:
                        raw_signals.extend(strategy.analyze(markets, self._provider))
                    except Exception:
                        logger.exception("Strategy %s failed at %s", getattr(strategy, "name", "?"), ts)

                signals = aggregate_signals(
                    raw_signals,
                    min_confidence=self._config.aggregation.min_confidence,
                    min_strategies=self._config.aggregation.min_strategies,
                )

                for signal in signals:
                    executor.place_order(signal)

                portfolio = executor.get_portfolio()
                snapshots.append(
                    PortfolioSnapshot(
                        timestamp=ts,
                        balance=portfolio.balance,
                        total_value=portfolio.total_value,
                    )
                )

            trades = db.get_trades()
            metrics = compute_metrics(trades, snapshots, self._config.starting_balance)
            db.close()

        return BacktestResult(metrics=metrics, snapshots=snapshots, trades=trades)


class BacktestResult:
    """Container for backtest output."""

    def __init__(
        self,
        metrics: BacktestMetrics,
        snapshots: list[PortfolioSnapshot],
        trades: list[dict[str, object]],
    ) -> None:
        self.metrics = metrics
        self.snapshots = snapshots
        self.trades = trades

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result to a JSON-friendly dict."""
        return {
            "metrics": asdict(self.metrics),
            "snapshots": [asdict(s) for s in self.snapshots],
            "total_trades": len(self.trades),
        }
