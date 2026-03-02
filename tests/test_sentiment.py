"""Tests for sentiment scoring and keyword spike tracking."""

import time

from polymarket_agent.data.models import KeywordSpike, SentimentScore
from polymarket_agent.news.models import NewsItem
from polymarket_agent.news.sentiment import (
    KeywordTracker,
    format_keyword_spikes,
    format_sentiment_summary,
    score_sentiment,
)


def _make_items(n: int = 3) -> list[NewsItem]:
    return [NewsItem(title=f"Headline {i}: market moves significantly", published="2026-03-01") for i in range(n)]


def _mock_llm(prompt: str) -> str:
    return "SENTIMENT: bullish\nSCORE: 0.7\nSUMMARY: Markets are looking up."


def _mock_llm_bearish(prompt: str) -> str:
    return "SENTIMENT: bearish\nSCORE: -0.5\nSUMMARY: Pessimistic outlook."


def _mock_llm_malformed(prompt: str) -> str:
    return "I'm not sure about the sentiment."


def _mock_llm_error(prompt: str) -> str:
    raise RuntimeError("LLM call failed")


def test_score_sentiment_bullish() -> None:
    """Bullish LLM response should produce bullish sentiment score."""
    result = score_sentiment(_make_items(), "market1", _mock_llm)
    assert result is not None
    assert result.sentiment == "bullish"
    assert result.score == 0.7
    assert result.summary == "Markets are looking up."
    assert result.headline_count == 3


def test_score_sentiment_bearish() -> None:
    """Bearish LLM response should produce bearish sentiment score."""
    result = score_sentiment(_make_items(), "market1", _mock_llm_bearish)
    assert result is not None
    assert result.sentiment == "bearish"
    assert result.score == -0.5


def test_score_sentiment_empty_items() -> None:
    """Empty items list should return None."""
    assert score_sentiment([], "market1", _mock_llm) is None


def test_score_sentiment_malformed_response() -> None:
    """Malformed LLM response should still return a score with defaults."""
    result = score_sentiment(_make_items(), "market1", _mock_llm_malformed)
    assert result is not None
    assert result.sentiment == "neutral"
    assert result.score == 0.0


def test_score_sentiment_llm_error() -> None:
    """LLM exception should return None."""
    assert score_sentiment(_make_items(), "market1", _mock_llm_error) is None


def test_format_sentiment_summary() -> None:
    """Formatted summary should contain key fields."""
    score = SentimentScore(
        market_id="m1",
        sentiment="bullish",
        score=0.7,
        summary="Looking good.",
        headline_count=5,
    )
    summary = format_sentiment_summary(score)
    assert "bullish" in summary
    assert "+0.70" in summary
    assert "5 headlines" in summary
    assert "Looking good." in summary


# ---------------------------------------------------------------------------
# KeywordTracker tests
# ---------------------------------------------------------------------------


def test_keyword_tracker_no_spike_insufficient_data() -> None:
    """No spike should be detected with fewer than 2 observations."""
    tracker = KeywordTracker()
    tracker.record_observation("bitcoin", 10)
    assert tracker.detect_spike("bitcoin") is None


def test_keyword_tracker_detects_spike() -> None:
    """Spike should be detected when current count >= 3x baseline."""
    tracker = KeywordTracker(spike_threshold=3.0)
    # Record baseline observations in the past
    now = time.time()
    tracker._observations["bitcoin"] = [
        (now - 86400 * 2, 10),  # 2 days ago
        (now - 86400, 12),  # 1 day ago
        (now - 3600 * 25, 11),  # 25 hours ago (baseline)
    ]
    # Record a spike now
    tracker.record_observation("bitcoin", 35)
    spike = tracker.detect_spike("bitcoin", window_hours=24)
    assert spike is not None
    assert spike.keyword == "bitcoin"
    assert spike.spike_ratio >= 3.0


def test_keyword_tracker_no_spike_below_threshold() -> None:
    """No spike when current count is below threshold multiplier."""
    tracker = KeywordTracker(spike_threshold=3.0)
    now = time.time()
    tracker._observations["test"] = [
        (now - 86400, 10),  # baseline
    ]
    tracker.record_observation("test", 15)  # 1.5x, below 3x
    assert tracker.detect_spike("test", window_hours=1) is None


def test_keyword_tracker_prune_old() -> None:
    """Observations older than max_age_hours should be pruned."""
    tracker = KeywordTracker(max_age_hours=24.0)
    now = time.time()
    tracker._observations["old"] = [
        (now - 86400 * 3, 5),  # 3 days ago
        (now - 86400 * 2, 5),  # 2 days ago
    ]
    tracker.record_observation("old", 10)  # triggers prune
    assert len(tracker._observations["old"]) == 1  # only the new one


def test_keyword_tracker_detect_spikes_multiple() -> None:
    """detect_spikes should check multiple keywords."""
    tracker = KeywordTracker(spike_threshold=2.0)
    now = time.time()
    tracker._observations["alpha"] = [(now - 86400, 10)]
    tracker.record_observation("alpha", 25)
    tracker._observations["beta"] = [(now - 86400, 10)]
    tracker.record_observation("beta", 8)  # no spike

    spikes = tracker.detect_spikes(["alpha", "beta"])
    assert len(spikes) == 1
    assert spikes[0].keyword == "alpha"


def test_format_keyword_spikes() -> None:
    """Formatted spikes should contain key information."""
    spikes = [
        KeywordSpike(keyword="bitcoin", current_count=30, baseline_avg=10.0, spike_ratio=3.0, window_hours=24),
    ]
    formatted = format_keyword_spikes(spikes)
    assert "'bitcoin'" in formatted
    assert "3.0x" in formatted
    assert "24h" in formatted
