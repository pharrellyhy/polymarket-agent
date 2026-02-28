"""News provider package for fetching real-world context."""

from polymarket_agent.news.models import NewsItem
from polymarket_agent.news.provider import NewsProvider

__all__ = ["NewsItem", "NewsProvider"]
