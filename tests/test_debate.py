"""Tests for adversarial debate module."""

from polymarket_agent.strategies.debate import DebateResult, _parse_probability, run_debate


def test_parse_probability_structured() -> None:
    """Parses PROBABILITY: X.XX format."""
    assert _parse_probability("Some reasoning\nPROBABILITY: 0.75") == 0.75


def test_parse_probability_case_insensitive() -> None:
    assert _parse_probability("probability: 0.60") == 0.60


def test_parse_probability_fallback_last_decimal() -> None:
    """Falls back to last decimal when no PROBABILITY: marker."""
    assert _parse_probability("I think 0.5 but maybe 0.7") == 0.7


def test_parse_probability_returns_none_on_failure() -> None:
    assert _parse_probability("No numbers here") is None


def test_run_debate_with_mocked_llm() -> None:
    """Full debate flow with mocked LLM responses."""
    call_count = 0

    def mock_llm(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # Bull
            return "Strong arguments for Yes.\nPROBABILITY: 0.80"
        if call_count == 2:  # Bear
            return "Strong arguments against.\nPROBABILITY: 0.30"
        # Judge
        return "The bull case is stronger.\nPROBABILITY: 0.65"

    result = run_debate(
        market_question="Will X happen?",
        market_description="Test market",
        current_price=0.50,
        call_llm=mock_llm,
    )

    assert result is not None
    assert isinstance(result, DebateResult)
    assert result.bull_probability == 0.80
    assert result.bear_probability == 0.30
    assert result.judge_probability == 0.65
    assert call_count == 3


def test_run_debate_returns_none_on_parse_failure() -> None:
    """Returns None if any participant's response can't be parsed."""
    def mock_llm(prompt: str) -> str:
        return "No probability here at all"

    result = run_debate(
        market_question="Will X happen?",
        market_description="Test market",
        current_price=0.50,
        call_llm=mock_llm,
    )
    assert result is None


def test_run_debate_with_context_sections() -> None:
    """Context sections are passed through to prompts."""
    prompts_received: list[str] = []

    def mock_llm(prompt: str) -> str:
        prompts_received.append(prompt)
        return "PROBABILITY: 0.50"

    run_debate(
        market_question="Test?",
        market_description="Desc",
        current_price=0.50,
        call_llm=mock_llm,
        context_sections="--- TECHNICAL ANALYSIS ---\nPrice trending up",
    )

    # All three prompts should contain the context
    for prompt in prompts_received:
        assert "TECHNICAL ANALYSIS" in prompt


def test_debate_result_fields() -> None:
    """DebateResult stores all fields correctly."""
    result = DebateResult(
        bull_probability=0.8,
        bear_probability=0.3,
        judge_probability=0.65,
        bull_argument="bull text",
        bear_argument="bear text",
        judge_reasoning="judge text",
    )
    assert result.bull_probability == 0.8
    assert result.bear_probability == 0.3
    assert result.judge_probability == 0.65
