"""Tests for the cross-market dependency detection module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.arb.dependency import (
    DependencyDetector,
    DependencyEdge,
    DependencyGraph,
    _keyword_overlap,
    _question_similarity,
)


def _make_market(market_id: str, question: str, outcomes: list[str] | None = None) -> Market:
    outcomes = outcomes or ["Yes", "No"]
    return Market.from_cli(
        {
            "id": market_id,
            "question": question,
            "outcomes": json.dumps(outcomes),
            "outcomePrices": json.dumps([str(1.0 / len(outcomes))] * len(outcomes)),
            "volume": "10000",
            "active": True,
            "closed": False,
            "clobTokenIds": json.dumps([f"tok_{market_id}_{i}" for i in range(len(outcomes))]),
        }
    )


class TestDependencyGraph:
    def test_empty_graph(self) -> None:
        graph = DependencyGraph()
        assert graph.connected_components() == []

    def test_single_edge(self) -> None:
        graph = DependencyGraph()
        edge = DependencyEdge(
            market_id_a="m1",
            market_id_b="m2",
            valid_combinations=[(0, 0), (1, 1)],
            relationship_type="conditional",
        )
        graph.add_edge(edge)
        components = graph.connected_components()
        assert len(components) == 1
        assert components[0] == {"m1", "m2"}

    def test_two_components(self) -> None:
        graph = DependencyGraph()
        graph.add_edge(DependencyEdge("m1", "m2", [(0, 0)], "dep"))
        graph.add_edge(DependencyEdge("m3", "m4", [(0, 0)], "dep"))
        components = graph.connected_components()
        assert len(components) == 2

    def test_chain_forms_single_component(self) -> None:
        graph = DependencyGraph()
        graph.add_edge(DependencyEdge("m1", "m2", [(0, 0)], "dep"))
        graph.add_edge(DependencyEdge("m2", "m3", [(0, 0)], "dep"))
        components = graph.connected_components()
        assert len(components) == 1
        assert components[0] == {"m1", "m2", "m3"}

    def test_get_edges_for_component(self) -> None:
        graph = DependencyGraph()
        e1 = DependencyEdge("m1", "m2", [(0, 0)], "dep")
        e2 = DependencyEdge("m3", "m4", [(0, 0)], "dep")
        graph.add_edge(e1)
        graph.add_edge(e2)
        edges = graph.get_edges_for_component({"m1", "m2"})
        assert len(edges) == 1
        assert edges[0] is e1

    def test_get_constraints_single_market(self) -> None:
        graph = DependencyGraph()
        polytope = graph.get_constraints(["m1"])
        assert polytope.n_vars == 2  # default single-market


class TestSimilarity:
    def test_identical_questions(self) -> None:
        assert _question_similarity("Will X win?", "Will X win?") == 1.0

    def test_different_questions(self) -> None:
        sim = _question_similarity("Will it rain?", "Who won the election?")
        assert sim < 0.5

    def test_keyword_overlap_high(self) -> None:
        overlap = _keyword_overlap("Will Trump win the election?", "Will Trump lose the election?")
        assert overlap > 0.5

    def test_keyword_overlap_zero(self) -> None:
        overlap = _keyword_overlap("rain forecast", "stock market")
        assert overlap == 0.0


class TestDependencyDetector:
    def test_no_client_returns_empty_graph(self) -> None:
        """When no API key is set, should return empty graph."""
        with patch.dict("os.environ", {}, clear=True):
            detector = DependencyDetector()
            m1 = _make_market("1", "Will X happen?")
            m2 = _make_market("2", "Will X happen by March?")
            graph = detector.detect([m1, m2])
            assert len(graph.edges) == 0

    def test_caching(self) -> None:
        """Cached results should be returned without LLM calls."""
        detector = DependencyDetector()
        detector._client = MagicMock()

        edge = DependencyEdge("1", "2", [(0, 0)], "test")
        detector._store_cache("1", "2", edge)

        cached, result = detector._check_cache("1", "2")
        assert cached is True
        assert result is edge

    def test_cache_key_order_independent(self) -> None:
        """Cache key should be order-independent."""
        detector = DependencyDetector()
        edge = DependencyEdge("a", "b", [(0, 0)], "test")
        detector._store_cache("b", "a", edge)
        cached, result = detector._check_cache("a", "b")
        assert cached is True

    def test_candidate_pair_filtering_similar_questions(self) -> None:
        """Markets with similar questions should be candidate pairs."""
        detector = DependencyDetector()
        m1 = _make_market("1", "Will Bitcoin reach $100k by June 2026?")
        m2 = _make_market("2", "Will Bitcoin reach $100k by December 2026?")
        m3 = _make_market("3", "Will it rain in Seattle tomorrow?")
        pairs = detector._get_candidate_pairs([m1, m2, m3])
        pair_ids = [(p[0].id, p[1].id) for p in pairs]
        assert ("1", "2") in pair_ids
        # m3 is unrelated, should not pair with m1 or m2
        assert ("1", "3") not in pair_ids

    def test_candidate_pair_same_group(self) -> None:
        """Markets with same group_item_title should be candidates."""
        detector = DependencyDetector()
        m1 = _make_market("1", "Team A wins?")
        m2 = _make_market("2", "Team B wins?")
        m1.group_item_title = "NBA Finals"
        m2.group_item_title = "NBA Finals"
        pairs = detector._get_candidate_pairs([m1, m2])
        assert len(pairs) == 1

    def test_rate_limiting(self) -> None:
        """Should respect rate limits."""
        import time

        detector = DependencyDetector(max_calls_per_hour=2)
        detector._call_timestamps = [time.monotonic(), time.monotonic()]
        assert detector._can_call() is False

    def test_detect_with_mock_llm(self) -> None:
        """Full detection flow with mocked LLM."""
        detector = DependencyDetector()
        mock_client = MagicMock()
        detector._client = mock_client

        llm_response = json.dumps({
            "dependent": True,
            "relationship": "conditional",
            "valid_combinations": [[0, 0], [1, 1]],
        })
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = llm_response
        mock_client.chat.completions.create.return_value = mock_response

        m1 = _make_market("1", "Will X run for president?")
        m2 = _make_market("2", "Will X win the presidential election?")
        graph = detector.detect([m1, m2])

        assert len(graph.edges) == 1
        assert graph.edges[0].relationship_type == "conditional"
