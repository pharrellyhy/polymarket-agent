"""Tests for the DateCurveTrader strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import DateCurve, DateCurvePoint, Market
from polymarket_agent.strategies.date_curve_trader import (
    DateCurveTrader,
    _extract_base_question,
    _extract_date_from_question,
)


def _make_market(
    market_id: str = "100",
    question: str = "Will event happen?",
    yes_price: float = 0.5,
) -> Market:
    return Market.from_cli(
        {
            "id": market_id,
            "question": question,
            "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(yes_price), str(round(1 - yes_price, 4))]),
            "volume": "50000",
            "volume24hr": "10000",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "description": "Test market",
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
        }
    )


def _make_curve(prices: list[tuple[str, float]], base_question: str = "US forces enter Iran") -> DateCurve:
    """Build a DateCurve from (date, price) pairs."""
    points = [
        DateCurvePoint(
            date=d,
            market=_make_market(market_id=f"m_{d}", question=f"{base_question} by {d}?", yes_price=p),
            price=p,
        )
        for d, p in prices
    ]
    return DateCurve(base_question=base_question, points=points)


def _make_trader(**config_overrides: object) -> DateCurveTrader:
    """Create a DateCurveTrader with a mock client and sensible test defaults."""
    config: dict[str, object] = {"max_calls_per_hour": 100, **config_overrides}
    trader = DateCurveTrader()
    trader.configure(config)  # type: ignore[arg-type]
    trader._client = MagicMock()
    return trader


# ------------------------------------------------------------------
# Date extraction tests
# ------------------------------------------------------------------


def test_extract_date_from_question_month_day() -> None:
    result = _extract_date_from_question("US forces enter Iran by March 7?")
    assert result is not None
    assert result.endswith("-03-07")


def test_extract_date_from_question_with_year() -> None:
    result = _extract_date_from_question("Will X happen by January 15, 2026?")
    assert result == "2026-01-15"


def test_extract_date_from_question_before_pattern() -> None:
    result = _extract_date_from_question("Event before April 30?")
    assert result is not None
    assert "-04-30" in result


def test_extract_date_returns_none_for_no_date() -> None:
    assert _extract_date_from_question("Bitcoin above 100k?") is None


def test_extract_base_question() -> None:
    base = _extract_base_question("US forces enter Iran by March 7?")
    assert "US forces enter Iran" in base
    assert "March 7" not in base


# ------------------------------------------------------------------
# Term structure validation tests
# ------------------------------------------------------------------


def test_term_structure_no_signal_when_monotonic() -> None:
    """No arbitrage signals when curve is properly monotonic."""
    curve = _make_curve([
        ("2026-03-07", 0.05),
        ("2026-03-14", 0.12),
        ("2026-03-31", 0.28),
        ("2026-04-30", 0.45),
    ])
    trader = _make_trader()
    signals = trader._check_term_structure(curve)
    assert len(signals) == 0


def test_term_structure_signals_on_violation() -> None:
    """Monotonicity violation should produce buy signals (Yes + No) arbitrage signals."""
    curve = _make_curve([
        ("2026-03-07", 0.20),  # overpriced
        ("2026-03-14", 0.10),  # underpriced (violation!)
        ("2026-03-31", 0.28),
    ])
    trader = _make_trader()
    signals = trader._check_term_structure(curve)
    assert len(signals) == 2
    # Both are buy signals: buy Yes on underpriced later date, buy No on overpriced earlier date
    assert all(s.side == "buy" for s in signals)
    # One should be the No token (on the overpriced earlier date)
    no_signals = [s for s in signals if s.token_id.endswith("_no")]
    assert len(no_signals) == 1
    for s in signals:
        assert s.confidence == 0.9
        assert "term_structure_arb" in s.reason


def test_term_structure_tolerance_ignores_tiny_violations() -> None:
    """Violations within 0.5% tolerance should not trigger signals."""
    curve = _make_curve([
        ("2026-03-07", 0.100),
        ("2026-03-14", 0.098),  # only 0.2% violation
    ])
    trader = _make_trader()
    signals = trader._check_term_structure(curve)
    assert len(signals) == 0


# ------------------------------------------------------------------
# Curve detection (regex fallback) tests
# ------------------------------------------------------------------


def test_detect_curves_regex_groups_related_markets() -> None:
    """Regex detection should group markets by shared base question."""
    markets = [
        _make_market("m1", "US forces enter Iran by March 7?", 0.05),
        _make_market("m2", "US forces enter Iran by March 14?", 0.12),
        _make_market("m3", "US forces enter Iran by March 31?", 0.28),
        _make_market("m4", "Bitcoin above 100k?", 0.50),
    ]
    trader = _make_trader()
    # Force regex fallback by not having a client
    trader._client = None
    curves = trader._detect_curves_regex(markets)
    assert len(curves) == 1
    assert len(curves[0].points) == 3
    # Points should be sorted chronologically
    dates = [p.date for p in curves[0].points]
    assert dates == sorted(dates)


def test_detect_curves_regex_ignores_single_markets() -> None:
    """A single date market should not form a curve."""
    markets = [
        _make_market("m1", "US forces enter Iran by March 7?", 0.05),
        _make_market("m2", "Bitcoin above 100k?", 0.50),
    ]
    trader = _make_trader()
    curves = trader._detect_curves_regex(markets)
    assert len(curves) == 0


def test_detect_curves_regex_base_question_handles_case_variants() -> None:
    """Base question extraction should not depend on lowercase 'by/before' tokens."""
    markets = [
        _make_market("m1", "Will event happen BY March 7?", 0.05),
        _make_market("m2", "Will event happen BY March 14?", 0.12),
    ]
    trader = _make_trader()
    curves = trader._detect_curves_regex(markets)
    assert len(curves) == 1
    assert curves[0].base_question == "Will event happen"


# ------------------------------------------------------------------
# LLM curve detection parsing tests
# ------------------------------------------------------------------


def test_parse_curve_detection_response_valid() -> None:
    """Valid JSON response should produce DateCurve objects."""
    markets = [
        _make_market("abc", "Event by March 7?", 0.05),
        _make_market("def", "Event by March 14?", 0.12),
    ]
    trader = _make_trader()
    response = json.dumps([{
        "base_question": "Event",
        "markets": [
            {"id": "abc", "date": "2026-03-07"},
            {"id": "def", "date": "2026-03-14"},
        ],
    }])
    curves = trader._parse_curve_detection_response(response, markets)
    assert len(curves) == 1
    assert len(curves[0].points) == 2
    assert curves[0].points[0].date < curves[0].points[1].date


def test_parse_curve_detection_response_markdown_wrapped() -> None:
    """Should handle JSON wrapped in markdown code blocks."""
    markets = [
        _make_market("a1", "Q by March 1?", 0.10),
        _make_market("a2", "Q by March 15?", 0.20),
    ]
    trader = _make_trader()
    response = '```json\n[{"base_question": "Q", "markets": [{"id": "a1", "date": "2026-03-01"}, {"id": "a2", "date": "2026-03-15"}]}]\n```'
    curves = trader._parse_curve_detection_response(response, markets)
    assert len(curves) == 1


def test_parse_curve_detection_response_invalid_json() -> None:
    """Invalid JSON should return empty list."""
    trader = _make_trader()
    curves = trader._parse_curve_detection_response("not json at all", [])
    assert curves == []


def test_parse_curve_detection_response_unknown_market_id() -> None:
    """Unknown market IDs should be skipped."""
    markets = [_make_market("abc", "Event by March 7?", 0.05)]
    trader = _make_trader()
    response = json.dumps([{
        "base_question": "Event",
        "markets": [
            {"id": "abc", "date": "2026-03-07"},
            {"id": "unknown_id", "date": "2026-03-14"},
        ],
    }])
    # Only 1 valid market, so < 2 points → no curve
    curves = trader._parse_curve_detection_response(response, markets)
    assert len(curves) == 0


# ------------------------------------------------------------------
# Curve analysis parsing tests
# ------------------------------------------------------------------


def test_parse_curve_analysis_generates_divergence_signals() -> None:
    """LLM estimates diverging from market prices should produce signals."""
    curve = _make_curve([
        ("2026-03-07", 0.05),
        ("2026-03-14", 0.12),
        ("2026-03-31", 0.28),
    ])
    trader = _make_trader(min_divergence=0.10)
    # Note: Platt scaling extremizes LLM estimates, so use larger raw
    # probabilities to ensure post-scaling divergence exceeds min_divergence
    response = json.dumps({
        "estimates": [
            {"date": "2026-03-07", "probability": 0.05},   # no divergence
            {"date": "2026-03-14", "probability": 0.45},   # large divergence from 0.12
            {"date": "2026-03-31", "probability": 0.65},   # large divergence from 0.28
        ]
    })
    signals = trader._parse_curve_analysis(response, curve)
    assert len(signals) == 2  # only the two divergent points
    for s in signals:
        assert s.side == "buy"  # LLM prices are higher
        assert "curve_divergence" in s.reason


def test_parse_curve_analysis_buy_no_on_negative_divergence() -> None:
    """LLM estimate below market price should buy No token."""
    curve = _make_curve([
        ("2026-03-07", 0.50),
        ("2026-03-14", 0.60),
    ])
    trader = _make_trader(min_divergence=0.10)
    response = json.dumps({
        "estimates": [
            {"date": "2026-03-07", "probability": 0.30},  # -0.20 divergence
            {"date": "2026-03-14", "probability": 0.55},  # small divergence
        ]
    })
    signals = trader._parse_curve_analysis(response, curve)
    assert len(signals) >= 1
    # Negative divergence should produce buy signals on No token
    no_signals = [s for s in signals if s.token_id.endswith("_no")]
    assert len(no_signals) >= 1
    assert all(s.side == "buy" for s in no_signals)


def test_parse_curve_analysis_invalid_json() -> None:
    """Invalid response should return empty list."""
    curve = _make_curve([("2026-03-07", 0.10), ("2026-03-14", 0.20)])
    trader = _make_trader()
    signals = trader._parse_curve_analysis("not valid json", curve)
    assert signals == []


# ------------------------------------------------------------------
# Platt scaling tests
# ------------------------------------------------------------------


def test_extremize_moves_moderate_probabilities_toward_extremes() -> None:
    """Platt scaling should push 0.6 toward 1.0 and 0.3 toward 0.0."""
    assert DateCurveTrader._extremize(0.6) > 0.6
    assert DateCurveTrader._extremize(0.3) < 0.3


def test_extremize_preserves_boundaries() -> None:
    """Platt scaling should not change 0.0, 1.0, or 0.5."""
    assert DateCurveTrader._extremize(0.0) == 0.0
    assert DateCurveTrader._extremize(1.0) == 1.0
    assert abs(DateCurveTrader._extremize(0.5) - 0.5) < 0.001


# ------------------------------------------------------------------
# Full analyze() integration tests
# ------------------------------------------------------------------


def test_analyze_runs_structural_checks_without_client() -> None:
    """Strategy should run structural checks even without LLM client."""
    trader = DateCurveTrader()
    trader._client = None
    # With no markets, should return empty (no curves to detect)
    signals = trader.analyze([], MagicMock())
    assert signals == []


def test_analyze_produces_arb_signals_without_llm() -> None:
    """Term structure violations should produce signals even without LLM curve analysis."""
    markets = [
        _make_market("m1", "Event by March 7?", 0.30),   # overpriced
        _make_market("m2", "Event by March 14?", 0.10),   # underpriced (violation)
        _make_market("m3", "Event by March 31?", 0.28),
    ]
    trader = _make_trader()
    # Disable LLM calls for curve analysis (keep only term structure)
    trader._max_calls_per_hour = 0
    # Pre-populate curve cache to skip LLM-based detection
    curve = trader._detect_curves_regex(markets)
    trader._curve_cache = (curve, float("inf"))  # never expires

    signals = trader.analyze(markets, MagicMock())
    assert any("term_structure_arb" in s.reason for s in signals)


def test_cache_reprice_updates_prices() -> None:
    """Cached curves should get repriced with current market data."""
    trader = _make_trader()
    original_curve = _make_curve([("2026-03-07", 0.10), ("2026-03-14", 0.20)])

    updated_markets = [
        _make_market("m_2026-03-07", "US forces enter Iran by 2026-03-07?", 0.15),
        _make_market("m_2026-03-14", "US forces enter Iran by 2026-03-14?", 0.25),
    ]

    repriced = trader._reprice_cached_curves([original_curve], updated_markets)
    assert len(repriced) == 1
    assert repriced[0].points[0].price == 0.15
    assert repriced[0].points[1].price == 0.25
