"""Tests for PolymarketData CLI wrapper client."""

import json
import subprocess

import pytest

from polymarket_agent.data.client import PolymarketData

MOCK_MARKETS_JSON = json.dumps(
    [
        {
            "id": "100",
            "question": "Will it rain tomorrow?",
            "conditionId": "0xabc",
            "slug": "will-it-rain-tomorrow",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.6","0.4"]',
            "volume": "50000",
            "volume24hr": "1200",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "clobTokenIds": '["0xtok1","0xtok2"]',
        }
    ]
)

MOCK_EVENTS_JSON = json.dumps(
    [
        {
            "id": "200",
            "title": "Weather Events",
            "slug": "weather-events",
            "description": "Weather prediction markets",
            "active": True,
            "closed": False,
            "volume": "100000",
            "volume24hr": "5000",
            "liquidity": "20000",
            "markets": [],
        }
    ]
)

MOCK_BOOK_JSON = json.dumps(
    {
        "bids": [{"price": "0.55", "size": "100"}],
        "asks": [{"price": "0.65", "size": "200"}],
    }
)


def _mock_run(args, **kwargs):
    cmd = " ".join(args)
    result = subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
    if "markets list" in cmd:
        result.stdout = MOCK_MARKETS_JSON
    elif "events list" in cmd:
        result.stdout = MOCK_EVENTS_JSON
    elif "clob book" in cmd:
        result.stdout = MOCK_BOOK_JSON
    return result


@pytest.fixture
def client(mocker):
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    return PolymarketData()


def test_get_active_markets(client):
    markets = client.get_active_markets(limit=1)
    assert len(markets) == 1
    assert markets[0].id == "100"
    assert markets[0].question == "Will it rain tomorrow?"


def test_get_events(client):
    events = client.get_events(limit=1)
    assert len(events) == 1
    assert events[0].title == "Weather Events"


def test_get_orderbook(client):
    book = client.get_orderbook("0xtok1")
    assert book.best_bid == 0.55
    assert book.best_ask == 0.65


def test_cli_error_raises(mocker):
    def _fail(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="API error")

    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_fail)
    client = PolymarketData()
    with pytest.raises(RuntimeError, match="polymarket CLI failed"):
        client.get_active_markets()
