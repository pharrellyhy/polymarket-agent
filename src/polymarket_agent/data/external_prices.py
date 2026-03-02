"""External prediction market API clients (Kalshi, Metaculus).

Provides price data from external platforms for cross-platform arbitrage
detection. Both clients use urllib and TTLCache following existing patterns.
"""

import json
import logging
import os
import urllib.request
from typing import Any

from polymarket_agent.data.cache import TTLCache
from polymarket_agent.data.models import CrossPlatformPrice

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "polymarket-agent/1.0"}
_TIMEOUT = 15


def _coerce_probability(value: Any) -> float | None:
    """Convert API probability-like values to a normalized 0-1 float."""
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None

    if probability > 1.0:
        probability /= 100.0
    return max(0.0, min(1.0, probability))


class KalshiClient:
    """Fetch active event prices from Kalshi's public API."""

    _BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"

    def __init__(self, cache_ttl: float = 300.0, api_key_env: str = "KALSHI_API_KEY") -> None:
        self._cache = TTLCache(default_ttl=cache_ttl)
        self._api_key = os.environ.get(api_key_env, "")

    def get_active_events(self, *, limit: int = 50) -> list[CrossPlatformPrice]:
        """Fetch active events and extract yes-side prices."""
        cache_key = f"kalshi:events:{limit}"
        cached = self._cache.get(cache_key)
        if isinstance(cached, list):
            return cached

        url = f"{self._BASE_URL}/events?status=open&limit={limit}"
        prices = self._fetch_prices(url)
        self._cache.set(cache_key, prices)
        return prices

    def _fetch_prices(self, url: str) -> list[CrossPlatformPrice]:
        """Fetch and parse event prices from Kalshi."""
        headers = {**_HEADERS}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
                data: dict[str, Any] = json.loads(resp.read().decode())
        except Exception:
            logger.debug("Kalshi API request failed")
            return []

        prices: list[CrossPlatformPrice] = []
        for event in data.get("events", []):
            if not isinstance(event, dict):
                continue
            markets = event.get("markets", [])
            if not isinstance(markets, list):
                continue
            for market in markets:
                if not isinstance(market, dict):
                    continue
                probability = _coerce_probability(market.get("yes_ask"))
                if probability is None:
                    continue
                question = str(market.get("title") or event.get("title") or "")
                prices.append(
                    CrossPlatformPrice(
                        platform="kalshi",
                        question=question,
                        probability=probability,
                        url=f"https://kalshi.com/markets/{market.get('ticker', '')}",
                        last_updated=market.get("close_time", ""),
                    )
                )
        return prices


class MetaculusClient:
    """Fetch binary question predictions from Metaculus."""

    _BASE_URL = "https://www.metaculus.com/api2"

    def __init__(self, cache_ttl: float = 600.0, api_key_env: str = "METACULUS_API_KEY") -> None:
        self._cache = TTLCache(default_ttl=cache_ttl)
        self._api_key = os.environ.get(api_key_env, "")

    def get_active_questions(self, *, limit: int = 50) -> list[CrossPlatformPrice]:
        """Fetch active binary questions with community predictions."""
        cache_key = f"metaculus:questions:{limit}"
        cached = self._cache.get(cache_key)
        if isinstance(cached, list):
            return cached

        url = f"{self._BASE_URL}/questions/?type=binary&status=open&limit={limit}&order_by=-activity"
        prices = self._fetch_prices(url)
        self._cache.set(cache_key, prices)
        return prices

    def _fetch_prices(self, url: str) -> list[CrossPlatformPrice]:
        """Fetch and parse question predictions from Metaculus."""
        headers = {**_HEADERS}
        if self._api_key:
            headers["Authorization"] = f"Token {self._api_key}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
                data: dict[str, Any] = json.loads(resp.read().decode())
        except Exception:
            logger.debug("Metaculus API request failed")
            return []

        prices: list[CrossPlatformPrice] = []
        results = data.get("results", data) if isinstance(data, dict) else data
        if not isinstance(results, list):
            results = []

        for question in results:
            if not isinstance(question, dict):
                continue
            prediction = question.get("community_prediction", {})
            if not isinstance(prediction, dict):
                continue
            if not prediction:
                continue
            full = prediction.get("full", {})
            if not isinstance(full, dict):
                continue
            probability = _coerce_probability(full.get("q2"))
            if probability is None:
                continue
            question_title = str(question.get("title") or "")
            prices.append(
                CrossPlatformPrice(
                    platform="metaculus",
                    question=question_title,
                    probability=probability,
                    url=f"https://www.metaculus.com/questions/{question.get('id', '')}",
                    last_updated=question.get("last_activity_time", ""),
                )
            )
        return prices
