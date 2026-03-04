"""Reflection engine — post-trade learning via LLM analysis.

After each resolved trade, the reflection engine asks an LLM to analyze
what went right or wrong, extracting lessons that are stored in an FTS5
index for retrieval on similar future markets.
"""

import logging
import re
from typing import Callable

from polymarket_agent.db import Database

logger = logging.getLogger(__name__)


class ReflectionEngine:
    """Generate and retrieve trade reflections for institutional memory."""

    def __init__(self, db: Database, call_llm: Callable[[str], str]) -> None:
        self._db = db
        self._call_llm = call_llm

    def reflect_on_outcome(
        self,
        *,
        market_question: str,
        strategy: str,
        market_id: str,
        side: str,
        confidence: float,
        predicted_price: float,
        actual_result: float,
        pnl: float,
        entry_reason: str,
    ) -> int | None:
        """Generate a reflection on a resolved trade outcome.

        Uses the LLM to analyze what went right/wrong and stores the lesson
        in the database for future retrieval.

        Returns the reflection row ID, or None if generation fails.
        """
        outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"

        prompt = (
            "You are analyzing a completed prediction market trade to extract lessons.\n\n"
            f"Market question: {market_question}\n"
            f"Strategy: {strategy}\n"
            f"Position: {side} at confidence {confidence:.2f}\n"
            f"Entry reason: {entry_reason}\n"
            f"Predicted price: {predicted_price:.4f}\n"
            f"Actual result: {actual_result:.4f}\n"
            f"P&L: {pnl:+.2f} ({outcome})\n\n"
            "Analyze this trade and respond with:\n"
            "KEY_FACTOR: [The single most important factor that determined the outcome]\n"
            "LESSON: [A concise lesson (1-2 sentences) for similar future trades]\n"
            "APPLICABLE_TYPES: [Comma-separated market types this lesson applies to, e.g. 'politics, elections']\n"
            "KEYWORDS: [Comma-separated search keywords for retrieval, e.g. 'election, polling, incumbent']\n"
        )

        try:
            text = self._call_llm(prompt)
            parsed = self._parse_reflection(text)
            if parsed is None:
                logger.warning("Could not parse reflection response")
                return None

            return self._db.record_reflection(
                strategy=strategy,
                market_id=market_id,
                market_question=market_question,
                side=side,
                confidence=confidence,
                outcome=outcome,
                pnl=pnl,
                lesson=parsed["lesson"],
                key_factor=parsed["key_factor"],
                applicable_types=parsed["applicable_types"],
                keywords=parsed["keywords"],
            )
        except Exception:
            logger.debug("Failed to generate reflection", exc_info=True)
            return None

    def retrieve_relevant_lessons(self, market_question: str, *, limit: int = 3) -> list[dict[str, object]]:
        """Retrieve past reflections relevant to a market question.

        Extracts key terms from the question and searches the FTS index.
        """
        # Extract meaningful keywords from the question
        stop_words = {
            "will", "what", "when", "where", "which", "would", "could",
            "should", "about", "their", "there", "have", "been", "does",
            "this", "that", "with", "from", "than", "more", "before",
            "after", "into", "over", "under",
        }
        words = [
            w.lower().strip("?.,!\"'")
            for w in market_question.split()
            if len(w) > 3 and w.lower() not in stop_words
        ]
        if not words:
            return []

        # Use top keywords for search
        query = " ".join(words[:5])
        return self._db.search_reflections(query, limit=limit)

    @staticmethod
    def format_lessons_for_prompt(lessons: list[dict[str, object]]) -> str:
        """Format retrieved lessons into a prompt section for the AI analyst."""
        if not lessons:
            return ""
        lines: list[str] = ["--- PAST LESSONS ---"]
        for lesson in lessons:
            lines.append(
                f"- [{lesson.get('outcome', '?')}] {lesson.get('lesson', '')}"
                f" (strategy: {lesson.get('strategy', '?')}, "
                f"key factor: {lesson.get('key_factor', '?')})"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_reflection(text: str) -> dict[str, str] | None:
        """Parse structured reflection fields from LLM output."""
        key_factor = _extract_field(text, "KEY_FACTOR")
        lesson = _extract_field(text, "LESSON")
        applicable_types = _extract_field(text, "APPLICABLE_TYPES")
        keywords = _extract_field(text, "KEYWORDS")

        if not lesson:
            return None

        return {
            "key_factor": key_factor or "",
            "lesson": lesson,
            "applicable_types": applicable_types or "",
            "keywords": keywords or "",
        }


def _extract_field(text: str, field_name: str) -> str | None:
    """Extract a named field value from LLM output."""
    pattern = rf"{field_name}\s*:\s*(.+?)(?:\n|$)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip("[]")
    return None
