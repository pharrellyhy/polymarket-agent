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
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                strategy TEXT NOT NULL,
                market_id TEXT NOT NULL,
                market_question TEXT NOT NULL,
                side TEXT NOT NULL,
                confidence REAL NOT NULL,
                outcome TEXT NOT NULL,
                pnl REAL NOT NULL,
                lesson TEXT NOT NULL,
                key_factor TEXT NOT NULL DEFAULT '',
                applicable_types TEXT NOT NULL DEFAULT '',
                keywords TEXT NOT NULL DEFAULT ''
            )
        """)
        # FTS5 virtual table for keyword search over reflections
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS trade_reflections_fts
            USING fts5(lesson, key_factor, applicable_types, keywords, content=trade_reflections, content_rowid=id)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at DATETIME,
                strategy TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                confidence REAL NOT NULL,
                predicted_price REAL NOT NULL,
                entry_price REAL NOT NULL,
                resolved_price REAL,
                pnl REAL,
                brier_score REAL,
                size REAL NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'pending'
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
    # Trade reflection methods
    # ------------------------------------------------------------------

    def record_reflection(
        self,
        *,
        strategy: str,
        market_id: str,
        market_question: str,
        side: str,
        confidence: float,
        outcome: str,
        pnl: float,
        lesson: str,
        key_factor: str = "",
        applicable_types: str = "",
        keywords: str = "",
    ) -> int:
        """Record a post-trade reflection and index it for FTS search."""
        cursor = self._conn.execute(
            "INSERT INTO trade_reflections"
            " (strategy, market_id, market_question, side, confidence, outcome, pnl,"
            "  lesson, key_factor, applicable_types, keywords)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (strategy, market_id, market_question, side, confidence, outcome, pnl,
             lesson, key_factor, applicable_types, keywords),
        )
        row_id = cursor.lastrowid or 0
        # Sync to FTS index
        self._conn.execute(
            "INSERT INTO trade_reflections_fts(rowid, lesson, key_factor, applicable_types, keywords)"
            " VALUES (?, ?, ?, ?, ?)",
            (row_id, lesson, key_factor, applicable_types, keywords),
        )
        self._conn.commit()
        return row_id

    def search_reflections(self, query: str, *, limit: int = 5) -> list[dict[str, object]]:
        """Search past reflections using FTS5 keyword matching.

        Args:
            query: Search terms (space-separated keywords).
            limit: Maximum results to return.

        Returns:
            List of reflection dicts, ranked by FTS relevance.
        """
        if not query.strip():
            return []
        # Sanitize query for FTS5 (escape special chars)
        safe_query = " OR ".join(word for word in query.split() if word.strip())
        if not safe_query:
            return []
        try:
            rows = self._conn.execute(
                "SELECT r.* FROM trade_reflections r"
                " JOIN trade_reflections_fts fts ON r.id = fts.rowid"
                " WHERE trade_reflections_fts MATCH ?"
                " ORDER BY fts.rank"
                " LIMIT ?",
                (safe_query, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Signal outcome methods
    # ------------------------------------------------------------------

    def record_signal_outcome(
        self,
        *,
        strategy: str,
        market_id: str,
        token_id: str,
        side: str,
        confidence: float,
        predicted_price: float,
        entry_price: float,
        size: float,
    ) -> int:
        """Record a signal outcome when a signal is executed. Returns the row ID."""
        cursor = self._conn.execute(
            "INSERT INTO signal_outcomes"
            " (strategy, market_id, token_id, side, confidence, predicted_price, entry_price, size)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (strategy, market_id, token_id, side, confidence, predicted_price, entry_price, size),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def resolve_signal_outcomes(
        self,
        market_id: str,
        resolved_price: float,
    ) -> int:
        """Resolve all pending outcomes for a market. Computes P&L and Brier score.

        Returns the number of outcomes resolved.
        """
        rows = self._conn.execute(
            "SELECT id, side, confidence, entry_price, size FROM signal_outcomes"
            " WHERE market_id = ? AND outcome = 'pending'",
            (market_id,),
        ).fetchall()

        resolved_count = 0
        for row in rows:
            outcome_id = row["id"]
            side = row["side"]
            entry_price = row["entry_price"]
            confidence = row["confidence"]
            size = row["size"]

            # P&L: for buys, profit = (resolved - entry) * shares; shares = size / entry
            if entry_price > 0:
                shares = size / entry_price
                if side == "buy":
                    pnl = (resolved_price - entry_price) * shares
                else:
                    pnl = (entry_price - resolved_price) * shares
            else:
                pnl = 0.0

            # Brier score: measures calibration. Lower = better.
            # For a buy signal, the implied prediction is that the event resolves "yes" (price → 1.0)
            # For a sell signal, the implied prediction is "no" (price → 0.0)
            if side == "buy":
                brier_score = (confidence - resolved_price) ** 2
            else:
                brier_score = ((1.0 - confidence) - resolved_price) ** 2

            outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"

            self._conn.execute(
                "UPDATE signal_outcomes SET resolved_at = CURRENT_TIMESTAMP,"
                " resolved_price = ?, pnl = ?, brier_score = ?, outcome = ?"
                " WHERE id = ?",
                (resolved_price, round(pnl, 6), round(brier_score, 6), outcome, outcome_id),
            )
            resolved_count += 1

        if resolved_count > 0:
            self._conn.commit()
        return resolved_count

    def get_strategy_accuracy(self, strategy: str | None = None, min_samples: int = 0) -> list[dict[str, object]]:
        """Return per-strategy accuracy stats (win_rate, avg_brier, total trades).

        Args:
            strategy: If provided, return stats only for this strategy.
            min_samples: Minimum resolved outcomes to include a strategy.
        """
        query = """
            SELECT strategy,
                   COUNT(*) as total,
                   SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
                   AVG(brier_score) as avg_brier,
                   AVG(pnl) as avg_pnl,
                   SUM(pnl) as total_pnl
            FROM signal_outcomes
            WHERE outcome != 'pending'
        """
        params: list[str | int] = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        query += " GROUP BY strategy HAVING COUNT(*) >= ?"
        params.append(min_samples)
        query += " ORDER BY total DESC"

        rows = self._conn.execute(query, params).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            total = row["total"]
            wins = row["wins"]
            results.append({
                "strategy": row["strategy"],
                "total": total,
                "wins": wins,
                "win_rate": round(wins / total, 4) if total > 0 else 0.0,
                "avg_brier": round(row["avg_brier"], 6) if row["avg_brier"] is not None else None,
                "avg_pnl": round(row["avg_pnl"], 4) if row["avg_pnl"] is not None else None,
                "total_pnl": round(row["total_pnl"], 4) if row["total_pnl"] is not None else None,
            })
        return results

    def get_strategy_pnl(self, strategy: str | None = None) -> list[dict[str, object]]:
        """Return per-strategy P&L summary."""
        query = """
            SELECT strategy,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl,
                   COUNT(*) as total_trades,
                   SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                   SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as gross_loss
            FROM signal_outcomes
            WHERE outcome != 'pending'
        """
        params: list[str] = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        query += " GROUP BY strategy ORDER BY total_pnl DESC"

        rows = self._conn.execute(query, params).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            gross_loss = row["gross_loss"] or 0.0
            results.append({
                "strategy": row["strategy"],
                "total_pnl": round(row["total_pnl"] or 0.0, 4),
                "avg_pnl": round(row["avg_pnl"] or 0.0, 4),
                "total_trades": row["total_trades"],
                "gross_profit": round(row["gross_profit"] or 0.0, 4),
                "gross_loss": round(gross_loss, 4),
                "profit_factor": round((row["gross_profit"] or 0.0) / gross_loss, 4) if gross_loss > 0 else None,
            })
        return results

    def get_resolved_outcomes(self) -> list[dict[str, object]]:
        """Return all resolved signal outcomes (strategy, confidence, outcome)."""
        rows = self._conn.execute(
            "SELECT strategy, confidence, outcome FROM signal_outcomes WHERE outcome != 'pending'"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_pending_outcomes_by_market(self) -> dict[str, list[dict[str, object]]]:
        """Return pending signal outcomes grouped by market_id."""
        rows = self._conn.execute(
            "SELECT * FROM signal_outcomes WHERE outcome = 'pending' ORDER BY created_at"
        ).fetchall()
        result: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            market_id = row["market_id"]
            result.setdefault(market_id, []).append(dict(row))
        return result

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
