"""Tests for the reflection and memory loop."""

import tempfile
from pathlib import Path

from polymarket_agent.db import Database
from polymarket_agent.strategies.reflection import ReflectionEngine, _extract_field


def _mock_llm(prompt: str) -> str:
    """Mock LLM that returns structured reflection output."""
    return (
        "KEY_FACTOR: Market sentiment shifted due to unexpected policy change\n"
        "LESSON: Always check for pending policy decisions before entering political markets\n"
        "APPLICABLE_TYPES: politics, policy, regulation\n"
        "KEYWORDS: policy, regulation, government, election\n"
    )


def test_extract_field() -> None:
    text = "KEY_FACTOR: some factor\nLESSON: some lesson"
    assert _extract_field(text, "KEY_FACTOR") == "some factor"
    assert _extract_field(text, "LESSON") == "some lesson"
    assert _extract_field(text, "MISSING") is None


def test_record_and_search_reflections() -> None:
    """Reflections are stored and searchable via FTS."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        engine = ReflectionEngine(db, _mock_llm)

        row_id = engine.reflect_on_outcome(
            market_question="Will the new trade policy pass?",
            strategy="ai_analyst",
            market_id="100",
            side="buy",
            confidence=0.8,
            predicted_price=0.5,
            actual_result=0.0,
            pnl=-25.0,
            entry_reason="high confidence",
        )
        assert row_id is not None
        assert row_id > 0

        # Search by keyword
        results = engine.retrieve_relevant_lessons("trade policy government")
        assert len(results) >= 1
        assert "policy" in str(results[0].get("keywords", ""))


def test_retrieve_lessons_with_no_data() -> None:
    """No reflections returns empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        engine = ReflectionEngine(db, _mock_llm)

        results = engine.retrieve_relevant_lessons("some random market question")
        assert results == []


def test_format_lessons_for_prompt() -> None:
    """Formatted lessons contain the lesson text."""
    lessons = [
        {"outcome": "loss", "lesson": "Check for policy changes", "strategy": "ai_analyst", "key_factor": "policy shift"},
    ]
    text = ReflectionEngine.format_lessons_for_prompt(lessons)
    assert "PAST LESSONS" in text
    assert "Check for policy changes" in text
    assert "loss" in text


def test_format_empty_lessons() -> None:
    assert ReflectionEngine.format_lessons_for_prompt([]) == ""


def test_reflect_on_outcome_failed_parsing() -> None:
    """Returns None if LLM output can't be parsed."""
    def bad_llm(prompt: str) -> str:
        return "No structured fields here"

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        engine = ReflectionEngine(db, bad_llm)

        row_id = engine.reflect_on_outcome(
            market_question="Will X happen?",
            strategy="ai_analyst",
            market_id="100",
            side="buy",
            confidence=0.8,
            predicted_price=0.5,
            actual_result=1.0,
            pnl=25.0,
            entry_reason="test",
        )
        assert row_id is None


def test_multiple_reflections_searchable() -> None:
    """Multiple reflections are all searchable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")

        # Insert reflections directly for predictable testing
        db.record_reflection(
            strategy="ai_analyst",
            market_id="100",
            market_question="Will Bitcoin reach 100k?",
            side="buy",
            confidence=0.8,
            outcome="win",
            pnl=50.0,
            lesson="Crypto markets follow halving cycles",
            key_factor="halving cycle",
            applicable_types="crypto",
            keywords="bitcoin, crypto, halving",
        )
        db.record_reflection(
            strategy="ai_analyst",
            market_id="200",
            market_question="Will the election result be contested?",
            side="sell",
            confidence=0.7,
            outcome="loss",
            pnl=-20.0,
            lesson="Election markets are highly volatile near deadlines",
            key_factor="deadline proximity",
            applicable_types="politics, elections",
            keywords="election, contested, deadline",
        )

        results = db.search_reflections("election contested")
        assert len(results) >= 1
        assert "election" in str(results[0].get("keywords", "")).lower()

        results = db.search_reflections("bitcoin halving")
        assert len(results) >= 1
        assert "bitcoin" in str(results[0].get("keywords", "")).lower()
