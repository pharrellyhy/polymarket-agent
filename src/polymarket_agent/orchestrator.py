"""Orchestrator — main loop coordinating data, strategies, and execution."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polymarket_agent.config import AppConfig
from polymarket_agent.data.client import PolymarketData
from polymarket_agent.db import Database
from polymarket_agent.execution.base import Executor, Order, Portfolio
from polymarket_agent.execution.paper import PaperTrader
from polymarket_agent.strategies.aggregator import aggregate_signals
from polymarket_agent.strategies.ai_analyst import AIAnalyst
from polymarket_agent.strategies.arbitrageur import Arbitrageur
from polymarket_agent.strategies.base import Signal, Strategy
from polymarket_agent.strategies.market_maker import MarketMaker
from polymarket_agent.strategies.signal_trader import SignalTrader

logger = logging.getLogger(__name__)

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "signal_trader": SignalTrader,
    "market_maker": MarketMaker,
    "arbitrageur": Arbitrageur,
    "ai_analyst": AIAnalyst,
}


@dataclass
class _RiskSnapshot:
    """Precomputed risk inputs reused across a single tick execution."""

    daily_loss: float
    open_orders: int


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
        self._executor = self._build_executor(config, self._db)
        self._strategies = self._load_strategies(config.strategies)
        self._last_signals: list[Signal] = []
        self._last_signals_updated_at: datetime | None = None

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

        raw_signals: list[Signal] = []
        for strategy in self._strategies:
            raw_signals.extend(strategy.analyze(markets, self._data))
        logger.info("Generated %d raw signals from %d strategies", len(raw_signals), len(self._strategies))

        signals = aggregate_signals(
            raw_signals,
            min_confidence=self._config.aggregation.min_confidence,
            min_strategies=self._config.aggregation.min_strategies,
        )
        self._cache_signals(signals)
        logger.info("Aggregated to %d signals", len(signals))

        trades_executed = 0
        if self._config.mode != "monitor":
            risk_snapshot = self._build_risk_snapshot()
            for signal in signals:
                if self.place_order(signal, risk_snapshot=risk_snapshot) is None:
                    continue
                trades_executed += 1
                self._update_risk_snapshot_after_order(risk_snapshot, signal)
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

    @property
    def data(self) -> PolymarketData:
        """Public access to the data client."""
        return self._data

    @property
    def strategies(self) -> list[Strategy]:
        """Return the list of active strategy instances."""
        return self._strategies

    def place_order(self, signal: Signal, *, risk_snapshot: _RiskSnapshot | None = None) -> Order | None:
        """Place an order through the executor after mode/risk checks."""
        if self._config.mode == "monitor":
            logger.info("Monitor mode: skipping order for market %s", signal.market_id)
            return None
        rejection = self._check_risk(signal, risk_snapshot=risk_snapshot)
        if rejection:
            logger.info("Risk gate rejected signal: %s", rejection)
            return None
        return self._executor.place_order(signal)

    def generate_signals(self) -> list[Signal]:
        """Run all strategies and return aggregated signals without executing."""
        markets = self._data.get_active_markets()
        raw_signals: list[Signal] = []
        for strategy in self._strategies:
            try:
                raw_signals.extend(strategy.analyze(markets, self._data))
            except Exception:
                logger.exception("Strategy %s failed", getattr(strategy, "name", "unknown"))
        signals = aggregate_signals(
            raw_signals,
            min_confidence=self._config.aggregation.min_confidence,
            min_strategies=self._config.aggregation.min_strategies,
        )
        self._cache_signals(signals)
        return signals

    def get_cached_signals(self) -> list[Signal]:
        """Return the latest cached aggregated signals (no recomputation)."""
        return list(self._last_signals)

    def get_cached_signals_updated_at(self) -> datetime | None:
        """Return when cached signals were last refreshed."""
        return self._last_signals_updated_at

    def close(self) -> None:
        """Release resources (database connection)."""
        self._db.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_executor(config: AppConfig, db: Database) -> Executor:
        """Instantiate the right executor for the configured mode."""
        if config.mode == "live":
            from polymarket_agent.execution.live import LiveTrader  # noqa: PLC0415

            return LiveTrader.from_env(db=db)
        return PaperTrader(starting_balance=config.starting_balance, db=db)

    def _build_risk_snapshot(self) -> _RiskSnapshot:
        """Collect current risk inputs once for reuse across a tick."""
        return _RiskSnapshot(
            daily_loss=self._calculate_daily_loss(),
            open_orders=len(self._executor.get_open_orders()),
        )

    def _update_risk_snapshot_after_order(self, snapshot: _RiskSnapshot, signal: Signal) -> None:
        """Advance a reused risk snapshot after an accepted order."""
        if signal.side == "buy":
            snapshot.daily_loss += signal.size
        else:
            snapshot.daily_loss = max(snapshot.daily_loss - signal.size, 0.0)

        # Live GTC orders may remain open; paper orders fill immediately and report no open orders.
        if self._config.mode == "live":
            snapshot.open_orders += 1

    def _check_risk(self, signal: Signal, *, risk_snapshot: _RiskSnapshot | None = None) -> str | None:
        """Return a rejection reason if the signal violates risk limits, else None."""
        risk = self._config.risk

        if signal.size > risk.max_position_size:
            return f"size {signal.size} exceeds max_position_size {risk.max_position_size}"

        snapshot = risk_snapshot if risk_snapshot is not None else self._build_risk_snapshot()
        daily_loss = snapshot.daily_loss
        if daily_loss >= risk.max_daily_loss:
            return f"daily_loss {daily_loss:.2f} >= max_daily_loss {risk.max_daily_loss}"

        open_count = snapshot.open_orders
        if open_count >= risk.max_open_orders:
            return f"open_orders {open_count} >= max_open_orders {risk.max_open_orders}"

        return None

    def _calculate_daily_loss(self) -> float:
        """Sum of losses from today's trades (UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trades = self._db.get_trades()
        loss = 0.0
        for t in trades:
            ts = str(t.get("timestamp", ""))
            if not ts.startswith(today):
                continue
            size = float(str(t.get("size", 0)))
            if t.get("side") == "buy":
                loss += size
            else:
                loss -= size
        return max(loss, 0.0)

    def _cache_signals(self, signals: list[Signal]) -> None:
        """Store the latest aggregated signal snapshot for read-only consumers."""
        self._last_signals = list(signals)
        self._last_signals_updated_at = datetime.now(timezone.utc)

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
