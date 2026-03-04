"""Adversarial debate module for improved probability estimation.

Implements a bull/bear/judge debate pattern inspired by TradingAgents.
Three LLM calls force steelmanning both sides, reducing overconfidence
and improving Brier scores on forecasting tasks.
"""

import logging
import re
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class DebateResult:
    """Result of a bull/bear/judge debate."""

    bull_probability: float
    bear_probability: float
    judge_probability: float
    bull_argument: str
    bear_argument: str
    judge_reasoning: str


def run_debate(
    market_question: str,
    market_description: str,
    current_price: float,
    call_llm: Callable[[str], str],
    *,
    context_sections: str = "",
) -> DebateResult | None:
    """Run a bull/bear/judge debate on a market question.

    Args:
        market_question: The prediction market question.
        market_description: Additional market context/description.
        current_price: Current Yes token price (0-1).
        call_llm: Function that sends a prompt to the LLM and returns text.
        context_sections: Additional context (TA, news, etc.) to include in prompts.

    Returns:
        DebateResult with probabilities from all three participants,
        or None if parsing fails for any participant.
    """
    base_context = (
        f"Question: {market_question}\n"
        f"Description: {market_description}\n"
        f"Current market price: {current_price:.4f}\n"
    )
    if context_sections:
        base_context += f"\n{context_sections}\n"

    # --- Bull case ---
    bull_prompt = (
        "You are a BULL analyst in a prediction market debate. "
        "You MUST argue FOR the Yes outcome. Your job is to present "
        "the strongest possible case for why this event will happen.\n\n"
        f"{base_context}\n"
        "Instructions:\n"
        "1. Present 3-4 compelling arguments for why this resolves Yes.\n"
        "2. Identify favorable trends, precedents, or catalysts.\n"
        "3. Provide your estimated probability (MUST be above the current market price).\n"
        "4. End with: PROBABILITY: [number between 0.0 and 1.0]\n"
    )

    bull_text = call_llm(bull_prompt)
    bull_prob = _parse_probability(bull_text)
    if bull_prob is None:
        logger.warning("Failed to parse bull probability from debate")
        return None

    # --- Bear case ---
    bear_prompt = (
        "You are a BEAR analyst in a prediction market debate. "
        "You MUST argue AGAINST the Yes outcome. Your job is to present "
        "the strongest possible case for why this event will NOT happen.\n\n"
        f"{base_context}\n"
        "Instructions:\n"
        "1. Present 3-4 compelling arguments for why this resolves No.\n"
        "2. Identify risks, counterarguments, or historical base rates against it.\n"
        "3. Provide your estimated probability (MUST be below the current market price).\n"
        "4. End with: PROBABILITY: [number between 0.0 and 1.0]\n"
    )

    bear_text = call_llm(bear_prompt)
    bear_prob = _parse_probability(bear_text)
    if bear_prob is None:
        logger.warning("Failed to parse bear probability from debate")
        return None

    # --- Judge synthesis ---
    judge_prompt = (
        "You are an impartial JUDGE synthesizing a prediction market debate. "
        "You have heard arguments from both a Bull and Bear analyst.\n\n"
        f"{base_context}\n"
        f"--- BULL CASE (estimated {bull_prob:.2f}) ---\n{bull_text}\n\n"
        f"--- BEAR CASE (estimated {bear_prob:.2f}) ---\n{bear_text}\n\n"
        "Instructions:\n"
        "1. Evaluate the strength of each side's arguments.\n"
        "2. Identify which arguments are most compelling and why.\n"
        "3. You MUST NOT default to the midpoint between bull and bear — "
        "that is intellectually lazy. Commit to a stance.\n"
        "4. If both sides have weak arguments, lean toward the base rate.\n"
        "5. Provide your final estimated probability.\n"
        "6. End with: PROBABILITY: [number between 0.0 and 1.0]\n"
    )

    judge_text = call_llm(judge_prompt)
    judge_prob = _parse_probability(judge_text)
    if judge_prob is None:
        logger.warning("Failed to parse judge probability from debate")
        return None

    return DebateResult(
        bull_probability=bull_prob,
        bear_probability=bear_prob,
        judge_probability=judge_prob,
        bull_argument=bull_text,
        bear_argument=bear_text,
        judge_reasoning=judge_text,
    )


def _parse_probability(text: str) -> float | None:
    """Extract a probability from LLM debate response.

    Looks for "PROBABILITY: X.XX" pattern first, falls back to last decimal found.
    """
    # Try structured format first
    match = re.search(r"PROBABILITY\s*:\s*(0(?:\.\d+)?|1(?:\.0+)?)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))

    # Fallback: last decimal number in valid range
    matches = re.findall(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", text)
    if matches:
        return float(matches[-1])

    return None
