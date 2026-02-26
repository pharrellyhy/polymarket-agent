"""Tests for Pydantic data models."""

from polymarket_agent.data.models import Event, Market, OrderBook

SAMPLE_MARKET_JSON = {
    "id": "517310",
    "question": "Will Trump deport less than 250,000?",
    "conditionId": "0xaf9d0e448129a9f657f851d49495ba4742055d80e0ef1166ba0ee81d4d594214",
    "slug": "will-trump-deport-less-than-250000",
    "endDate": "2025-12-31T12:00:00Z",
    "liquidity": "17346.01768",
    "description": "This market will resolve to Yes if...",
    "outcomes": '["Yes","No"]',
    "outcomePrices": '["0.047","0.953"]',
    "volume": "1228511.944941",
    "active": True,
    "closed": False,
    "clobTokenIds": '["0xe0cb24200c550f33b8c0faffc6f500598ca6953137ba9077a55239fb858fc371",'
    '"0x92eae301a0617b6992cbcdb0d555d490b9539b387f9eef512339613ddce2eb4"]',
    "volume24hr": "10670.252910000008",
    "groupItemTitle": "<250k",
}

SAMPLE_EVENT_JSON = {
    "id": "16167",
    "ticker": "microstrategy-sell-any-bitcoin-in-2025",
    "slug": "microstrategy-sell-any-bitcoin-in-2025",
    "title": "MicroStrategy sells any Bitcoin by ___ ?",
    "description": "This market will resolve to Yes if...",
    "startDate": "2024-12-31T18:51:45.506005Z",
    "endDate": "2025-12-31T12:00:00Z",
    "active": True,
    "closed": False,
    "liquidity": "187791.88561",
    "volume": "20753924.165745",
    "volume24hr": "20717.72332",
    "markets": [],
}

SAMPLE_BOOK_JSON = {
    "asks": [
        {"price": "0.999", "size": "650.27"},
        {"price": "0.95", "size": "100"},
    ],
    "bids": [
        {"price": "0.04", "size": "500"},
        {"price": "0.03", "size": "1000"},
    ],
}


def test_market_from_cli_json():
    market = Market.from_cli(SAMPLE_MARKET_JSON)
    assert market.id == "517310"
    assert market.question == "Will Trump deport less than 250,000?"
    assert market.outcomes == ["Yes", "No"]
    assert market.outcome_prices == [0.047, 0.953]
    assert market.volume == 1228511.944941
    assert market.liquidity == 17346.01768
    assert market.active is True
    assert len(market.clob_token_ids) == 2
    assert market.volume_24h == 10670.252910000008


def test_event_from_cli_json():
    event = Event.from_cli(SAMPLE_EVENT_JSON)
    assert event.id == "16167"
    assert event.title == "MicroStrategy sells any Bitcoin by ___ ?"
    assert event.volume == 20753924.165745
    assert event.active is True


def test_orderbook_from_cli_json():
    book = OrderBook.from_cli(SAMPLE_BOOK_JSON)
    assert len(book.asks) == 2
    assert len(book.bids) == 2
    assert book.asks[0].price == 0.999
    assert book.asks[0].size == 650.27
    assert book.bids[0].price == 0.04
    assert book.best_ask == 0.95
    assert book.best_bid == 0.04
    assert book.midpoint == (0.95 + 0.04) / 2
    assert book.spread == 0.95 - 0.04


def test_market_handles_missing_optional_fields():
    minimal = {
        "id": "1",
        "question": "Test?",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.5","0.5"]',
        "volume": "100",
        "active": True,
        "closed": False,
    }
    market = Market.from_cli(minimal)
    assert market.id == "1"
    assert market.liquidity == 0.0
    assert market.volume_24h == 0.0
    assert market.clob_token_ids == []


def test_market_handles_null_optional_array_fields():
    market = Market.from_cli(
        {
            "id": "2",
            "question": "Null arrays?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.4","0.6"]',
            "volume": "100",
            "active": True,
            "closed": False,
            "clobTokenIds": None,
        }
    )
    assert market.clob_token_ids == []


def test_event_handles_null_markets_list():
    event = Event.from_cli(
        {
            "id": "9",
            "title": "Null markets",
            "active": True,
            "closed": False,
            "markets": None,
        }
    )
    assert event.markets == []
