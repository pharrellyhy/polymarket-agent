"""Tests for the AIAnalyst strategy."""

import json
from unittest.mock import MagicMock, patch

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.ai_analyst import AIAnalyst


def _make_market(market_id: str = "100", yes_price: float = 0.5) -> Market:
    return Market.from_cli(
        {
            "id": market_id,
            "question": f"Will event {market_id} happen?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(yes_price), str(round(1 - yes_price, 4))]),
            "volume": "50000",
            "volume24hr": "10000",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "description": "This is a test market about whether an event will happen.",
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
        }
    )


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=response_text)]
    client.messages.create.return_value = response
    return client


def _make_analyst(response_text: str, **config_overrides: object) -> AIAnalyst:
    """Create an AIAnalyst with a mock client and sensible test defaults."""
    config: dict[str, object] = {"min_divergence": 0.10, "max_calls_per_hour": 100, **config_overrides}
    strategy = AIAnalyst()
    strategy.configure(config)  # type: ignore[arg-type]
    strategy._client = _mock_client(response_text)
    return strategy


def test_ai_analyst_generates_signal_on_divergence() -> None:
    """If AI estimate diverges from market price, emit a signal."""
    strategy = _make_analyst("0.80")
    signals = strategy.analyze([_make_market("1", yes_price=0.50)], MagicMock())
    assert len(signals) == 1
    assert signals[0].side == "buy"


def test_ai_analyst_no_signal_when_aligned() -> None:
    """If AI estimate is close to market price, no signal."""
    strategy = _make_analyst("0.52")
    signals = strategy.analyze([_make_market("1", yes_price=0.50)], MagicMock())
    assert len(signals) == 0


def test_ai_analyst_graceful_without_api_key() -> None:
    """Strategy should return empty signals if no API key is available."""
    with patch.dict("os.environ", {}, clear=True):
        strategy = AIAnalyst()
        assert strategy._client is None
        signals = strategy.analyze([_make_market("1", yes_price=0.50)], MagicMock())
        assert len(signals) == 0


def test_ai_analyst_generates_sell_signal_on_negative_divergence() -> None:
    """If AI estimate is below market price, emit a sell signal."""
    strategy = _make_analyst("0.20")
    signals = strategy.analyze([_make_market("1", yes_price=0.50)], MagicMock())
    assert len(signals) == 1
    assert signals[0].side == "sell"
    assert signals[0].token_id == "0xtok_1_yes"


def test_ai_analyst_handles_unparseable_response() -> None:
    """If AI returns text that cannot be parsed, no signal is emitted."""
    strategy = _make_analyst("I cannot estimate this")
    signals = strategy.analyze([_make_market("1", yes_price=0.50)], MagicMock())
    assert len(signals) == 0


def test_ai_analyst_handles_api_exception() -> None:
    """If the API call raises an exception, no signal and no crash."""
    strategy = AIAnalyst()
    strategy.configure({"min_divergence": 0.10, "max_calls_per_hour": 100})
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("API down")
    strategy._client = client

    signals = strategy.analyze([_make_market("1", yes_price=0.50)], MagicMock())
    assert len(signals) == 0


def test_ai_analyst_respects_rate_limit() -> None:
    """Strategy should stop calling API after hitting rate limit."""
    strategy = _make_analyst("0.80", max_calls_per_hour=2)
    markets = [_make_market(str(i), yes_price=0.50) for i in range(5)]
    strategy.analyze(markets, MagicMock())
    assert strategy._client.messages.create.call_count <= 2


def test_ai_analyst_counts_unparseable_responses_toward_rate_limit() -> None:
    """Unparseable API responses still count toward the hourly rate limit."""
    strategy = _make_analyst("not a probability", max_calls_per_hour=2)
    markets = [_make_market(str(i), yes_price=0.50) for i in range(5)]
    strategy.analyze(markets, MagicMock())
    assert strategy._client.messages.create.call_count <= 2
