"""Paper trading executor for simulated order execution."""

import logging
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
            logger.warning(
                "Insufficient shares for sell: need %.2f, have %.2f",
                shares_to_sell,
                pos["shares"],
            )
            return None

        proceeds = signal.size
        self._balance += proceeds
        pos["shares"] -= shares_to_sell

        if pos["shares"] <= 0:
            del self._positions[signal.token_id]
        else:
            pos["current_price"] = signal.target_price

        order = Order(
            market_id=signal.market_id,
            token_id=signal.token_id,
            side="sell",
            price=signal.target_price,
            size=signal.size,
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
