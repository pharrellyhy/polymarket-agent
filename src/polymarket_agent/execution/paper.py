"""Paper trading executor for simulated order execution."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from polymarket_agent.db import Database, Trade
from polymarket_agent.execution.base import Executor, Order, Portfolio
from polymarket_agent.strategies.base import Signal

logger = logging.getLogger(__name__)


class PaperTrader(Executor):
    """Simulated executor that tracks a virtual USDC balance and positions."""

    def __init__(self, starting_balance: float, db: Database) -> None:
        self._balance = starting_balance
        self._db = db
        self._positions: dict[str, dict[str, Any]] = {}

    def recover_from_db(self) -> None:
        """Restore positions and balance from the latest DB snapshot.

        If no snapshot exists, the current starting balance is kept.
        """
        snapshot = self._db.get_latest_snapshot()
        if snapshot is None:
            logger.info("No existing snapshot found; starting fresh with balance=%.2f", self._balance)
            return

        self._balance = float(str(snapshot.get("balance", self._balance)))
        positions_raw = str(snapshot.get("positions_json", "{}"))
        try:
            positions = json.loads(str(positions_raw))
            if isinstance(positions, dict):
                self._positions = positions
                # Backfill metadata for positions recovered without it
                now_iso = datetime.now(timezone.utc).isoformat()
                for pos in self._positions.values():
                    if "opened_at" not in pos:
                        pos["opened_at"] = now_iso
                    if "entry_strategy" not in pos:
                        pos["entry_strategy"] = "unknown"
                logger.info(
                    "Recovered %d positions from DB snapshot (balance=%.2f)",
                    len(self._positions),
                    self._balance,
                )
            else:
                logger.warning("positions_json is not a dict; ignoring")
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse positions_json from snapshot; starting fresh")

    def place_order(self, signal: Signal) -> Order | None:
        """Place a simulated order based on a trade signal.

        Buy orders deduct cost from balance and add shares to positions.
        Sell orders remove shares from positions and add proceeds to balance.
        Returns None if the order cannot be filled (insufficient balance or no position).
        """
        if signal.side == "buy":
            return self._execute_buy(signal)
        if signal.side == "sell":
            return self._execute_sell(signal)
        logger.warning("Unsupported signal side %r; skipping order", signal.side)
        return None

    def get_portfolio(self) -> Portfolio:
        """Return the current portfolio state."""
        return Portfolio(
            balance=self._balance,
            positions=dict(self._positions),
        )

    def _execute_buy(self, signal: Signal) -> Order | None:
        """Execute a buy order. Returns None if insufficient balance."""
        cost = signal.size
        if cost > self._balance:
            logger.warning(
                "Insufficient balance for buy: need %.2f, have %.2f",
                cost,
                self._balance,
            )
            return None

        shares = signal.size / signal.target_price
        self._balance -= cost

        if signal.token_id in self._positions:
            pos = self._positions[signal.token_id]
            existing_shares = pos["shares"]
            existing_avg = pos["avg_price"]
            total_shares = existing_shares + shares
            pos["avg_price"] = (existing_shares * existing_avg + shares * signal.target_price) / total_shares
            pos["shares"] = total_shares
        else:
            self._positions[signal.token_id] = {
                "market_id": signal.market_id,
                "shares": shares,
                "avg_price": signal.target_price,
                "current_price": signal.target_price,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "entry_strategy": signal.strategy,
            }

        order = Order(
            market_id=signal.market_id,
            token_id=signal.token_id,
            side="buy",
            price=signal.target_price,
            size=signal.size,
            shares=shares,
        )

        self._log_trade(signal)
        logger.info("BUY %.2f shares of %s @ %.4f (cost: %.2f)", shares, signal.token_id, signal.target_price, cost)
        return order

    def _execute_sell(self, signal: Signal) -> Order | None:
        """Execute a sell order. Returns None if no position exists."""
        if signal.token_id not in self._positions:
            logger.warning("No position to sell for token %s", signal.token_id)
            return None

        pos = self._positions[signal.token_id]
        if signal.target_price <= 0:
            logger.warning("Cannot sell at zero/negative price for token %s", signal.token_id)
            return None
        shares_to_sell = signal.size / signal.target_price

        if shares_to_sell > pos["shares"]:
            # Sell all available shares instead of rejecting
            shares_to_sell = pos["shares"]

        proceeds = shares_to_sell * signal.target_price
        self._balance += proceeds
        pos["shares"] -= shares_to_sell

        if pos["shares"] <= 0:
            del self._positions[signal.token_id]
        else:
            pos["current_price"] = signal.target_price

        # Update signal size to reflect actual proceeds for DB logging
        signal = Signal(
            strategy=signal.strategy,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            confidence=signal.confidence,
            target_price=signal.target_price,
            size=proceeds,
            reason=signal.reason,
        )

        order = Order(
            market_id=signal.market_id,
            token_id=signal.token_id,
            side="sell",
            price=signal.target_price,
            size=proceeds,
            shares=shares_to_sell,
        )

        self._log_trade(signal)
        logger.info(
            "SELL %.2f shares of %s @ %.4f (proceeds: %.2f)",
            shares_to_sell,
            signal.token_id,
            signal.target_price,
            proceeds,
        )
        return order

    def _log_trade(self, signal: Signal) -> None:
        """Record a trade in the database."""
        trade = Trade(
            strategy=signal.strategy,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            price=signal.target_price,
            size=signal.size,
            reason=signal.reason,
        )
        self._db.record_trade(trade)
