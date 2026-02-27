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
MOCK_MARKET_JSON = json.dumps(json.loads(MOCK_MARKETS_JSON)[0])

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

MOCK_LEADERBOARD_JSON = json.dumps(
    [
        {"name": "TopTrader", "volume": "500000", "pnl": "25000", "marketsTraded": "42"},
        {"name": "SecondPlace", "volume": "300000", "pnl": "15000", "marketsTraded": "30"},
    ]
)

MOCK_BOOK_JSON = json.dumps(
    {
        "bids": [{"price": "0.55", "size": "100"}],
        "asks": [{"price": "0.65", "size": "200"}],
    }
)

MOCK_EVENT_JSON = json.dumps(
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
)

MOCK_SPREAD_JSON = json.dumps({"spread": "0.10"})

MOCK_VOLUME_JSON = json.dumps(
    [
        {
            "markets": [
                {"market": "0xabc", "value": "70000"},
                {"market": "0xdef", "value": "30000"},
            ],
            "total": "100000",
        }
    ]
)

MOCK_POSITIONS_JSON = json.dumps(
    [
        {
            "market": "0xabc",
            "outcome": "Yes",
            "size": "50",
            "avgPrice": "0.40",
            "currentPrice": "0.60",
            "pnl": "10.0",
        },
        {
            "market": "0xdef",
            "outcome": "No",
            "size": "100",
            "avgPrice": "0.70",
            "currentPrice": "0.65",
            "pnl": "-5.0",
        },
    ]
)


def _mock_run(args, **kwargs):
    cmd = " ".join(args)
    result = subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
    if "markets list" in cmd:
        result.stdout = MOCK_MARKETS_JSON
    elif "markets get" in cmd:
        result.stdout = MOCK_MARKET_JSON
    elif "events get" in cmd:
        result.stdout = MOCK_EVENT_JSON
    elif "events list" in cmd:
        result.stdout = MOCK_EVENTS_JSON
    elif "clob spread" in cmd:
        result.stdout = MOCK_SPREAD_JSON
    elif "clob book" in cmd:
        result.stdout = MOCK_BOOK_JSON
    elif "data volume" in cmd:
        result.stdout = MOCK_VOLUME_JSON
    elif "data positions" in cmd:
        result.stdout = MOCK_POSITIONS_JSON
    elif "leaderboard" in cmd:
        result.stdout = MOCK_LEADERBOARD_JSON
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


def test_search_markets(client):
    """search_markets filters by keyword in question text."""
    results = client.search_markets("rain")
    assert len(results) == 1
    assert results[0].id == "100"


def test_search_markets_no_match(client):
    """search_markets returns empty list when no match found."""
    results = client.search_markets("xyz_nonexistent")
    assert len(results) == 0


def test_get_leaderboard(client):
    """get_leaderboard returns ranked Trader objects."""
    traders = client.get_leaderboard(period="month")
    assert len(traders) == 2
    assert traders[0].rank == 1
    assert traders[0].name == "TopTrader"
    assert traders[0].volume == 500000.0
    assert traders[1].rank == 2


def test_cli_error_raises(mocker):
    def _fail(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="API error")

    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_fail)
    client = PolymarketData()
    with pytest.raises(RuntimeError, match="polymarket CLI failed"):
        client.get_active_markets()


def test_cli_timeout_raises(mocker):
    """_run_cli raises RuntimeError on subprocess timeout."""
    mocker.patch(
        "polymarket_agent.data.client.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="polymarket", timeout=30),
    )
    client = PolymarketData()
    with pytest.raises(RuntimeError, match="timed out"):
        client.get_active_markets()


def test_get_event(client):
    """get_event returns a single Event by ID."""
    event = client.get_event("200")
    assert event is not None
    assert event.id == "200"
    assert event.title == "Weather Events"


def test_get_event_not_found(mocker):
    """get_event returns None when event is not found."""

    def _fail(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_fail)
    client = PolymarketData()
    assert client.get_event("999") is None


def test_get_spread(client):
    """get_spread returns Spread from clob spread CLI."""
    spread = client.get_spread("0xtok1")
    assert spread.token_id == "0xtok1"
    assert spread.spread == 0.10


def test_get_price(client):
    """get_price returns Spread with bid/ask derived from order book."""
    price = client.get_price("0xtok1")
    assert price.token_id == "0xtok1"
    assert price.bid == 0.55
    assert price.ask == 0.65
    assert price.spread == pytest.approx(0.10)


def test_get_volume(client):
    """get_volume returns total volume for an event."""
    volume = client.get_volume("200")
    assert volume.event_id == "200"
    assert volume.total == 100000.0


def test_get_positions(client):
    """get_positions returns Position list for an address."""
    positions = client.get_positions("0xdeadbeef")
    assert len(positions) == 2
    assert positions[0].market == "0xabc"
    assert positions[0].outcome == "Yes"
    assert positions[0].shares == 50.0
    assert positions[0].avg_price == 0.40
    assert positions[0].pnl == 10.0
    assert positions[1].market == "0xdef"
    assert positions[1].shares == 100.0


def test_get_positions_empty(mocker):
    """get_positions returns empty list for address with no positions."""

    def _empty(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")

    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_empty)
    client = PolymarketData()
    positions = client.get_positions("0x0000")
    assert positions == []


def test_get_positions_preserves_zero_values(mocker):
    """Position.from_cli should preserve explicit 0-valued fields without fallback."""

    zero_positions_json = json.dumps(
        [
            {
                "market": "0xzero",
                "outcome": "Yes",
                "size": "0",
                "shares": "123",  # should be ignored because size is explicitly present
                "avgPrice": "0",
                "avg_price": "0.42",  # should be ignored
                "currentPrice": "0",
                "current_price": "0.99",  # should be ignored
                "pnl": "0",
                "profit": "12.5",  # should be ignored
            }
        ]
    )

    def _zero_positions(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=zero_positions_json, stderr="")

    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_zero_positions)
    client = PolymarketData()
    positions = client.get_positions("0xzero")
    assert len(positions) == 1
    assert positions[0].shares == 0.0
    assert positions[0].avg_price == 0.0
    assert positions[0].current_price == 0.0
    assert positions[0].pnl == 0.0
