"""Tavily news provider — optional upgrade with structured, LLM-optimized results."""

import logging
import os
from typing import Any

from polymarket_agent.news.models import NewsItem

logger = logging.getLogger(__name__)


class TavilyProvider:
    """Fetch news using the Tavily search API.

    Requires ``tavily-python`` to be installed and ``TAVILY_API_KEY`` (or a
    custom env var) to be set.  Gracefully returns empty results otherwise.
    """

    def __init__(self, api_key_env: str = "TAVILY_API_KEY") -> None:
        self._api_key_env = api_key_env
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            logger.info("%s not set — TavilyProvider disabled", self._api_key_env)
            return
        try:
            from tavily import TavilyClient  # type: ignore[import-not-found]  # noqa: PLC0415

            self._client = TavilyClient(api_key=api_key)
        except ImportError:
            logger.warning("tavily-python not installed — TavilyProvider disabled")

    def search(self, query: str, *, max_results: int = 5) -> list[NewsItem]:
        if self._client is None:
            return []

        try:
            response = self._client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
                topic="news",
            )
        except Exception:
            logger.exception("Tavily search failed for query: %s", query)
            return []

        items: list[NewsItem] = []
        for result in response.get("results", [])[:max_results]:
            items.append(
                NewsItem(
                    title=result.get("title", ""),
                    url=result.get("url", ""),
                    published=result.get("published_date", ""),
                    summary=result.get("content", "")[:300],
                )
            )
        return items
