"""Tests for external prediction market API clients."""

import json
from typing import Any

from polymarket_agent.data.external_prices import KalshiClient, MetaculusClient


class _DummyResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def __enter__(self) -> "_DummyResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def test_kalshi_fetch_prices_skips_invalid_yes_ask(monkeypatch) -> None:
    """Invalid rows should be skipped while valid rows are still parsed."""
    payload = {
        "events": [
            {
                "title": "Event title",
                "markets": [
                    {"title": "Bad row", "yes_ask": "invalid", "ticker": "BAD"},
                    {"title": "Good row", "yes_ask": "62", "ticker": "GOOD"},
                ],
            }
        ]
    }

    monkeypatch.setattr(
        "polymarket_agent.data.external_prices.urllib.request.urlopen",
        lambda req, timeout: _DummyResponse(payload),
    )

    prices = KalshiClient()._fetch_prices("https://example.test")
    assert len(prices) == 1
    assert prices[0].question == "Good row"
    assert prices[0].probability == 0.62


def test_metaculus_fetch_prices_skips_invalid_median(monkeypatch) -> None:
    """Malformed median values should not drop valid rows."""
    payload = {
        "results": [
            {"id": 1, "title": "Bad row", "community_prediction": {"full": {"q2": "invalid"}}},
            {
                "id": 2,
                "title": "Good row",
                "community_prediction": {"full": {"q2": 0.35}},
                "last_activity_time": "2026-03-02T00:00:00Z",
            },
        ]
    }

    monkeypatch.setattr(
        "polymarket_agent.data.external_prices.urllib.request.urlopen",
        lambda req, timeout: _DummyResponse(payload),
    )

    prices = MetaculusClient()._fetch_prices("https://example.test")
    assert len(prices) == 1
    assert prices[0].question == "Good row"
    assert prices[0].probability == 0.35
