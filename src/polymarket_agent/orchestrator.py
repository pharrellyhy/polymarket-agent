"""Orchestrator — main loop coordinating data, strategies, and execution."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polymarket_agent.config import AppConfig
from polymarket_agent.data.client import PolymarketData
from polymarket_agent.data.gamma_client import GammaClient
from polymarket_agent.data.models import Market, categorize_market
from polymarket_agent.data.provider import DataProvider
from polymarket_agent.db import Database
from polymarket_agent.execution.base import Executor, Order, Portfolio
from polymarket_agent.execution.paper import PaperTrader
from polymarket_agent.monitoring.alerts import AlertManager, ConsoleAlertSink, WebhookAlertSink
from polymarket_agent.news.cached import CachedNewsProvider
from polymarket_agent.news.google_rss import GoogleRSSProvider
from polymarket_agent.news.provider import NewsProvider
from polymarket_agent.news.tavily_client import TavilyProvider
from polymarket_agent.orders import ConditionalOrder, OrderStatus, OrderType
from polymarket_agent.position_sizing import CalibrationTable, PositionSizer
from polymarket_agent.strategies.aggregator import aggregate_signals
from polymarket_agent.strategies.ai_analyst import AIAnalyst
from polymarket_agent.strategies.arbitrageur import Arbitrageur
from polymarket_agent.strategies.base import Signal, Strategy
from polymarket_agent.strategies.cross_platform_arb import CrossPlatformArb
from polymarket_agent.strategies.exit_manager import ExitManager
from polymarket_agent.strategies.market_maker import MarketMaker
from polymarket_agent.strategies.reflection import ReflectionEngine
from polymarket_agent.strategies.signal_trader import SignalTrader
from polymarket_agent.strategies.technical_analyst import TechnicalAnalyst
from polymarket_agent.strategies.whale_follower import WhaleFollower

logger = logging.getLogger(__name__)

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "signal_trader": SignalTrader,
    "market_maker": MarketMaker,
    "arbitrageur": Arbitrageur,
    "ai_analyst": AIAnalyst,
    "technical_analyst": TechnicalAnalyst,
    "whale_follower": WhaleFollower,
    "cross_platform_arb": CrossPlatformArb,
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
        self._news_provider: NewsProvider | None = self._build_news_provider(config)
        self._strategies = self._load_strategies(config.strategies)
        self._exit_manager = ExitManager(config.exit_manager)
        self._calibration = CalibrationTable()
        self._calibration.refresh(self._db)
        self._last_calibration_at: datetime = datetime.now(timezone.utc)
        self._sizer = PositionSizer(
            method=config.position_sizing.method,
            kelly_fraction=config.position_sizing.kelly_fraction,
            max_bet_pct=config.position_sizing.max_bet_pct,
            calibration=self._calibration,
        )
        self._alerts = self._build_alert_manager(config)
        self._snapshot_interval = config.monitoring.snapshot_interval
        self._last_snapshot_at: datetime | None = None
        self._last_signals: list[Signal] = []
        self._last_signals_updated_at: datetime | None = None
        self._exited_tokens: dict[str, datetime] = {}  # token_id -> exit time
        self._reflection_engine: ReflectionEngine | None = self._build_reflection_engine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tick(self) -> dict[str, Any]:
        """Run a single fetch-analyze-execute cycle.

        Returns a summary dict with ``markets_fetched``,
        ``signals_generated``, and ``trades_executed`` counts.
        """
        # Refresh calibration table hourly
        self._maybe_refresh_calibration()

        # Check for resolved markets and compute P&L attribution
        resolutions = self._check_market_resolutions()
        if resolutions > 0:
            logger.info("Resolved %d signal outcome(s) from closed markets", resolutions)

        # Check conditional orders before regular strategy analysis
        conditional_trades = 0
        if self._config.conditional_orders.enabled and self._config.mode != "monitor":
            conditional_trades = self._check_conditional_orders()

        markets = self._data.get_active_markets(limit=self._config.focus.fetch_limit)
        markets = self._apply_focus_filter(markets)
        logger.info("Fetched %d active markets", len(markets))

        # Mark-to-market: update position values with live prices
        self._mark_positions_to_market()

        raw_signals: list[Signal] = []
        for strategy in self._strategies:
            raw_signals.extend(strategy.analyze(markets, self._data))
        logger.info("Generated %d raw signals from %d strategies", len(raw_signals), len(self._strategies))

        strategy_weights = self._compute_strategy_weights() if self._config.aggregation.performance_weighted else None
        signals = aggregate_signals(
            raw_signals,
            min_confidence=self._config.aggregation.min_confidence,
            min_strategies=self._config.aggregation.min_strategies,
            conflict_resolution=self._config.aggregation.conflict_resolution,
            blend_confidence=self._config.aggregation.blend_confidence,
            strategy_weights=strategy_weights,
        )
        self._cache_signals(signals)
        logger.info("Aggregated to %d signals", len(signals))

        # Log signals to DB
        for signal in signals:
            self._record_signal(signal, status="generated")

        # Run exit manager on held positions
        exit_signals: list[Signal] = []
        if self._config.exit_manager.enabled and self._config.mode != "monitor":
            exit_signals = self._evaluate_exits()
            if exit_signals:
                logger.info("ExitManager generated %d sell signal(s)", len(exit_signals))

        trades_executed = 0
        if self._config.mode != "monitor":
            # Execute exit signals first (bypass risk gate and position sizing)
            for signal in exit_signals:
                order = self._executor.place_order(signal)
                if order is not None:
                    trades_executed += 1
                    self._record_signal(
                        signal, status="executed", fill_price=order.price, fill_size=order.size,
                    )
                    self._exited_tokens[signal.token_id] = datetime.now(timezone.utc)
                    self._alerts.alert(
                        f"Exit trade: {signal.side} {signal.size:.2f} USDC on {signal.market_id} ({signal.reason})"
                    )
                    self._db.cancel_conditional_orders_for_token(signal.token_id)
                else:
                    self._record_signal(signal, status="rejected")

            # Execute entry signals through normal risk/sizing pipeline
            risk_snapshot = self._build_risk_snapshot()
            for signal in signals:
                sized_signal = self._apply_position_sizing(signal)
                order = self.place_order(sized_signal, risk_snapshot=risk_snapshot)
                if order is None:
                    self._record_signal(sized_signal, status="rejected")
                    continue
                trades_executed += 1
                self._record_signal(
                    sized_signal, status="executed", fill_price=order.price, fill_size=order.size,
                )
                self._alerts.alert(
                    f"Trade executed: {sized_signal.side} {sized_signal.size:.2f} USDC "
                    f"on {sized_signal.market_id} ({sized_signal.strategy})"
                )
                self._update_risk_snapshot_after_order(risk_snapshot, sized_signal)
                self._auto_create_conditional_orders(sized_signal)
        logger.info("Executed %d trades (mode=%s)", trades_executed, self._config.mode)

        if trades_executed + conditional_trades > 0:
            self._force_portfolio_snapshot()
        else:
            # No state-changing trade this tick; keep periodic snapshot behavior.
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
    def poll_interval(self) -> int:
        """Return the current poll interval from config."""
        return self._config.poll_interval

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
        markets = self._data.get_active_markets(limit=self._config.focus.fetch_limit)
        markets = self._apply_focus_filter(markets)
        raw_signals: list[Signal] = []
        for strategy in self._strategies:
            try:
                raw_signals.extend(strategy.analyze(markets, self._data))
            except Exception:
                logger.exception("Strategy %s failed", getattr(strategy, "name", "unknown"))
        strategy_weights = self._compute_strategy_weights() if self._config.aggregation.performance_weighted else None
        signals = aggregate_signals(
            raw_signals,
            min_confidence=self._config.aggregation.min_confidence,
            min_strategies=self._config.aggregation.min_strategies,
            conflict_resolution=self._config.aggregation.conflict_resolution,
            blend_confidence=self._config.aggregation.blend_confidence,
            strategy_weights=strategy_weights,
        )
        self._cache_signals(signals)
        return signals

    def get_cached_signals(self) -> list[Signal]:
        """Return the latest cached aggregated signals (no recomputation)."""
        return list(self._last_signals)

    def get_cached_signals_updated_at(self) -> datetime | None:
        """Return when cached signals were last refreshed."""
        return self._last_signals_updated_at

    def reload_config(self, new_config: AppConfig) -> None:
        """Hot-reload configuration without rebuilding the executor.

        Safety: rejects mode changes (e.g. paper→live) to protect positions.
        Rebuilds strategies, position sizer, and alert manager from new config.
        The executor is preserved so in-memory positions remain intact.
        """
        if new_config.mode != self._config.mode:
            logger.warning(
                "[reload] Mode change rejected: %s → %s (restart required)",
                self._config.mode,
                new_config.mode,
            )
            return

        diff = self._compute_config_diff(self._config, new_config)
        if diff:
            self._db.record_config_change(
                changed_by="hot_reload",
                diff_json=json.dumps(diff),
                full_config_json=new_config.model_dump_json(),
            )

        self._config = new_config
        self._news_provider = self._build_news_provider(new_config)
        self._strategies = self._load_strategies(new_config.strategies)
        self._calibration.refresh(self._db)
        self._last_calibration_at = datetime.now(timezone.utc)
        self._sizer = PositionSizer(
            method=new_config.position_sizing.method,
            kelly_fraction=new_config.position_sizing.kelly_fraction,
            max_bet_pct=new_config.position_sizing.max_bet_pct,
            calibration=self._calibration,
        )
        self._reflection_engine = self._build_reflection_engine()
        self._alerts = self._build_alert_manager(new_config)
        self._exit_manager = ExitManager(new_config.exit_manager)
        self._snapshot_interval = new_config.monitoring.snapshot_interval
        logger.info("[reload] Config updated — %d strategies active", len(self._strategies))

    def close(self) -> None:
        """Release resources (database connection)."""
        self._db.close()

    # ------------------------------------------------------------------
    # Focus filter
    # ------------------------------------------------------------------

    def _apply_focus_filter(self, markets: list[Market]) -> list[Market]:
        """Filter markets using volume, category, trending, and focus config.

        Phases 1-3 (volume, category, trending) run unconditionally.
        Phase 4 (focus IDs/slugs/queries) only runs when focus is enabled.
        Phase 5 (max_brackets truncation) only runs for query-driven focus.
        """
        focus = self._config.focus

        # Phase 1: Volume filter — drop low-volume markets
        if focus.min_volume_24h > 0:
            before = len(markets)
            markets = [m for m in markets if m.volume_24h >= focus.min_volume_24h]
            logger.info("Volume filter: %d → %d markets", before, len(markets))

        # Phase 2: Category filter — exclude categories, sort preferred first
        excluded = {c.strip().lower() for c in focus.categories.excluded if c.strip()}
        preferred = [c.strip().lower() for c in focus.categories.preferred if c.strip()]
        if excluded:
            before = len(markets)
            markets = [m for m in markets if categorize_market(m) not in excluded]
            logger.info("Category exclude filter: %d → %d markets", before, len(markets))
        if preferred:
            preferred_set = set(preferred)
            markets.sort(key=lambda m: (0 if categorize_market(m) in preferred_set else 1))

        # Phase 3: Trending sort — sort by volume_24h descending
        if focus.prioritize_trending:
            markets.sort(key=lambda m: m.volume_24h, reverse=True)

        # Phase 4: Existing focus logic (IDs/slugs/queries)
        if not focus.enabled:
            return markets

        ids = {market_id.strip() for market_id in focus.market_ids if market_id.strip()}
        slugs = {slug.strip().lower() for slug in focus.market_slugs if slug.strip()}
        queries = [query.strip().lower() for query in focus.search_queries if query.strip()]

        if not ids and not slugs and not queries:
            return markets

        filtered: list[Market] = []
        for market in markets:
            question = market.question.lower()
            if market.id in ids or market.slug.lower() in slugs or any(query in question for query in queries):
                filtered.append(market)

        # Fallback: if CLI results had no matches, try the Gamma API directly
        if not filtered and queries:
            filtered = self._fetch_focus_markets_from_api(queries)

        # Phase 5: Limit to nearest N brackets (sorted by end_date).
        # Only apply to query-driven focus to avoid truncating explicit ID/slug lists.
        max_brackets = focus.max_brackets
        has_explicit_selectors = bool(ids or slugs)
        if not has_explicit_selectors and max_brackets > 0 and len(filtered) > max_brackets:
            filtered.sort(key=lambda m: m.end_date or "9999")
            filtered = filtered[:max_brackets]

        logger.info("Focus filter: %d → %d markets", len(markets), len(filtered))
        return filtered

    @staticmethod
    def _fetch_focus_markets_from_api(queries: list[str]) -> list[Market]:
        """Fetch markets from the Gamma API when the CLI doesn't return matches.

        Converts search queries into slugs and tries both event-slug and
        market-text matching via the Gamma API.
        """
        gamma = GammaClient(cache_ttl=60.0)
        all_markets: dict[str, Market] = {}

        for query in queries:
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
            for slug_variant in [slug, f"{slug}-by", f"{slug}-in"]:
                try:
                    events = gamma.search_events(slug_variant)
                    for event in events:
                        for item in event.get("markets", []):
                            try:
                                market = Market.from_cli(item)
                            except (KeyError, TypeError, ValueError):
                                continue
                            if market.active and not market.closed:
                                all_markets[market.id] = market
                    if all_markets:
                        break
                except Exception:
                    continue

            if not all_markets:
                logger.warning("Gamma API: no markets found for query %r", query)

        return list(all_markets.values())

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
                # Cancel stale orders whose position has already been closed.
                portfolio = self._executor.get_portfolio()
                if order.token_id not in portfolio.positions:
                    self._db.update_conditional_order(order.id, status=OrderStatus.CANCELLED)
                    logger.info(
                        "Cancelled stale %s order %d — no position for token %s",
                        order.order_type.value,
                        order.id,
                        order.token_id,
                    )
                    continue

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
                    if order.token_id not in self._executor.get_portfolio().positions:
                        self._db.cancel_conditional_orders_for_token(order.token_id)
                    self._exited_tokens[order.token_id] = datetime.now(timezone.utc)
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

    def _evaluate_exits(self) -> list[Signal]:
        """Fetch current prices for held positions and run the exit manager."""
        portfolio = self.get_portfolio()
        if not portfolio.positions:
            return []

        current_prices: dict[str, float] = {}
        for token_id in portfolio.positions:
            try:
                spread = self._data.get_price(token_id)
                current_prices[token_id] = spread.bid
            except Exception:
                logger.debug("Failed to fetch price for %s, skipping exit check", token_id)

        return self._exit_manager.evaluate(portfolio.positions, current_prices)

    def _compute_strategy_weights(self) -> dict[str, float] | None:
        """Compute performance weights from historical accuracy data.

        Returns a dict mapping strategy name → weight (win_rate with 0.3 floor).
        Requires at least 20 resolved samples per strategy.
        Returns None when insufficient data exists (equal weight fallback).
        """
        accuracy = self._db.get_strategy_accuracy(min_samples=20)
        if not accuracy:
            return None

        weights: dict[str, float] = {}
        for row in accuracy:
            strategy = str(row["strategy"])
            win_rate = float(str(row["win_rate"]))
            # Floor at 0.3 to prevent any strategy from being completely silenced
            weights[strategy] = max(win_rate, 0.3)

        return weights if weights else None

    def _build_reflection_engine(self) -> ReflectionEngine | None:
        """Build and wire a shared ReflectionEngine when reflection is enabled."""
        analysts = [s for s in self._strategies if isinstance(s, AIAnalyst) and s._reflection_enabled]
        if not analysts:
            return None

        for strategy in analysts:
            if strategy._client is not None:
                engine = ReflectionEngine(self._db, strategy._call_llm)
                for analyst in analysts:
                    analyst.set_reflection_engine(engine)
                return engine
        return None

    def _maybe_refresh_calibration(self) -> None:
        """Refresh the calibration table if more than 1 hour has elapsed."""
        elapsed = (datetime.now(timezone.utc) - self._last_calibration_at).total_seconds()
        if elapsed >= 3600:
            self._calibration.refresh(self._db)
            self._last_calibration_at = datetime.now(timezone.utc)
            logger.debug("Refreshed calibration table")

    def _mark_positions_to_market(self) -> None:
        """Fetch current prices for held positions and update portfolio values."""
        if not isinstance(self._executor, PaperTrader):
            return
        portfolio = self._executor.get_portfolio()
        if not portfolio.positions:
            return

        current_prices: dict[str, float] = {}
        for token_id in portfolio.positions:
            try:
                spread = self._data.get_price(token_id)
                current_prices[token_id] = spread.bid
            except Exception:
                logger.debug("Failed to fetch price for %s during mark-to-market", token_id)

        if current_prices:
            self._executor.mark_to_market(current_prices)

    def _check_market_resolutions(self) -> int:
        """Detect closed/resolved markets and compute P&L for pending outcomes.

        Queries pending signal outcomes, checks if their markets have resolved,
        and calls resolve_signal_outcomes with the final price.

        Returns the total number of outcomes resolved.
        """
        pending_by_market = self._db.get_pending_outcomes_by_market()
        if not pending_by_market:
            return 0

        total_resolved = 0
        for market_id in pending_by_market:
            try:
                market = self._data.get_market(market_id)
            except Exception:
                logger.debug("Cannot fetch market %s for resolution check", market_id)
                continue

            if market is None or not market.closed:
                continue

            # Determine resolved price from market outcome prices
            resolved_price = self._get_resolved_price(market)
            if resolved_price is None:
                continue

            count = self._db.resolve_signal_outcomes(market_id, resolved_price)
            if count > 0:
                logger.info(
                    "Market %s resolved at %.4f — resolved %d outcome(s)",
                    market_id,
                    resolved_price,
                    count,
                )
                total_resolved += count

                # Trigger reflections for resolved outcomes
                if self._reflection_engine is not None:
                    for outcome_row in pending_by_market[market_id]:
                        self._trigger_reflection(outcome_row, market, resolved_price)

        return total_resolved

    def _trigger_reflection(
        self,
        outcome_row: dict[str, object],
        market: Market,
        resolved_price: float,
    ) -> None:
        """Generate a reflection for a resolved signal outcome (best effort)."""
        if self._reflection_engine is None:
            return
        try:
            entry_price = float(str(outcome_row.get("entry_price", 0)))
            size = float(str(outcome_row.get("size", 0)))
            confidence = float(str(outcome_row.get("confidence", 0)))
            side = str(outcome_row.get("side", "buy"))

            if entry_price > 0:
                shares = size / entry_price
                pnl = (resolved_price - entry_price) * shares if side == "buy" else (entry_price - resolved_price) * shares
            else:
                pnl = 0.0

            self._reflection_engine.reflect_on_outcome(
                market_question=market.question,
                strategy=str(outcome_row.get("strategy", "unknown")),
                market_id=str(outcome_row.get("market_id", "")),
                side=side,
                confidence=confidence,
                predicted_price=float(str(outcome_row.get("predicted_price", 0))),
                actual_result=resolved_price,
                pnl=pnl,
                entry_reason=f"confidence={confidence:.2f}, entry={entry_price:.4f}",
            )
        except Exception:
            logger.debug("Failed to trigger reflection for outcome %s", outcome_row.get("id"), exc_info=True)

    @staticmethod
    def _get_resolved_price(market: Market) -> float | None:
        """Extract the final resolved price from a closed market.

        For closed markets, outcome_prices reflect the final resolution:
        [1.0, 0.0] for Yes, [0.0, 1.0] for No. Returns the Yes token price.
        Returns None if outcome prices are unavailable.
        """
        if market.outcome_prices:
            return market.outcome_prices[0]
        return None

    @staticmethod
    def _compute_config_diff(old: AppConfig, new: AppConfig) -> dict[str, dict[str, Any]]:
        """Recursively compare two configs, returning changed dotted paths."""
        old_dict = old.model_dump()
        new_dict = new.model_dump()
        diff: dict[str, dict[str, Any]] = {}

        def _walk(a: Any, b: Any, prefix: str) -> None:
            if isinstance(a, dict) and isinstance(b, dict):
                for key in a.keys() | b.keys():
                    _walk(a.get(key), b.get(key), f"{prefix}.{key}" if prefix else key)
            elif a != b:
                diff[prefix] = {"old": a, "new": b}

        _walk(old_dict, new_dict, "")
        return diff

    @staticmethod
    def _build_executor(config: AppConfig, db: Database) -> Executor:
        """Instantiate the right executor for the configured mode."""
        if config.mode == "live":
            from polymarket_agent.execution.live import LiveTrader  # noqa: PLC0415

            return LiveTrader.from_env(db=db)
        executor = PaperTrader(
            starting_balance=config.starting_balance,
            db=db,
            slippage_bps=config.paper_trading.slippage_bps,
        )
        executor.recover_from_db()
        return executor

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

            # Reject re-entry into recently exited tokens
            cooldown_hours = risk.reentry_cooldown_hours
            if cooldown_hours > 0 and signal.token_id in self._exited_tokens:
                exited_at = self._exited_tokens[signal.token_id]
                elapsed = (datetime.now(timezone.utc) - exited_at).total_seconds() / 3600
                if elapsed < cooldown_hours:
                    return f"reentry blocked: {signal.token_id[:16]}... exited {elapsed:.1f}h ago (cooldown {cooldown_hours}h)"

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

    def _record_signal(
        self,
        signal: Signal,
        *,
        status: str,
        fill_price: float | None = None,
        fill_size: float | None = None,
    ) -> None:
        """Log a signal event to the DB (best effort).

        When status is 'executed', also records a signal outcome for P&L tracking.
        Uses fill_price/fill_size from the actual execution when available (e.g.
        under slippage the fill price differs from signal.target_price).
        """
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

        if status == "executed":
            actual_price = fill_price if fill_price is not None else signal.target_price
            actual_size = fill_size if fill_size is not None else signal.size
            try:
                self._db.record_signal_outcome(
                    strategy=signal.strategy,
                    market_id=signal.market_id,
                    token_id=signal.token_id,
                    side=signal.side,
                    confidence=signal.confidence,
                    predicted_price=signal.target_price,
                    entry_price=actual_price,
                    size=actual_size,
                )
            except Exception:
                logger.debug("Failed to record signal outcome", exc_info=True)

    def _record_portfolio_snapshot(self) -> None:
        """Persist a portfolio snapshot to the DB, respecting snapshot_interval."""
        now = datetime.now(timezone.utc)
        if self._last_snapshot_at is not None:
            elapsed = (now - self._last_snapshot_at).total_seconds()
            if elapsed < self._snapshot_interval:
                return
        self._write_portfolio_snapshot()
        self._last_snapshot_at = datetime.now(timezone.utc)

    def _force_portfolio_snapshot(self) -> None:
        """Persist a portfolio snapshot immediately, bypassing interval throttle."""
        self._write_portfolio_snapshot()
        self._last_snapshot_at = datetime.now(timezone.utc)

    def _write_portfolio_snapshot(self) -> None:
        """Write the current portfolio state to the DB."""
        try:
            portfolio = self.get_portfolio()
            self._db.record_portfolio_snapshot(
                balance=portfolio.balance,
                total_value=portfolio.total_value,
                positions_json=json.dumps(portfolio.positions, default=str),
            )
        except Exception:
            logger.debug("Failed to record portfolio snapshot", exc_info=True)

    @staticmethod
    def _build_news_provider(config: AppConfig) -> NewsProvider | None:
        """Create a news provider based on config, or None if disabled."""
        news_cfg = config.news
        if not news_cfg.enabled:
            return None

        inner: NewsProvider
        if news_cfg.provider == "tavily":
            inner = TavilyProvider(api_key_env=news_cfg.api_key_env)
        else:
            inner = GoogleRSSProvider()

        return CachedNewsProvider(
            inner,
            cache_ttl=float(news_cfg.cache_ttl),
            max_calls_per_hour=news_cfg.max_calls_per_hour,
        )

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
            # Wire news provider into AI analyst
            if isinstance(instance, AIAnalyst) and self._news_provider is not None:
                instance.set_news_provider(self._news_provider, max_results=self._config.news.max_results)
            strategies.append(instance)
            logger.info("Loaded strategy: %s", name)
        return strategies
