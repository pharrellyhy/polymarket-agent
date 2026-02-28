"""Cached and rate-limited wrapper around any NewsProvider."""

import logging
import time

from polymarket_agent.data.cache import TTLCache
from polymarket_agent.news.models import NewsItem
from polymarket_agent.news.provider import NewsProvider

logger = logging.getLogger(__name__)


class CachedNewsProvider:
    """Wrap a ``NewsProvider`` with TTL caching and hourly rate limiting.

    Queries are cached for ``cache_ttl`` seconds (default 15 minutes).
    After ``max_calls_per_hour`` unique queries, further searches return
    empty results until the hour window rolls forward.
    """

    def __init__(
        self,
        inner: NewsProvider,
        *,
        cache_ttl: float = 900.0,
        max_calls_per_hour: int = 50,
    ) -> None:
        self._inner = inner
        self._cache = TTLCache(default_ttl=cache_ttl)
        self._max_calls_per_hour = max_calls_per_hour
        self._call_timestamps: list[float] = []

    def search(self, query: str, *, max_results: int = 5) -> list[NewsItem]:
        cache_key = f"news:{query}:{max_results}"
        cached: list[NewsItem] | None = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if not self._can_call():
            logger.debug("News rate limit reached; returning empty results")
            return []

        results = self._inner.search(query, max_results=max_results)
        self._call_timestamps.append(time.monotonic())
        self._cache.set(cache_key, results)
        return results

    def _can_call(self) -> bool:
        now = time.monotonic()
        cutoff = now - 3600.0
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        return len(self._call_timestamps) < self._max_calls_per_hour
