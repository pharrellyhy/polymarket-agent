"""Gamma Markets API client.

Provides typed access to the Gamma API (https://gamma-api.polymarket.com)
with TTL caching. Extracted from inline urllib usage in the orchestrator.
"""

import json
import logging
import urllib.request
from typing import Any

from polymarket_agent.data.cache import TTLCache
from polymarket_agent.data.models import WhaleTrade

logger = logging.getLogger(__name__)

_BASE_URL = "https://gamma-api.polymarket.com"
_DEFAULT_TTL = 120.0
_HEADERS = {"User-Agent": "polymarket-agent/1.0"}
_TIMEOUT = 15


class GammaClient:
    """Thin wrapper around the Gamma Markets REST API with TTL caching."""

    def __init__(self, cache_ttl: float = _DEFAULT_TTL) -> None:
        self._cache = TTLCache(default_ttl=cache_ttl)

    def _get(self, path: str, *, cache_key: str | None = None) -> Any:
        """Perform a cached GET request to the Gamma API."""
        key = cache_key or path
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        url = f"{_BASE_URL}{path}"
        req = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
                data = json.loads(resp.read().decode())
        except Exception:
            logger.debug("Gamma API request failed: %s", path)
            raise

        self._cache.set(key, data)
        return data

    def get_trader_activity(self, address: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent trading activity for a wallet address."""
        path = f"/activity?maker={address}&limit={limit}"
        try:
            data = self._get(path, cache_key=f"activity:{address}:{limit}")
            return data if isinstance(data, list) else []
        except Exception:
            logger.debug("Failed to fetch activity for %s", address)
            return []

    def get_market_trades(self, market_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recent trades for a specific market."""
        path = f"/activity?market={market_id}&limit={limit}"
        try:
            data = self._get(path, cache_key=f"trades:{market_id}:{limit}")
            return data if isinstance(data, list) else []
        except Exception:
            logger.debug("Failed to fetch trades for market %s", market_id)
            return []

    def search_events(self, slug: str) -> list[dict[str, Any]]:
        """Search for events by slug."""
        path = f"/events?slug={slug}"
        try:
            data = self._get(path, cache_key=f"events:{slug}")
            return data if isinstance(data, list) else []
        except Exception:
            logger.debug("Failed to search events for slug %s", slug)
            return []

    def parse_whale_trades(
        self,
        activities: list[dict[str, Any]],
        *,
        trader_name: str,
        trader_address: str,
        rank: int,
        min_size: float = 500.0,
    ) -> list[WhaleTrade]:
        """Parse raw Gamma activity into WhaleTrade models, filtering by size."""
        trades: list[WhaleTrade] = []
        for activity in activities:
            size = float(activity.get("size", 0) or 0)
            if size < min_size:
                continue
            side = activity.get("side", activity.get("type", "buy"))
            trades.append(
                WhaleTrade(
                    trader_name=trader_name,
                    trader_address=trader_address,
                    rank=rank,
                    market_id=str(activity.get("market", activity.get("conditionId", ""))),
                    token_id=str(activity.get("asset", activity.get("tokenId", ""))),
                    side=str(side).lower() if side else "buy",
                    size=size,
                    price=float(activity.get("price", 0) or 0),
                    timestamp=str(activity.get("timestamp", "")),
                )
            )
        return trades
