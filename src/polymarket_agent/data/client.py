"""PolymarketData CLI wrapper client.

Wraps the ``polymarket`` CLI tool, parsing JSON output into typed Pydantic
models and caching results with a configurable TTL.
"""

import json
import subprocess
from typing import Any

from polymarket_agent.data.cache import TTLCache
from polymarket_agent.data.models import Event, Market, OrderBook, PricePoint, Trader


class PolymarketData:
    """Thin wrapper around the ``polymarket`` CLI.

    Every public method delegates to :meth:`_run_cli` (or its cached
    variant) so that all subprocess invocations go through a single
    chokepoint that is easy to mock in tests.
    """

    def __init__(self, cache_ttl: float = 30.0) -> None:
        self._cache = TTLCache(default_ttl=cache_ttl)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_active_markets(self, *, tag: str | None = None, limit: int = 50) -> list[Market]:
        """Return active markets from the Polymarket CLI."""
        args = ["polymarket", "markets", "list", "--active", "true", "--limit", str(limit), "-o", "json"]
        if tag:
            args.extend(["--tag", tag])

        raw = self._run_cli_cached(f"markets:{tag}:{limit}", args)
        data: list[dict[str, Any]] = json.loads(raw)
        return [Market.from_cli(m) for m in data]

    def get_events(self, *, tag: str | None = None, limit: int = 50) -> list[Event]:
        """Return active events from the Polymarket CLI."""
        args = ["polymarket", "events", "list", "--active", "true", "--limit", str(limit), "-o", "json"]
        if tag:
            args.extend(["--tag", tag])

        raw = self._run_cli_cached(f"events:{tag}:{limit}", args)
        data: list[dict[str, Any]] = json.loads(raw)
        return [Event.from_cli(e) for e in data]

    def get_orderbook(self, token_id: str) -> OrderBook:
        """Return the order book for a CLOB token."""
        args = ["polymarket", "clob", "book", token_id, "-o", "json"]
        raw = self._run_cli_cached(f"book:{token_id}", args)
        data: dict[str, Any] = json.loads(raw)
        return OrderBook.from_cli(data)

    def get_market(self, market_id: str) -> Market | None:
        """Fetch a single market by ID. Returns None if not found."""
        args = ["polymarket", "markets", "get", market_id, "-o", "json"]
        try:
            raw = self._run_cli_cached(f"market:{market_id}", args)
            data: dict[str, Any] = json.loads(raw)
            return Market.from_cli(data)
        except (RuntimeError, json.JSONDecodeError, KeyError):
            return None

    def search_markets(self, query: str, *, limit: int = 25) -> list[Market]:
        """Search active markets by keyword in question text."""
        markets = self.get_active_markets(limit=100)
        query_lower = query.lower()
        matches = [m for m in markets if query_lower in m.question.lower()]
        return matches[:limit]

    def get_leaderboard(self, *, period: str = "month") -> list[Trader]:
        """Return top traders from the Polymarket leaderboard."""
        args = ["polymarket", "leaderboard", "--period", period, "-o", "json"]
        raw = self._run_cli_cached(f"leaderboard:{period}", args)
        data: list[dict[str, Any]] = json.loads(raw)
        return [Trader.from_cli(t, rank=i + 1) for i, t in enumerate(data)]

    def get_price_history(
        self,
        token_id: str,
        *,
        interval: str = "1d",
        fidelity: int = 60,
    ) -> list[PricePoint]:
        """Return historical price points for a CLOB token."""
        args = [
            "polymarket",
            "clob",
            "price-history",
            token_id,
            "--interval",
            interval,
            "--fidelity",
            str(fidelity),
            "-o",
            "json",
        ]
        raw = self._run_cli_cached(f"history:{token_id}:{interval}:{fidelity}", args)
        data: list[dict[str, Any]] = json.loads(raw)
        return [PricePoint.from_cli(p) for p in data]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_cli(self, args: list[str], *, timeout: float = 30.0) -> str:
        """Execute a ``polymarket`` CLI command and return its stdout.

        Raises :class:`RuntimeError` when the process exits with a
        non-zero return code or times out.
        """
        try:
            result = subprocess.run(  # noqa: S603
                args, capture_output=True, text=True, check=False, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"polymarket CLI timed out after {timeout}s: {' '.join(args)}"
            raise RuntimeError(msg) from exc
        if result.returncode != 0:
            msg = f"polymarket CLI failed (rc={result.returncode}): {result.stderr}"
            raise RuntimeError(msg)
        return result.stdout

    def _run_cli_cached(self, key: str, args: list[str]) -> str:
        """Return cached CLI output or execute and cache the result."""
        cached = self._cache.get(key)
        if cached is not None:
            assert isinstance(cached, str)
            return cached
        raw = self._run_cli(args)
        self._cache.set(key, raw)
        return raw
