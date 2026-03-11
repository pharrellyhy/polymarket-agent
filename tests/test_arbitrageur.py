"""Tests for the Arbitrageur strategy."""

import json
from unittest.mock import MagicMock

from polymarket_agent.data.models import Market, OrderBook, OrderBookLevel
from polymarket_agent.strategies.arb.dependency import DependencyEdge, DependencyGraph
from polymarket_agent.strategies.arbitrageur import Arbitrageur


def _make_market(
    market_id: str,
    yes_price: float,
    no_price: float | None = None,
    active: bool = True,
    closed: bool = False,
) -> Market:
    if no_price is None:
        no_price = round(1 - yes_price, 4)
    return Market.from_cli(
        {
            "id": market_id,
            "question": f"Test market {market_id}?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(yes_price), str(no_price)]),
            "volume": "50000",
            "volume24hr": "10000",
            "liquidity": "5000",
            "active": active,
            "closed": closed,
            "clobTokenIds": json.dumps([f"0xtok_{market_id}_yes", f"0xtok_{market_id}_no"]),
        }
    )


def _make_orderbook() -> OrderBook:
    return OrderBook(
        asks=[OrderBookLevel(price=0.50, size=200.0)],
        bids=[OrderBookLevel(price=0.49, size=200.0)],
    )


def test_arbitrageur_detects_price_sum_deviation() -> None:
    """If Yes+No prices don't sum to ~1.0, emit a signal."""
    strategy = Arbitrageur()
    strategy.configure({"price_sum_tolerance": 0.02})
    # Yes=0.60, No=0.35 -> sum=0.95, deviation=0.05 > tolerance
    market = _make_market("1", yes_price=0.60, no_price=0.35)
    signals = strategy.analyze([market], MagicMock())
    assert len(signals) >= 1
    assert any("price_sum" in s.reason for s in signals)


def test_arbitrageur_ignores_correct_pricing() -> None:
    """Markets with correct pricing should not generate signals."""
    strategy = Arbitrageur()
    strategy.configure({"price_sum_tolerance": 0.02})
    signals = strategy.analyze([_make_market("1", yes_price=0.50)], MagicMock())
    assert signals == []


def test_arbitrageur_skips_inactive() -> None:
    strategy = Arbitrageur()
    market = _make_market("1", yes_price=0.60, no_price=0.35, active=False, closed=True)
    signals = strategy.analyze([market], MagicMock())
    assert signals == []


def test_arbitrageur_configures_tolerance() -> None:
    strategy = Arbitrageur()
    strategy.configure({"price_sum_tolerance": 0.05, "min_deviation": 0.04})
    assert strategy._price_sum_tolerance == 0.05
    assert strategy._min_deviation == 0.04


def test_arbitrageur_respects_min_deviation() -> None:
    """Deviation above tolerance but below min_deviation should be filtered."""
    strategy = Arbitrageur()
    # tolerance=0.02, min_deviation=0.06: deviation of 0.05 passes tolerance but not min_deviation
    strategy.configure({"price_sum_tolerance": 0.02, "min_deviation": 0.06})
    market = _make_market("1", yes_price=0.60, no_price=0.35)  # sum=0.95, dev=0.05
    signals = strategy.analyze([market], MagicMock())
    assert signals == []


def test_arbitrageur_skips_signal_when_token_id_missing() -> None:
    strategy = Arbitrageur()
    market = _make_market("2", yes_price=0.60, no_price=0.35)
    market.clob_token_ids = []

    signals = strategy.analyze([market], MagicMock())

    assert signals == []


def test_arbitrageur_backward_compat_without_dependency() -> None:
    """Without dependency detection, should behave like the old version."""
    strategy = Arbitrageur()
    strategy.configure({
        "price_sum_tolerance": 0.02,
        "dependency_detection": False,
    })
    market = _make_market("1", yes_price=0.60, no_price=0.35)
    signals = strategy.analyze([market], MagicMock())
    assert len(signals) >= 1
    assert any("price_sum" in s.reason for s in signals)


def test_arbitrageur_advanced_config() -> None:
    """Should accept all new configuration parameters."""
    strategy = Arbitrageur()
    strategy.configure({
        "price_sum_tolerance": 0.015,
        "order_size": 10.0,
        "min_bregman_divergence": 0.02,
        "fw_max_iterations": 100,
        "fw_convergence_threshold": 0.0001,
        "fw_alpha": 0.8,
        "fw_initial_epsilon": 0.2,
        "min_profit_threshold": 0.03,
        "max_slippage_pct": 0.01,
    })
    assert strategy._min_bregman_divergence == 0.02
    assert strategy._fw_max_iterations == 100
    assert strategy._fw_alpha == 0.8
    assert strategy._min_profit_threshold == 0.03


def test_arbitrageur_advanced_pipeline_with_mocked_deps() -> None:
    """Full pipeline with mocked dependency detector and order book."""
    strategy = Arbitrageur()
    strategy.configure({
        "price_sum_tolerance": 0.02,
        "dependency_detection": True,
        "dependency_provider": "openai",
        "min_bregman_divergence": 0.001,
    })

    mock_detector = MagicMock()
    mock_detector.detect.return_value = DependencyGraph()
    strategy._dependency_detector = mock_detector

    # Create a mispriced market for single-market Bregman check
    market = _make_market("1", yes_price=0.40, no_price=0.40)

    # Mock data provider with orderbook
    data = MagicMock()
    data.get_orderbook.return_value = _make_orderbook()

    signals = strategy.analyze([market], data)
    # Should generate at least one signal (either from Bregman or price_sum fallback)
    assert len(signals) >= 1


def test_arbitrageur_signal_has_execution_probability() -> None:
    """Signals from advanced pipeline should include execution_probability."""
    strategy = Arbitrageur()
    strategy.configure({
        "dependency_detection": True,
        "min_bregman_divergence": 0.001,
    })

    mock_detector = MagicMock()
    mock_detector.detect.return_value = DependencyGraph()
    strategy._dependency_detector = mock_detector

    market = _make_market("1", yes_price=0.40, no_price=0.40)
    data = MagicMock()
    data.get_orderbook.return_value = _make_orderbook()

    signals = strategy.analyze([market], data)
    for sig in signals:
        if "bregman" in sig.reason:
            assert sig.execution_probability is not None


def test_arbitrageur_detects_cross_market_dependency_mispricing() -> None:
    """Dependent binary markets should produce cross-market signals."""
    strategy = Arbitrageur()
    strategy.configure({
        "dependency_detection": True,
        "min_bregman_divergence": 0.001,
    })

    graph = DependencyGraph()
    graph.add_edge(
        DependencyEdge(
            market_id_a="1",
            market_id_b="2",
            valid_combinations=[(0, 0), (1, 1)],
            relationship_type="aligned",
        )
    )
    mock_detector = MagicMock()
    mock_detector.detect.return_value = graph
    strategy._dependency_detector = mock_detector

    market_a = _make_market("1", yes_price=0.80, no_price=0.20)
    market_b = _make_market("2", yes_price=0.20, no_price=0.80)

    data = MagicMock()
    data.get_orderbook.return_value = _make_orderbook()

    signals = strategy.analyze([market_a, market_b], data)

    assert signals
    assert any("cross_market" in signal.reason for signal in signals)
