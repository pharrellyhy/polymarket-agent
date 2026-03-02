"""Sentiment scoring and keyword spike tracking for AIAnalyst enrichment.

Provides two enrichment capabilities:
1. LLM-based sentiment scoring of news headlines
2. Google RSS keyword frequency tracking with spike detection
"""

import logging
import re
import time
from typing import Protocol
from urllib.parse import quote_plus

from polymarket_agent.data.models import KeywordSpike, SentimentScore
from polymarket_agent.news.models import NewsItem

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


class LLMCaller(Protocol):
    """Protocol for LLM call functions used by sentiment scoring."""

    def __call__(self, prompt: str) -> str: ...


def score_sentiment(
    items: list[NewsItem],
    market_id: str,
    llm_call: LLMCaller,
) -> SentimentScore | None:
    """Score sentiment of news headlines using an LLM.

    Sends a batch of headlines and asks the LLM to assess overall sentiment.
    Returns None if no items or LLM call fails.
    """
    if not items:
        return None

    headlines = "\n".join(f"- {item.title}" for item in items[:10])
    prompt = (
        "Analyze the sentiment of these news headlines about a prediction market.\n"
        "Headlines:\n"
        f"{headlines}\n\n"
        "Respond in EXACTLY this format (one line each):\n"
        "SENTIMENT: bullish|bearish|neutral\n"
        "SCORE: <decimal from -1.0 to 1.0>\n"
        "SUMMARY: <one sentence summary>"
    )

    try:
        response = llm_call(prompt)
    except Exception:
        logger.debug("Sentiment LLM call failed for market %s", market_id)
        return None

    return _parse_sentiment_response(response, market_id, len(items))


def _parse_sentiment_response(text: str, market_id: str, headline_count: int) -> SentimentScore | None:
    """Parse the structured LLM response into a SentimentScore."""
    sentiment = "neutral"
    score = 0.0
    summary = ""

    for raw_line in text.strip().splitlines():
        line = raw_line.strip()
        upper = line.upper()
        if upper.startswith("SENTIMENT:"):
            raw = line.split(":", 1)[1].strip().lower()
            if raw in ("bullish", "bearish", "neutral"):
                sentiment = raw
        elif upper.startswith("SCORE:"):
            match = re.search(r"-?[01](?:\.\d+)?", line)
            if match:
                score = max(-1.0, min(1.0, float(match.group())))
        elif upper.startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()

    return SentimentScore(
        market_id=market_id,
        sentiment=sentiment,
        score=round(score, 4),
        summary=summary,
        headline_count=headline_count,
    )


def format_sentiment_summary(score: SentimentScore) -> str:
    """Format a SentimentScore into a human-readable prompt section."""
    lines = [
        f"Overall sentiment: {score.sentiment} (score: {score.score:+.2f})",
        f"Based on {score.headline_count} headlines",
    ]
    if score.summary:
        lines.append(f"Summary: {score.summary}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Keyword spike tracker
# ---------------------------------------------------------------------------

_Observation = tuple[float, int]  # (timestamp, count)


class KeywordTracker:
    """Track keyword mention frequency and detect spikes.

    Uses Google RSS to count mentions over sliding time windows.
    Flags spikes when current count >= spike_threshold_multiplier * baseline avg.
    """

    def __init__(
        self,
        *,
        spike_threshold: float = 3.0,
        max_age_hours: float = 48.0,
    ) -> None:
        self._spike_threshold = spike_threshold
        self._max_age_seconds = max_age_hours * 3600.0
        self._observations: dict[str, list[_Observation]] = {}

    def record_observation(self, keyword: str, count: int) -> None:
        """Record a keyword mention count observation."""
        now = time.time()
        if keyword not in self._observations:
            self._observations[keyword] = []
        self._observations[keyword].append((now, count))
        self._prune(keyword)

    def fetch_and_record(self, keyword: str) -> int:
        """Fetch current mention count from Google RSS and record it."""
        count = self._fetch_rss_count(keyword)
        self.record_observation(keyword, count)
        return count

    def detect_spike(self, keyword: str, *, window_hours: int = 24) -> KeywordSpike | None:
        """Check if the keyword shows a spike vs baseline average."""
        obs = self._observations.get(keyword, [])
        if len(obs) < 2:
            return None

        now = time.time()
        window_cutoff = now - (window_hours * 3600)
        recent = [count for ts, count in obs if ts >= window_cutoff]
        if not recent:
            return None

        current_count = recent[-1]
        baseline = [count for ts, count in obs if ts < window_cutoff]
        if not baseline:
            # Not enough history for baseline comparison
            return None

        baseline_avg = sum(baseline) / len(baseline)
        if baseline_avg <= 0:
            return None

        ratio = current_count / baseline_avg
        if ratio >= self._spike_threshold:
            return KeywordSpike(
                keyword=keyword,
                current_count=current_count,
                baseline_avg=round(baseline_avg, 2),
                spike_ratio=round(ratio, 2),
                window_hours=window_hours,
            )
        return None

    def detect_spikes(self, keywords: list[str]) -> list[KeywordSpike]:
        """Check multiple keywords for spikes across standard windows."""
        spikes: list[KeywordSpike] = []
        for keyword in keywords:
            for window in (1, 6, 24):
                spike = self.detect_spike(keyword, window_hours=window)
                if spike is not None:
                    spikes.append(spike)
                    break  # One spike per keyword is sufficient
        return spikes

    def _prune(self, keyword: str) -> None:
        """Remove observations older than max_age_hours."""
        cutoff = time.time() - self._max_age_seconds
        self._observations[keyword] = [(ts, c) for ts, c in self._observations[keyword] if ts >= cutoff]

    @staticmethod
    def _fetch_rss_count(keyword: str) -> int:
        """Fetch headline count from Google RSS for the keyword."""
        try:
            import feedparser  # type: ignore[import-untyped]  # noqa: PLC0415
        except ImportError:
            logger.debug("feedparser not installed — keyword counting disabled")
            return 0

        url = _GOOGLE_NEWS_RSS_URL.format(query=quote_plus(keyword))
        try:
            feed = feedparser.parse(url)
            return len(feed.entries)
        except Exception:
            logger.debug("Failed to fetch RSS for keyword: %s", keyword)
            return 0


def format_keyword_spikes(spikes: list[KeywordSpike]) -> str:
    """Format keyword spikes into a human-readable prompt section."""
    lines: list[str] = []
    for spike in spikes:
        lines.append(
            f"- '{spike.keyword}': {spike.current_count} mentions "
            f"({spike.spike_ratio:.1f}x baseline avg of {spike.baseline_avg:.0f}) "
            f"in {spike.window_hours}h window"
        )
    return "\n".join(lines)
