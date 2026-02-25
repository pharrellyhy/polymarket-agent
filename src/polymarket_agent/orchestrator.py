"""Orchestrator — main loop coordinating data, strategies, and execution."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from polymarket_agent.config import AppConfig
from polymarket_agent.data.client import PolymarketData
from polymarket_agent.db import Database
from polymarket_agent.execution.base import Portfolio
from polymarket_agent.execution.paper import PaperTrader
from polymarket_agent.strategies.base import Signal, Strategy
from polymarket_agent.strategies.signal_trader import SignalTrader

logger = logging.getLogger(__name__)

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "signal_trader": SignalTrader,
}


class Orchestrator:
    """Coordinate the data-strategy-execution pipeline.

    Each call to :meth:`tick` fetches market data, runs all enabled
    strategies, and (unless in *monitor* mode) executes the resulting
    signals through the configured executor.
    """

    def __init__(self, config: AppConfig, db_path: Path) -> None:
        self._config = config
        self._data = PolymarketData()
        self._db = Database(db_path)
        self._executor = PaperTrader(starting_balance=config.starting_balance, db=self._db)
        self._strategies = self._load_strategies(config.strategies)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tick(self) -> dict[str, Any]:
        """Run a single fetch-analyze-execute cycle.

        Returns a summary dict with ``markets_fetched``,
        ``signals_generated``, and ``trades_executed`` counts.
        """
        markets = self._data.get_active_markets()
        logger.info("Fetched %d active markets", len(markets))

        signals: list[Signal] = []
        for strategy in self._strategies:
            signals.extend(strategy.analyze(markets, self._data))
        logger.info("Generated %d signals from %d strategies", len(signals), len(self._strategies))

        trades_executed = 0
        if self._config.mode != "monitor":
            trades_executed = sum(1 for s in signals if self._executor.place_order(s) is not None)
        logger.info("Executed %d trades (mode=%s)", trades_executed, self._config.mode)

        return {
            "markets_fetched": len(markets),
            "signals_generated": len(signals),
            "trades_executed": trades_executed,
        }

    def get_portfolio(self) -> Portfolio:
        """Return the current portfolio state from the executor."""
        return self._executor.get_portfolio()

    def get_recent_trades(self, limit: int = 20) -> list[dict[str, object]]:
        """Return recent trades from the database."""
        return self._db.get_trades()[:limit]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_strategies(self, strategy_configs: dict[str, dict[str, Any]]) -> list[Strategy]:
        """Instantiate and configure strategies listed in config."""
        strategies: list[Strategy] = []
        for name, params in strategy_configs.items():
            if not params.get("enabled", False):
                logger.debug("Strategy %s is disabled, skipping", name)
                continue
            cls = STRATEGY_REGISTRY.get(name)
            if cls is None:
                logger.warning("Unknown strategy %r, skipping", name)
                continue
            instance = cls()
            instance.configure(params)
            strategies.append(instance)
            logger.info("Loaded strategy: %s", name)
        return strategies
