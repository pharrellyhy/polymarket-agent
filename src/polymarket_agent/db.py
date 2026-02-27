"""SQLite database for trade logging and portfolio state."""

import sqlite3
from dataclasses import astuple, dataclass
from pathlib import Path

from polymarket_agent.orders import ConditionalOrder, OrderStatus, OrderType


@dataclass
class Trade:
    """A trade to be recorded."""

    strategy: str
    market_id: str
    token_id: str
    side: str
    price: float
    size: float
    reason: str


class Database:
    """SQLite database for persisting trades and portfolio state."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                strategy TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                reason TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                strategy TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                confidence REAL NOT NULL,
                size REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'generated'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                balance REAL NOT NULL,
                total_value REAL NOT NULL,
                positions_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS conditional_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                triggered_at DATETIME,
                token_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                trigger_price REAL NOT NULL,
                size REAL NOT NULL,
                high_watermark REAL,
                trail_percent REAL,
                parent_strategy TEXT NOT NULL,
                reason TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS config_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                changed_by TEXT NOT NULL DEFAULT 'hot_reload',
                diff_json TEXT NOT NULL,
                full_config_json TEXT NOT NULL
            )
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Trade methods
    # ------------------------------------------------------------------

    def record_trade(self, trade: Trade) -> None:
        """Insert a trade record into the database."""
        self._conn.execute(
            "INSERT INTO trades (strategy, market_id, token_id, side, price, size, reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            astuple(trade),
        )
        self._conn.commit()

    def get_trades(
        self,
        strategy: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, object]]:
        """Retrieve trades, optionally filtered by strategy name and/or time.

        Args:
            strategy: If provided, only return trades from this strategy.
            since: If provided, only return trades with timestamp >= this ISO value.
        """
        query = "SELECT * FROM trades"
        conditions: list[str] = []
        params: list[str] = []
        if strategy:
            conditions.append("strategy = ?")
            params.append(strategy)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY timestamp DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Signal log methods
    # ------------------------------------------------------------------

    def record_signal(
        self,
        *,
        strategy: str,
        market_id: str,
        token_id: str,
        side: str,
        confidence: float,
        size: float,
        status: str = "generated",
    ) -> None:
        """Record a signal event in the signal log."""
        self._conn.execute(
            "INSERT INTO signal_log (strategy, market_id, token_id, side, confidence, size, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (strategy, market_id, token_id, side, confidence, size, status),
        )
        self._conn.commit()

    def get_signal_log(self, *, strategy: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        """Retrieve signal log entries, optionally filtered by strategy."""
        query = "SELECT * FROM signal_log"
        params: tuple[str | int, ...] = ()
        if strategy:
            query += " WHERE strategy = ?"
            params = (strategy,)
        query += " ORDER BY id DESC LIMIT ?"
        params = (*params, limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Portfolio snapshot methods
    # ------------------------------------------------------------------

    def record_portfolio_snapshot(
        self,
        *,
        balance: float,
        total_value: float,
        positions_json: str = "{}",
    ) -> None:
        """Record a portfolio snapshot."""
        self._conn.execute(
            "INSERT INTO portfolio_snapshots (balance, total_value, positions_json) VALUES (?, ?, ?)",
            (balance, total_value, positions_json),
        )
        self._conn.commit()

    def get_portfolio_snapshots(
        self,
        *,
        limit: int = 100,
        since: str | None = None,
    ) -> list[dict[str, object]]:
        """Retrieve portfolio snapshots, most recent first.

        Args:
            limit: Maximum number of snapshots to return.
            since: If provided, only return snapshots with timestamp >= this ISO value.
        """
        query = "SELECT * FROM portfolio_snapshots"
        params: list[str | int] = []
        if since:
            query += " WHERE timestamp >= ?"
            params.append(since)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_latest_snapshot(self) -> dict[str, object] | None:
        """Return the most recent portfolio snapshot, or None if none exist."""
        row = self._conn.execute("SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Conditional order methods
    # ------------------------------------------------------------------

    def create_conditional_order(
        self,
        *,
        token_id: str,
        market_id: str,
        order_type: OrderType,
        trigger_price: float,
        size: float,
        parent_strategy: str,
        reason: str,
        high_watermark: float | None = None,
        trail_percent: float | None = None,
    ) -> int:
        """Insert a new conditional order and return its ID."""
        cursor = self._conn.execute(
            "INSERT INTO conditional_orders"
            " (token_id, market_id, order_type, trigger_price, size, high_watermark,"
            "  trail_percent, parent_strategy, reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                token_id,
                market_id,
                order_type.value,
                trigger_price,
                size,
                high_watermark,
                trail_percent,
                parent_strategy,
                reason,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_active_conditional_orders(self) -> list[ConditionalOrder]:
        """Return all conditional orders with status='active'."""
        rows = self._conn.execute(
            "SELECT * FROM conditional_orders WHERE status = 'active' ORDER BY created_at"
        ).fetchall()
        return [self._row_to_conditional_order(row) for row in rows]

    def update_conditional_order(self, order_id: int, *, status: OrderStatus) -> None:
        """Update a conditional order's status (and set triggered_at if triggered)."""
        if status == OrderStatus.TRIGGERED:
            self._conn.execute(
                "UPDATE conditional_orders SET status = ?, triggered_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status.value, order_id),
            )
        else:
            self._conn.execute(
                "UPDATE conditional_orders SET status = ? WHERE id = ?",
                (status.value, order_id),
            )
        self._conn.commit()

    def cancel_conditional_orders_for_token(self, token_id: str) -> int:
        """Cancel all active conditional orders for the given token. Returns count cancelled."""
        cursor = self._conn.execute(
            "UPDATE conditional_orders SET status = ? WHERE token_id = ? AND status = 'active'",
            (OrderStatus.CANCELLED.value, token_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def update_high_watermark(self, order_id: int, high_watermark: float) -> None:
        """Update the high watermark for a trailing stop order."""
        self._conn.execute(
            "UPDATE conditional_orders SET high_watermark = ? WHERE id = ?",
            (high_watermark, order_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_conditional_order(row: sqlite3.Row) -> ConditionalOrder:
        """Convert a DB row to a ConditionalOrder dataclass."""
        return ConditionalOrder(
            id=row["id"],
            token_id=row["token_id"],
            market_id=row["market_id"],
            order_type=OrderType(row["order_type"]),
            status=OrderStatus(row["status"]),
            trigger_price=row["trigger_price"],
            size=row["size"],
            high_watermark=row["high_watermark"],
            trail_percent=row["trail_percent"],
            parent_strategy=row["parent_strategy"],
            reason=row["reason"],
            created_at=row["created_at"] or "",
            triggered_at=row["triggered_at"],
        )

    # ------------------------------------------------------------------
    # Config change methods
    # ------------------------------------------------------------------

    def record_config_change(self, changed_by: str, diff_json: str, full_config_json: str) -> None:
        """Insert a config change record."""
        self._conn.execute(
            "INSERT INTO config_changes (changed_by, diff_json, full_config_json) VALUES (?, ?, ?)",
            (changed_by, diff_json, full_config_json),
        )
        self._conn.commit()

    def get_config_changes(self, limit: int = 50) -> list[dict[str, object]]:
        """Retrieve config changes, most recent first."""
        rows = self._conn.execute("SELECT * FROM config_changes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def get_all_conditional_orders(self, limit: int = 100) -> list[dict[str, object]]:
        """Retrieve all conditional orders (all statuses), most recent first."""
        rows = self._conn.execute(
            "SELECT * FROM conditional_orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
