"""NewsProvider protocol defining the search interface."""

from typing import Protocol

from polymarket_agent.news.models import NewsItem


class NewsProvider(Protocol):
    """Structural protocol for news search providers."""

    def search(self, query: str, *, max_results: int = 5) -> list[NewsItem]: ...
