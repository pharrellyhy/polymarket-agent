"""Google News RSS provider — free, no API key required."""

import logging
from urllib.parse import quote_plus

from polymarket_agent.news.models import NewsItem

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


class GoogleRSSProvider:
    """Fetch news headlines from Google News RSS feeds.

    Uses ``feedparser`` to parse the RSS XML.  Zero cost, no authentication.
    """

    def search(self, query: str, *, max_results: int = 5) -> list[NewsItem]:
        try:
            import feedparser  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError:
            logger.warning("feedparser not installed — GoogleRSSProvider disabled")
            return []

        url = _GOOGLE_NEWS_RSS_URL.format(query=quote_plus(query))
        try:
            feed = feedparser.parse(url)
        except Exception:
            logger.exception("Failed to fetch Google News RSS for query: %s", query)
            return []

        items: list[NewsItem] = []
        for entry in feed.entries[:max_results]:
            items.append(
                NewsItem(
                    title=entry.get("title", ""),
                    url=entry.get("link", ""),
                    published=entry.get("published", ""),
                    summary=entry.get("summary", ""),
                )
            )
        return items
