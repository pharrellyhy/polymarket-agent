"""Orchestrator — main loop coordinating data, strategies, and execution."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polymarket_agent.config import AppConfig
from polymarket_agent.data.client import PolymarketData
from polymarket_agent.data.provider import DataProvider
from polymarket_agent.db import Database
from polymarket_agent.execution.base import Executor, Order, Portfolio
from polymarket_agent.execution.paper import PaperTrader
from polymarket_agent.monitoring.alerts import AlertManager, ConsoleAlertSink, WebhookAlertSink
from polymarket_agent.orders import ConditionalOrder, OrderStatus, OrderType
from polymarket_agent.position_sizing import PositionSizer
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

    def __init__(
        self,
        config: AppConfig,
        db_path: Path,
        *,
        data_provider: DataProvider | None = None,
    ) -> None:
        self._config = config
        self._data: DataProvider = data_provider if data_provider is not None else PolymarketData()
        self._db = Database(db_path)
        self._executor = self._build_executor(config, self._db)
        self._strategies = self._load_strategies(config.strategies)
        self._sizer = PositionSizer(
            method=config.position_sizing.method,
            kelly_fraction=config.position_sizing.kelly_fraction,
            max_bet_pct=config.position_sizing.max_bet_pct,
        )
        self._alerts = self._build_alert_manager(config)
        self._snapshot_interval = config.monitoring.snapshot_interval
        self._last_snapshot_at: datetime | None = None
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
        # Check conditional orders before regular strategy analysis
        conditional_trades = 0
        if self._config.conditional_orders.enabled and self._config.mode != "monitor":
            conditional_trades = self._check_conditional_orders()

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

        # Log signals to DB
        for signal in signals:
            self._record_signal(signal, status="generated")

        trades_executed = 0
        if self._config.mode != "monitor":
            risk_snapshot = self._build_risk_snapshot()
            for signal in signals:
                sized_signal = self._apply_position_sizing(signal)
                if self.place_order(sized_signal, risk_snapshot=risk_snapshot) is None:
                    self._record_signal(sized_signal, status="rejected")
                    continue
                trades_executed += 1
                self._record_signal(sized_signal, status="executed")
                self._alerts.alert(
                    f"Trade executed: {sized_signal.side} {sized_signal.size:.2f} USDC "
                    f"on {sized_signal.market_id} ({sized_signal.strategy})"
                )
                self._update_risk_snapshot_after_order(risk_snapshot, sized_signal)
                self._auto_create_conditional_orders(sized_signal)
        logger.info("Executed %d trades (mode=%s)", trades_executed, self._config.mode)

        # Record portfolio snapshot
        self._record_portfolio_snapshot()

        return {
            "markets_fetched": len(markets),
            "signals_generated": len(signals),
            "trades_executed": trades_executed + conditional_trades,
        }

    def get_portfolio(self) -> Portfolio:
        """Return the current portfolio state from the executor."""
        return self._executor.get_portfolio()

    def get_recent_trades(self, limit: int = 20) -> list[dict[str, object]]:
        """Return recent trades from the database."""
        return self._db.get_trades()[:limit]

    @property
    def data(self) -> DataProvider:
        """Public access to the data client."""
        return self._data

    @property
    def strategies(self) -> list[Strategy]:
        """Return the list of active strategy instances."""
        return self._strategies

    @property
    def db(self) -> Database:
        """Public access to the database."""
        return self._db

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
    # Conditional orders
    # ------------------------------------------------------------------

    def _check_conditional_orders(self) -> int:
        """Evaluate active conditional orders against current prices.

        Returns the number of orders triggered and executed.
        """
        orders = self._db.get_active_conditional_orders()
        triggered = 0
        for order in orders:
            try:
                spread = self._data.get_price(order.token_id)
            except RuntimeError:
                logger.warning("Cannot fetch price for conditional order %d (token %s)", order.id, order.token_id)
                continue

            bid = spread.bid
            if self._should_trigger(order, bid):
                signal = Signal(
                    strategy=order.parent_strategy,
                    market_id=order.market_id,
                    token_id=order.token_id,
                    side="sell",
                    confidence=1.0,
                    target_price=bid,
                    size=order.size,
                    reason=f"Conditional {order.order_type.value} triggered at {bid:.4f}",
                )
                result = self._executor.place_order(signal)
                if result is not None:
                    self._db.update_conditional_order(order.id, status=OrderStatus.TRIGGERED)
                    triggered += 1
                    logger.info("Triggered %s order %d at bid=%.4f", order.order_type.value, order.id, bid)
            elif order.order_type == OrderType.TRAILING_STOP:
                self._update_trailing_watermark(order, bid)

        return triggered

    @staticmethod
    def _should_trigger(order: ConditionalOrder, bid: float) -> bool:
        """Return True if the conditional order should be triggered at the given bid."""
        if order.order_type == OrderType.STOP_LOSS:
            return bid <= order.trigger_price
        if order.order_type == OrderType.TAKE_PROFIT:
            return bid >= order.trigger_price
        if order.order_type == OrderType.TRAILING_STOP:
            if order.high_watermark is None or order.trail_percent is None:
                return False
            threshold = order.high_watermark * (1.0 - order.trail_percent)
            return bid <= threshold
        return False

    def _update_trailing_watermark(self, order: ConditionalOrder, bid: float) -> None:
        """Update the high watermark if bid exceeds it."""
        if order.high_watermark is not None and bid > order.high_watermark:
            self._db.update_high_watermark(order.id, bid)

    def _auto_create_conditional_orders(self, signal: Signal) -> None:
        """Auto-create stop-loss/take-profit orders from signal hints or config defaults."""
        if not self._config.conditional_orders.enabled or signal.side != "buy":
            return

        cfg = self._config.conditional_orders

        stop_loss_price = signal.stop_loss
        if stop_loss_price is None:
            stop_loss_price = signal.target_price * (1.0 - cfg.default_stop_loss_pct)

        self._db.create_conditional_order(
            token_id=signal.token_id,
            market_id=signal.market_id,
            order_type=OrderType.STOP_LOSS,
            trigger_price=stop_loss_price,
            size=signal.size,
            parent_strategy=signal.strategy,
            reason=f"Auto stop-loss at {stop_loss_price:.4f}",
        )

        take_profit_price = signal.take_profit
        if take_profit_price is None:
            take_profit_price = signal.target_price * (1.0 + cfg.default_take_profit_pct)

        self._db.create_conditional_order(
            token_id=signal.token_id,
            market_id=signal.market_id,
            order_type=OrderType.TAKE_PROFIT,
            trigger_price=take_profit_price,
            size=signal.size,
            parent_strategy=signal.strategy,
            reason=f"Auto take-profit at {take_profit_price:.4f}",
        )

        if cfg.trailing_stop_enabled:
            self._db.create_conditional_order(
                token_id=signal.token_id,
                market_id=signal.market_id,
                order_type=OrderType.TRAILING_STOP,
                trigger_price=0.0,
                size=signal.size,
                high_watermark=signal.target_price,
                trail_percent=cfg.trailing_stop_pct,
                parent_strategy=signal.strategy,
                reason=f"Auto trailing stop ({cfg.trailing_stop_pct:.0%})",
            )

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def _apply_position_sizing(self, signal: Signal) -> Signal:
        """Return a new signal with size adjusted by the position sizer."""
        if self._config.position_sizing.method == "fixed":
            return signal
        portfolio = self.get_portfolio()
        new_size = self._sizer.compute_size(signal, portfolio)
        if new_size == signal.size:
            return signal
        return Signal(
            strategy=signal.strategy,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            confidence=signal.confidence,
            target_price=signal.target_price,
            size=new_size,
            reason=signal.reason,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )

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

        # Reject buy signals for tokens we already hold a position in
        if signal.side == "buy":
            positions = self._executor.get_portfolio().positions
            if signal.token_id in positions:
                return f"already holding position in {signal.token_id[:16]}..."

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

    @staticmethod
    def _build_alert_manager(config: AppConfig) -> AlertManager:
        """Create an AlertManager with sinks based on config."""
        manager = AlertManager()
        manager.register(ConsoleAlertSink())
        for url in config.monitoring.alert_webhooks:
            manager.register(WebhookAlertSink(url))
        return manager

    def _record_signal(self, signal: Signal, *, status: str) -> None:
        """Log a signal event to the DB (best effort)."""
        try:
            self._db.record_signal(
                strategy=signal.strategy,
                market_id=signal.market_id,
                token_id=signal.token_id,
                side=signal.side,
                confidence=signal.confidence,
                size=signal.size,
                status=status,
            )
        except Exception:
            logger.debug("Failed to record signal", exc_info=True)

    def _record_portfolio_snapshot(self) -> None:
        """Persist a portfolio snapshot to the DB, respecting snapshot_interval."""
        now = datetime.now(timezone.utc)
        if self._last_snapshot_at is not None:
            elapsed = (now - self._last_snapshot_at).total_seconds()
            if elapsed < self._snapshot_interval:
                return
        try:
            portfolio = self.get_portfolio()
            self._db.record_portfolio_snapshot(
                balance=portfolio.balance,
                total_value=portfolio.total_value,
                positions_json=json.dumps(portfolio.positions, default=str),
            )
            self._last_snapshot_at = now
        except Exception:
            logger.debug("Failed to record portfolio snapshot", exc_info=True)

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
