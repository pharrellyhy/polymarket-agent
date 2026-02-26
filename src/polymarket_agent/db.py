"""SQLite database for trade logging and portfolio state."""

import sqlite3
from dataclasses import astuple, dataclass
from pathlib import Path


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
        self._conn = sqlite3.connect(str(path))
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
        self._conn.commit()

    def record_trade(self, trade: Trade) -> None:
        """Insert a trade record into the database."""
        self._conn.execute(
            "INSERT INTO trades (strategy, market_id, token_id, side, price, size, reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            astuple(trade),
        )
        self._conn.commit()

    def get_trades(self, strategy: str | None = None) -> list[dict[str, object]]:
        """Retrieve trades, optionally filtered by strategy name."""
        query = "SELECT * FROM trades"
        params: tuple[str, ...] = ()
        if strategy:
            query += " WHERE strategy = ?"
            params = (strategy,)
        query += " ORDER BY timestamp DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
