"""LiveTrader — executes real orders on Polymarket via py-clob-client."""

import logging
import os
from typing import Any

from polymarket_agent.db import Database, Trade
from polymarket_agent.execution.base import Executor, Order, Portfolio
from polymarket_agent.strategies.base import Signal

logger = logging.getLogger(__name__)

_HOST = "https://clob.polymarket.com"
_CHAIN_ID = 137


class LiveTrader(Executor):
    """Execute real orders on Polymarket via the CLOB API.

    Requires ``py-clob-client`` to be installed (``pip install polymarket-agent[live]``).
    Authentication uses ``POLYMARKET_PRIVATE_KEY`` env var. Optionally set
    ``POLYMARKET_FUNDER`` for Magic/proxy wallets.
    """

    def __init__(self, *, private_key: str, db: Database, funder: str | None = None) -> None:
        try:
            from py_clob_client.client import ClobClient  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as exc:
            msg = "py-clob-client is required for live trading: pip install polymarket-agent[live]"
            raise ImportError(msg) from exc

        sig_type = 1 if funder else 0
        self._client: Any = ClobClient(
            _HOST,
            key=private_key,
            chain_id=_CHAIN_ID,
            signature_type=sig_type,
            funder=funder or "",
        )
        self._client.set_api_creds(self._client.create_or_derive_api_creds())
        self._db = db
        logger.info("LiveTrader initialized (funder=%s)", "yes" if funder else "none")

    def place_order(self, signal: Signal) -> Order | None:
        """Place a limit order (GTC) on the Polymarket CLOB."""
        if signal.target_price <= 0:
            logger.error("Invalid target price for live order: %s", signal.target_price)
            return None

        try:
            from py_clob_client.clob_types import (  # type: ignore[import-not-found]  # noqa: PLC0415
                OrderArgs,
                OrderType,
            )
            from py_clob_client.order_builder.constants import (  # type: ignore[import-not-found]  # noqa: PLC0415
                BUY,
                SELL,
            )
        except ImportError:
            logger.error("py-clob-client not available")
            return None

        side_const = BUY if signal.side == "buy" else SELL
        # Signal.size is tracked in USDC across the codebase; CLOB limit orders expect share quantity.
        shares = signal.size / signal.target_price
        order_args = OrderArgs(
            token_id=signal.token_id,
            price=signal.target_price,
            size=shares,
            side=side_const,
        )

        try:
            signed = self._client.create_order(order_args)
            resp = self._client.post_order(signed, OrderType.GTC)
        except Exception:
            logger.exception("Failed to place live order for %s", signal.market_id)
            return None

        if not resp or resp.get("errorMsg"):
            error_msg = resp.get("errorMsg", "unknown error") if resp else "empty response"
            logger.error("Order rejected: %s", error_msg)
            return None

        self._db.record_trade(
            Trade(
                strategy=signal.strategy,
                market_id=signal.market_id,
                token_id=signal.token_id,
                side=signal.side,
                price=signal.target_price,
                size=signal.size,
                reason=signal.reason,
            )
        )

        return Order(
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side,
            price=signal.target_price,
            size=signal.size,
            shares=shares,
        )

    def get_portfolio(self) -> Portfolio:
        """Fetch live balance and positions from the CLOB API."""
        try:
            # py-clob-client doesn't expose a direct balance endpoint;
            # balance is typically managed on-chain. Return a portfolio
            # reflecting open positions only.
            from py_clob_client.clob_types import OpenOrderParams  # noqa: PLC0415

            open_orders = self._client.get_orders(OpenOrderParams())
            positions: dict[str, dict[str, Any]] = {}

            for order in open_orders:
                token_id = str(order.get("asset_id", ""))
                if token_id and token_id not in positions:
                    positions[token_id] = {
                        "shares": float(order.get("original_size", 0)),
                        "avg_price": float(order.get("price", 0)),
                        "current_price": float(order.get("price", 0)),
                    }

            return Portfolio(balance=0.0, positions=positions)
        except Exception:
            logger.exception("Failed to fetch live portfolio")
            return Portfolio(balance=0.0)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID."""
        try:
            self._client.cancel(order_id)
            return True
        except Exception:
            logger.exception("Failed to cancel order %s", order_id)
            return False

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Return currently open orders from the CLOB API."""
        try:
            from py_clob_client.clob_types import OpenOrderParams  # noqa: PLC0415

            orders: list[dict[str, Any]] = self._client.get_orders(OpenOrderParams())
            return orders
        except Exception:
            logger.exception("Failed to fetch open orders")
            return []

    @staticmethod
    def from_env(db: Database) -> "LiveTrader":
        """Create a LiveTrader from environment variables."""
        private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
        if not private_key:
            msg = "POLYMARKET_PRIVATE_KEY environment variable is required for live trading"
            raise ValueError(msg)
        funder = os.environ.get("POLYMARKET_FUNDER")
        return LiveTrader(private_key=private_key, db=db, funder=funder)
