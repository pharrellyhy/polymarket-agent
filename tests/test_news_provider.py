"""Tests for the news provider package."""

import sys
from unittest.mock import MagicMock, patch

from polymarket_agent.news.cached import CachedNewsProvider
from polymarket_agent.news.google_rss import GoogleRSSProvider
from polymarket_agent.news.models import NewsItem
from polymarket_agent.news.tavily_client import TavilyProvider

# ---------------------------------------------------------------------------
# NewsItem
# ---------------------------------------------------------------------------


def test_news_item_defaults() -> None:
    item = NewsItem(title="Test headline")
    assert item.title == "Test headline"
    assert item.url == ""
    assert item.published == ""
    assert item.summary == ""


def test_news_item_full() -> None:
    item = NewsItem(title="Test", url="https://example.com", published="2026-02-28", summary="A summary")
    assert item.url == "https://example.com"
    assert item.published == "2026-02-28"


# ---------------------------------------------------------------------------
# GoogleRSSProvider
# ---------------------------------------------------------------------------


def test_google_rss_parses_feed() -> None:
    mock_feedparser = MagicMock()
    mock_feed = MagicMock()
    mock_feed.entries = [
        {"title": "Headline 1", "link": "https://a.com", "published": "Fri, 28 Feb 2026", "summary": "Sum 1"},
        {"title": "Headline 2", "link": "https://b.com", "published": "Thu, 27 Feb 2026", "summary": "Sum 2"},
        {"title": "Headline 3", "link": "https://c.com", "published": "Wed, 26 Feb 2026", "summary": "Sum 3"},
    ]
    mock_feedparser.parse.return_value = mock_feed

    with patch.dict(sys.modules, {"feedparser": mock_feedparser}):
        provider = GoogleRSSProvider()
        results = provider.search("Will Biden win?", max_results=2)

    assert len(results) == 2
    assert results[0].title == "Headline 1"
    assert results[1].url == "https://b.com"


def test_google_rss_graceful_without_feedparser() -> None:
    """If feedparser is not installed, return empty results."""
    provider = GoogleRSSProvider()
    saved = sys.modules.pop("feedparser", None)
    try:
        with patch.dict(sys.modules, {"feedparser": None}):
            results = provider.search("test query")
        assert results == []
    finally:
        if saved is not None:
            sys.modules["feedparser"] = saved


def test_google_rss_handles_parse_exception() -> None:
    mock_feedparser = MagicMock()
    mock_feedparser.parse.side_effect = RuntimeError("network error")

    with patch.dict(sys.modules, {"feedparser": mock_feedparser}):
        provider = GoogleRSSProvider()
        results = provider.search("test query")
    assert results == []


# ---------------------------------------------------------------------------
# TavilyProvider
# ---------------------------------------------------------------------------


def test_tavily_disabled_without_api_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        provider = TavilyProvider()
        assert provider._client is None
        results = provider.search("test query")
        assert results == []


def test_tavily_parses_results() -> None:
    mock_client = MagicMock()
    mock_client.search.return_value = {
        "results": [
            {"title": "News 1", "url": "https://a.com", "published_date": "2026-02-28", "content": "Content 1"},
            {"title": "News 2", "url": "https://b.com", "published_date": "2026-02-27", "content": "Content 2"},
        ]
    }

    provider = TavilyProvider()
    provider._client = mock_client
    results = provider.search("Biden election", max_results=5)

    assert len(results) == 2
    assert results[0].title == "News 1"
    assert results[1].published == "2026-02-27"


def test_tavily_handles_api_exception() -> None:
    mock_client = MagicMock()
    mock_client.search.side_effect = RuntimeError("API error")

    provider = TavilyProvider()
    provider._client = mock_client
    results = provider.search("test query")
    assert results == []


# ---------------------------------------------------------------------------
# CachedNewsProvider
# ---------------------------------------------------------------------------


def _make_inner(items: list[NewsItem] | None = None) -> MagicMock:
    inner = MagicMock()
    inner.search.return_value = items or [NewsItem(title="Cached headline")]
    return inner


def test_cached_returns_results() -> None:
    inner = _make_inner()
    provider = CachedNewsProvider(inner, cache_ttl=60.0)
    results = provider.search("test query")
    assert len(results) == 1
    assert results[0].title == "Cached headline"
    inner.search.assert_called_once()


def test_cached_uses_cache_on_repeat() -> None:
    inner = _make_inner()
    provider = CachedNewsProvider(inner, cache_ttl=60.0)
    provider.search("same query")
    provider.search("same query")
    # Second call should hit cache, not the inner provider
    assert inner.search.call_count == 1


def test_cached_different_queries_hit_inner() -> None:
    inner = _make_inner()
    provider = CachedNewsProvider(inner, cache_ttl=60.0)
    provider.search("query 1")
    provider.search("query 2")
    assert inner.search.call_count == 2


def test_cached_respects_rate_limit() -> None:
    inner = _make_inner()
    provider = CachedNewsProvider(inner, cache_ttl=0.0, max_calls_per_hour=2)
    # cache_ttl=0 forces cache misses
    provider.search("q1")
    provider.search("q2")
    results = provider.search("q3")
    # Third call should be rate-limited
    assert inner.search.call_count == 2
    assert results == []
