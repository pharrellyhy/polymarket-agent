"""Tests for the SportsDerivativeTrader strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import Market, SportsEventGraph, SportsMarketNode
from polymarket_agent.strategies.sports_derivative_trader import (
    SportsDerivativeTrader,
    _classify_market_type_regex,
    _is_resolved_price,
)


def _make_market(
    market_id: str = "100",
    question: str = "Will the Lakers win the NBA Championship?",
    yes_price: float = 0.5,
    volume_24h: float = 1000.0,
    one_day_price_change: float = 0.0,
) -> Market:
    return Market.from_cli(
        {
            "id": market_id,
            "question": question,
            "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(yes_price), str(round(1 - yes_price, 4))]),
            "volume": "50000",
            "volume24hr": str(volume_24h),
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "description": "Test sports market",
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
            "oneDayPriceChange": str(one_day_price_change),
        }
    )


def _make_node(
    market: Market,
    market_type: str = "championship",
    team_or_player: str = "Lakers",
    is_resolved: bool = False,
) -> SportsMarketNode:
    return SportsMarketNode(
        market=market,
        market_type=market_type,
        team_or_player=team_or_player,
        is_resolved=is_resolved,
    )


def _make_graph(
    nodes: list[SportsMarketNode],
    sport: str = "nba",
    event_label: str = "NBA Playoffs 2026",
) -> SportsEventGraph:
    return SportsEventGraph(sport=sport, event_label=event_label, nodes=nodes)


def _make_trader(**config_overrides: object) -> SportsDerivativeTrader:
    """Create a SportsDerivativeTrader with a mock client and sensible test defaults."""
    config: dict[str, object] = {"max_calls_per_hour": 100, **config_overrides}
    trader = SportsDerivativeTrader()
    trader.configure(config)  # type: ignore[arg-type]
    trader._client = MagicMock()
    return trader


# ------------------------------------------------------------------
# Regex classification tests
# ------------------------------------------------------------------


def test_classify_game() -> None:
    assert _classify_market_type_regex("Lakers vs Celtics Game 3") == "game"


def test_classify_series() -> None:
    assert _classify_market_type_regex("Will Lakers win the Western Conference Finals?") == "series"
    assert _classify_market_type_regex("Will Celtics win the series?") == "series"


def test_classify_championship() -> None:
    assert _classify_market_type_regex("Will Lakers win the NBA title?") == "championship"
    assert _classify_market_type_regex("Who will win the Super Bowl?") == "championship"


def test_classify_player_prop() -> None:
    assert _classify_market_type_regex("Will LeBron win MVP?") == "player_prop"
    assert _classify_market_type_regex("Who will be the scoring leader?") == "player_prop"


def test_is_resolved_price() -> None:
    assert _is_resolved_price(0.01) is True
    assert _is_resolved_price(0.99) is True
    assert _is_resolved_price(0.50) is False
    assert _is_resolved_price(0.03) is False


# ------------------------------------------------------------------
# Bracket sum validation tests
# ------------------------------------------------------------------


def test_bracket_sum_overpriced() -> None:
    """Sum > 1.0 should produce sell signal on most overpriced candidate."""
    nodes = [
        _make_node(_make_market("m1", "Lakers win championship?", 0.30), team_or_player="Lakers"),
        _make_node(_make_market("m2", "Celtics win championship?", 0.35), team_or_player="Celtics"),
        _make_node(_make_market("m3", "Thunder win championship?", 0.25), team_or_player="Thunder"),
        _make_node(_make_market("m4", "Nuggets win championship?", 0.22), team_or_player="Nuggets"),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_bracket_sum(graph)

    assert len(signals) == 1
    assert signals[0].side == "sell"
    assert signals[0].market_id == "m2"  # Celtics at 0.35 is highest
    assert "bracket_sum" in signals[0].reason


def test_bracket_sum_underpriced() -> None:
    """Sum < 1.0 should produce buy signal on most underpriced candidate."""
    nodes = [
        _make_node(_make_market("m1", "Lakers win championship?", 0.15), team_or_player="Lakers"),
        _make_node(_make_market("m2", "Celtics win championship?", 0.20), team_or_player="Celtics"),
        _make_node(_make_market("m3", "Thunder win championship?", 0.18), team_or_player="Thunder"),
        _make_node(_make_market("m4", "Nuggets win championship?", 0.10), team_or_player="Nuggets"),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_bracket_sum(graph)

    assert len(signals) == 1
    assert signals[0].side == "buy"
    assert signals[0].market_id == "m4"  # Nuggets at 0.10 is lowest
    assert "bracket_sum" in signals[0].reason


def test_bracket_sum_within_tolerance() -> None:
    """Sum near 1.0 (within tolerance) should produce no signals."""
    nodes = [
        _make_node(_make_market("m1", "Lakers win championship?", 0.25), team_or_player="Lakers"),
        _make_node(_make_market("m2", "Celtics win championship?", 0.25), team_or_player="Celtics"),
        _make_node(_make_market("m3", "Thunder win championship?", 0.25), team_or_player="Thunder"),
        _make_node(_make_market("m4", "Nuggets win championship?", 0.25), team_or_player="Nuggets"),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_bracket_sum(graph)
    assert len(signals) == 0


def test_bracket_sum_skips_resolved() -> None:
    """Resolved markets should be excluded from sum calculation."""
    nodes = [
        _make_node(_make_market("m1", "Lakers win championship?", 0.30), team_or_player="Lakers"),
        _make_node(_make_market("m2", "Celtics win championship?", 0.35), team_or_player="Celtics"),
        _make_node(_make_market("m3", "Thunder win championship?", 0.25), team_or_player="Thunder"),
        _make_node(_make_market("m4", "Nuggets win championship?", 0.99), team_or_player="Nuggets", is_resolved=True),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_bracket_sum(graph)
    # Only 3 active markets: 0.30 + 0.35 + 0.25 = 0.90 < 1.0
    assert all("bracket_sum" in s.reason for s in signals)


def test_bracket_sum_needs_at_least_3() -> None:
    """Fewer than 3 siblings should not trigger bracket sum check."""
    nodes = [
        _make_node(_make_market("m1", "Lakers win championship?", 0.60), team_or_player="Lakers"),
        _make_node(_make_market("m2", "Celtics win championship?", 0.60), team_or_player="Celtics"),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_bracket_sum(graph)
    assert len(signals) == 0


# ------------------------------------------------------------------
# Hierarchy consistency tests
# ------------------------------------------------------------------


def test_hierarchy_violation_produces_signals() -> None:
    """Championship price > series price should produce buy-series + sell-championship."""
    nodes = [
        _make_node(
            _make_market("champ1", "Lakers win NBA title?", 0.40),
            market_type="championship", team_or_player="Lakers",
        ),
        _make_node(
            _make_market("series1", "Lakers win WCF?", 0.30),
            market_type="series", team_or_player="Lakers",
        ),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_hierarchy_consistency(graph)

    assert len(signals) == 2
    sides = {s.side for s in signals}
    assert sides == {"buy", "sell"}
    buy_signal = [s for s in signals if s.side == "buy"][0]
    sell_signal = [s for s in signals if s.side == "sell"][0]
    assert buy_signal.market_id == "series1"
    assert sell_signal.market_id == "champ1"
    assert "hierarchy" in buy_signal.reason


def test_hierarchy_no_violation() -> None:
    """Championship < series should produce no signals."""
    nodes = [
        _make_node(
            _make_market("champ1", "Lakers win NBA title?", 0.20),
            market_type="championship", team_or_player="Lakers",
        ),
        _make_node(
            _make_market("series1", "Lakers win WCF?", 0.40),
            market_type="series", team_or_player="Lakers",
        ),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_hierarchy_consistency(graph)
    assert len(signals) == 0


def test_hierarchy_tolerance() -> None:
    """Championship barely above series (within 1% tolerance) should not trigger."""
    nodes = [
        _make_node(
            _make_market("champ1", "Lakers win NBA title?", 0.305),
            market_type="championship", team_or_player="Lakers",
        ),
        _make_node(
            _make_market("series1", "Lakers win WCF?", 0.30),
            market_type="series", team_or_player="Lakers",
        ),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_hierarchy_consistency(graph)
    assert len(signals) == 0


# ------------------------------------------------------------------
# Cascade signal detection tests
# ------------------------------------------------------------------


def test_cascade_detects_lagging_derivative() -> None:
    """Sharp game move with minimal derivative response should produce signal."""
    game_market = _make_market(
        "game1", "Lakers vs Nuggets Game 5?", 0.80, one_day_price_change=0.25,
    )
    series_market = _make_market(
        "series1", "Lakers win WCF?", 0.50, one_day_price_change=0.02,
    )
    nodes = [
        _make_node(game_market, market_type="game", team_or_player="Lakers"),
        _make_node(series_market, market_type="series", team_or_player="Lakers"),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_cascade_signals(graph)

    assert len(signals) >= 1
    assert signals[0].side == "buy"  # game went up, so derivative should too
    assert "cascade" in signals[0].reason


def test_cascade_no_signal_when_derivative_moved() -> None:
    """If derivative already adjusted, no cascade signal."""
    game_market = _make_market(
        "game1", "Lakers vs Nuggets Game 5?", 0.80, one_day_price_change=0.25,
    )
    series_market = _make_market(
        "series1", "Lakers win WCF?", 0.60, one_day_price_change=0.15,
    )
    nodes = [
        _make_node(game_market, market_type="game", team_or_player="Lakers"),
        _make_node(series_market, market_type="series", team_or_player="Lakers"),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_cascade_signals(graph)
    # Derivative moved 0.15 > 0.25 * 0.3 = 0.075, so no cascade
    assert len(signals) == 0


def test_cascade_skips_below_min_move() -> None:
    """Game moves below cascade_min_move should not trigger cascade analysis."""
    game_market = _make_market(
        "game1", "Lakers vs Nuggets Game 5?", 0.55, one_day_price_change=0.05,
    )
    series_market = _make_market(
        "series1", "Lakers win WCF?", 0.50, one_day_price_change=0.01,
    )
    nodes = [
        _make_node(game_market, market_type="game", team_or_player="Lakers"),
        _make_node(series_market, market_type="series", team_or_player="Lakers"),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._check_cascade_signals(graph)
    assert len(signals) == 0


# ------------------------------------------------------------------
# LLM derivative analysis parsing tests
# ------------------------------------------------------------------


def test_parse_derivative_analysis_valid() -> None:
    """Valid LLM response should produce divergence signals."""
    nodes = [
        _make_node(
            _make_market("m1", "Lakers win NBA title?", 0.12),
            market_type="championship", team_or_player="Lakers",
        ),
        _make_node(
            _make_market("m2", "Celtics win NBA title?", 0.25),
            market_type="championship", team_or_player="Celtics",
        ),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader(min_divergence=0.08)

    # LLM thinks Lakers are underpriced
    response = json.dumps({
        "estimates": [
            {"market_id": "m1", "probability": 0.45},  # big divergence from 0.12
            {"market_id": "m2", "probability": 0.28},  # small divergence from 0.25
        ]
    })
    signals = trader._parse_derivative_analysis(response, graph)
    assert len(signals) >= 1
    # At least one signal for m1 (large divergence)
    m1_signals = [s for s in signals if s.market_id == "m1"]
    assert len(m1_signals) == 1
    assert m1_signals[0].side == "buy"
    assert "derivative_divergence" in m1_signals[0].reason


def test_parse_derivative_analysis_invalid_json() -> None:
    """Invalid response should return empty list."""
    nodes = [
        _make_node(_make_market("m1", "Lakers win NBA title?", 0.12), team_or_player="Lakers"),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader()
    signals = trader._parse_derivative_analysis("not valid json", graph)
    assert signals == []


def test_parse_derivative_analysis_skips_invalid_probability_entries() -> None:
    """Malformed probability entries should be ignored, not crash the parse."""
    nodes = [
        _make_node(
            _make_market("m1", "Lakers win NBA title?", 0.12),
            market_type="championship", team_or_player="Lakers",
        ),
        _make_node(
            _make_market("m2", "Celtics win NBA title?", 0.25),
            market_type="championship", team_or_player="Celtics",
        ),
    ]
    graph = _make_graph(nodes)
    trader = _make_trader(min_divergence=0.08)

    response = json.dumps({
        "estimates": [
            {"market_id": "m1", "probability": "not-a-number"},  # malformed
            {"market_id": "m2", "probability": 0.45},  # valid
        ]
    })

    signals = trader._parse_derivative_analysis(response, graph)
    m2_signals = [s for s in signals if s.market_id == "m2"]
    assert len(m2_signals) == 1
    assert m2_signals[0].side == "buy"


# ------------------------------------------------------------------
# Event graph LLM parsing tests
# ------------------------------------------------------------------


def test_parse_event_graph_response_valid() -> None:
    """Valid JSON response should produce SportsEventGraph objects."""
    markets = [
        _make_market("abc", "Lakers win NBA title?", 0.15),
        _make_market("def", "Lakers win WCF?", 0.35),
    ]
    trader = _make_trader()
    response = json.dumps([{
        "event_label": "NBA Playoffs 2026",
        "sport": "nba",
        "markets": [
            {"id": "abc", "type": "championship", "team_or_player": "Lakers"},
            {"id": "def", "type": "series", "team_or_player": "Lakers"},
        ],
    }])
    graphs = trader._parse_event_graph_response(response, markets)
    assert len(graphs) == 1
    assert len(graphs[0].nodes) == 2
    assert graphs[0].sport == "nba"


def test_parse_event_graph_response_invalid_json() -> None:
    """Invalid JSON should return empty list."""
    trader = _make_trader()
    graphs = trader._parse_event_graph_response("not json", [])
    assert graphs == []


def test_parse_event_graph_response_unknown_market_id() -> None:
    """Unknown market IDs should be skipped."""
    markets = [_make_market("abc", "Lakers win NBA title?", 0.15)]
    trader = _make_trader()
    response = json.dumps([{
        "event_label": "NBA Playoffs 2026",
        "sport": "nba",
        "markets": [
            {"id": "abc", "type": "championship", "team_or_player": "Lakers"},
            {"id": "unknown", "type": "series", "team_or_player": "Lakers"},
        ],
    }])
    graphs = trader._parse_event_graph_response(response, markets)
    # Only 1 valid market, < 2 nodes → no graph
    assert len(graphs) == 0


# ------------------------------------------------------------------
# Market identification tests
# ------------------------------------------------------------------


def test_identify_sports_markets() -> None:
    """Should filter to active sports markets with volume."""
    trader = _make_trader()
    markets = [
        _make_market("s1", "Lakers win NBA Championship?", 0.20, volume_24h=500),
        _make_market("p1", "Will Trump win election?", 0.60, volume_24h=500),
        _make_market("s2", "Lakers win NBA playoff series?", 0.40, volume_24h=50),  # low volume
    ]
    result = trader._identify_sports_markets(markets)
    assert len(result) == 1
    assert result[0].id == "s1"


# ------------------------------------------------------------------
# Full analyze() integration tests
# ------------------------------------------------------------------


def test_analyze_returns_empty_when_no_client() -> None:
    """Strategy should gracefully return empty when no LLM client."""
    trader = SportsDerivativeTrader()
    trader._client = None
    signals = trader.analyze([], MagicMock())
    assert signals == []


def test_analyze_returns_empty_for_non_sports() -> None:
    """No sports markets should produce empty signals."""
    trader = _make_trader()
    markets = [
        _make_market("p1", "Will Trump win election?", 0.60, volume_24h=5000),
    ]
    signals = trader.analyze(markets, MagicMock())
    assert signals == []


def test_extremize_moves_moderate_probabilities() -> None:
    """Platt scaling should push 0.6 toward 1.0 and 0.3 toward 0.0."""
    assert SportsDerivativeTrader._extremize(0.6) > 0.6
    assert SportsDerivativeTrader._extremize(0.3) < 0.3
    assert SportsDerivativeTrader._extremize(0.0) == 0.0
    assert SportsDerivativeTrader._extremize(1.0) == 1.0
    assert abs(SportsDerivativeTrader._extremize(0.5) - 0.5) < 0.001


def test_cache_reprice_updates_prices() -> None:
    """Cached graphs should get repriced with current market data."""
    trader = _make_trader()
    original_market = _make_market("m1", "Lakers win NBA Championship?", 0.20)
    original_graph = _make_graph([
        _make_node(original_market, team_or_player="Lakers"),
    ])

    updated_market = _make_market("m1", "Lakers win NBA Championship?", 0.35)
    repriced = trader._reprice_cached_graphs([original_graph], [updated_market])
    assert len(repriced) == 1
    assert repriced[0].nodes[0].market.outcome_prices[0] == 0.35
