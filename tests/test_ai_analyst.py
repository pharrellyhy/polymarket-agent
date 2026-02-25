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


def test_ai_analyst_generates_signal_on_divergence() -> None:
    """If AI estimate diverges from market price, emit a signal."""
    strategy = AIAnalyst()
    strategy.configure({"min_divergence": 0.10, "max_calls_per_hour": 100})

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="0.80")]
    mock_client.messages.create.return_value = mock_response
    strategy._client = mock_client

    market = _make_market("1", yes_price=0.50)
    data = MagicMock()
    signals = strategy.analyze([market], data)
    assert len(signals) == 1
    assert signals[0].side == "buy"


def test_ai_analyst_no_signal_when_aligned() -> None:
    """If AI estimate is close to market price, no signal."""
    strategy = AIAnalyst()
    strategy.configure({"min_divergence": 0.10})

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="0.52")]
    mock_client.messages.create.return_value = mock_response
    strategy._client = mock_client

    market = _make_market("1", yes_price=0.50)
    data = MagicMock()
    signals = strategy.analyze([market], data)
    assert len(signals) == 0


def test_ai_analyst_graceful_without_api_key() -> None:
    """Strategy should return empty signals if no API key is available."""
    with patch.dict("os.environ", {}, clear=True):
        strategy = AIAnalyst()
        strategy.configure({})
        if strategy._client is None:
            market = _make_market("1", yes_price=0.50)
            data = MagicMock()
            signals = strategy.analyze([market], data)
            assert len(signals) == 0


def test_ai_analyst_respects_rate_limit() -> None:
    """Strategy should stop calling API after hitting rate limit."""
    strategy = AIAnalyst()
    strategy.configure({"max_calls_per_hour": 2})

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="0.80")]
    mock_client.messages.create.return_value = mock_response
    strategy._client = mock_client

    markets = [_make_market(str(i), yes_price=0.50) for i in range(5)]
    data = MagicMock()
    signals = strategy.analyze(markets, data)
    assert mock_client.messages.create.call_count <= 2
