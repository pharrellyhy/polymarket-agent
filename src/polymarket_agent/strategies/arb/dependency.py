"""Cross-market dependency detection via LLM analysis.

Identifies logical dependencies between prediction markets (e.g.,
"Will X win the primary?" depends on "Will X run?") and builds a
dependency graph with valid outcome combinations for multi-market
arbitrage.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

import numpy as np

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.arb.frank_wolfe import MarginalPolytope, build_single_market_polytope

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER: str = "openai"
_DEFAULT_MODEL: str = "gpt-4o"
_DEFAULT_MAX_CALLS_PER_HOUR: int = 50
_DEFAULT_CACHE_TTL: int = 3600

_DEFAULT_API_KEY_ENVS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_DEPENDENCY_PROMPT_TEMPLATE: str = """You are a prediction market analyst. Given two markets, determine if their outcomes are logically dependent.

Market A: "{question_a}"
Outcomes: {outcomes_a}

Market B: "{question_b}"
Outcomes: {outcomes_b}

If the markets are logically dependent (one outcome constrains the other), respond with a JSON object:
{{"dependent": true, "relationship": "<brief description>", "valid_combinations": [[<a_idx>, <b_idx>], ...]}}

where valid_combinations lists pairs [outcome_index_A, outcome_index_B] that can logically co-occur.

If the markets are independent, respond with:
{{"dependent": false}}

Respond ONLY with valid JSON, no other text."""


@dataclass
class DependencyEdge:
    """A dependency relationship between two markets."""

    market_id_a: str
    market_id_b: str
    valid_combinations: list[tuple[int, int]]
    relationship_type: str


@dataclass
class DependencyGraph:
    """Graph of dependency relationships between markets."""

    edges: list[DependencyEdge] = field(default_factory=list)
    _adjacency: dict[str, set[str]] = field(default_factory=dict)

    def add_edge(self, edge: DependencyEdge) -> None:
        """Add a dependency edge to the graph."""
        self.edges.append(edge)
        self._adjacency.setdefault(edge.market_id_a, set()).add(edge.market_id_b)
        self._adjacency.setdefault(edge.market_id_b, set()).add(edge.market_id_a)

    def connected_components(self) -> list[set[str]]:
        """Find connected components of dependent markets."""
        visited: set[str] = set()
        components: list[set[str]] = []

        for node in self._adjacency:
            if node in visited:
                continue
            component: set[str] = set()
            stack = [node]
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                stack.extend(self._adjacency.get(current, set()) - visited)
            components.append(component)

        return components

    def get_edges_for_component(self, component: set[str]) -> list[DependencyEdge]:
        """Get all edges within a connected component."""
        return [e for e in self.edges if e.market_id_a in component and e.market_id_b in component]

    def get_constraints(self, market_ids: list[str]) -> MarginalPolytope:
        """Build a MarginalPolytope from dependency constraints for given markets.

        For dependent markets, builds a polytope from valid joint outcome combinations.
        For independent markets, returns a simple simplex constraint.
        """
        if len(market_ids) <= 1:
            return build_single_market_polytope(2)

        component = set(market_ids)
        edges = self.get_edges_for_component(component)
        if not edges:
            return build_single_market_polytope(len(market_ids) * 2)

        # Collect all valid joint combinations across all edges
        all_combos: list[tuple[int, ...]] = []
        for edge in edges:
            for combo in edge.valid_combinations:
                all_combos.append(tuple(combo))

        if not all_combos:
            return build_single_market_polytope(len(market_ids) * 2)

        n_combos = len(all_combos)
        A_eq = np.ones((1, n_combos), dtype=np.float64)
        b_eq = np.array([1.0], dtype=np.float64)

        return MarginalPolytope(
            n_vars=n_combos,
            A_eq=A_eq,
            b_eq=b_eq,
        )


def _question_similarity(q1: str, q2: str) -> float:
    """Compute similarity between two market questions."""
    return SequenceMatcher(None, q1.lower(), q2.lower()).ratio()


def _keyword_overlap(q1: str, q2: str) -> float:
    """Compute keyword overlap ratio between two questions."""
    words1 = set(q1.lower().split())
    words2 = set(q2.lower().split())
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    return len(intersection) / min(len(words1), len(words2))


class DependencyDetector:
    """Detect dependencies between markets using LLM analysis."""

    def __init__(
        self,
        provider: str = _DEFAULT_PROVIDER,
        model: str = _DEFAULT_MODEL,
        base_url: str | None = None,
        api_key_env: str | None = None,
        max_calls_per_hour: int = _DEFAULT_MAX_CALLS_PER_HOUR,
        cache_ttl: int = _DEFAULT_CACHE_TTL,
        max_tokens: int = 1024,
        extra_params: dict[str, Any] | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._base_url = base_url
        self._api_key_env = api_key_env
        self._max_calls_per_hour = max_calls_per_hour
        self._cache_ttl = cache_ttl
        self._max_tokens = max_tokens
        self._extra_params: dict[str, Any] = extra_params or {}
        self._call_timestamps: list[float] = []
        self._client: Any = None
        # Cache: (market_id_a, market_id_b) -> (DependencyEdge | None, timestamp)
        self._cache: dict[tuple[str, str], tuple[DependencyEdge | None, float]] = {}
        self._init_client()

    def _resolved_api_key_env(self) -> str:
        if self._api_key_env:
            return self._api_key_env
        return _DEFAULT_API_KEY_ENVS.get(self._provider, "OPENAI_API_KEY")

    def _init_client(self) -> None:
        if self._provider == "anthropic":
            self._init_anthropic_client()
        elif self._provider == "openai":
            self._init_openai_client()
        else:
            logger.warning("Unknown provider %r; falling back to %s", self._provider, _DEFAULT_PROVIDER)
            self._provider = _DEFAULT_PROVIDER
            self._init_openai_client()

    def _init_anthropic_client(self) -> None:
        env_var = self._resolved_api_key_env()
        api_key = os.environ.get(env_var)
        if not api_key:
            logger.info("%s not set — DependencyDetector disabled", env_var)
            return
        try:
            import anthropic  # noqa: PLC0415

            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — DependencyDetector disabled")

    def _init_openai_client(self) -> None:
        env_var = self._resolved_api_key_env()
        api_key = os.environ.get(env_var)
        if not api_key:
            logger.info("%s not set — DependencyDetector disabled", env_var)
            return
        try:
            import openai  # noqa: PLC0415

            kwargs: dict[str, Any] = {"api_key": api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
        except ImportError:
            logger.warning("openai package not installed — DependencyDetector disabled")

    def _can_call(self) -> bool:
        now = time.monotonic()
        cutoff = now - 3600.0
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        return len(self._call_timestamps) < self._max_calls_per_hour

    def _call_llm(self, prompt: str) -> str:
        if self._client is None:
            return ""

        self._call_timestamps.append(time.monotonic())

        if self._provider == "anthropic":
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            return str(response.content[0].text).strip()

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a prediction market dependency analyst. Respond only with valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        if self._extra_params:
            kwargs["extra_body"] = self._extra_params

        response = self._client.chat.completions.create(**kwargs)
        return str(response.choices[0].message.content).strip()

    def _get_candidate_pairs(self, markets: list[Market]) -> list[tuple[Market, Market]]:
        """Select candidate pairs likely to be dependent using heuristics."""
        pairs: list[tuple[Market, Market]] = []
        for i, m1 in enumerate(markets):
            for m2 in markets[i + 1 :]:
                # Same group title suggests related markets
                if m1.group_item_title and m1.group_item_title == m2.group_item_title:
                    pairs.append((m1, m2))
                    continue
                # High question similarity
                sim = _question_similarity(m1.question, m2.question)
                if sim > 0.5:
                    pairs.append((m1, m2))
                    continue
                # Significant keyword overlap
                overlap = _keyword_overlap(m1.question, m2.question)
                if overlap > 0.4:
                    pairs.append((m1, m2))
        return pairs

    def _check_cache(self, id_a: str, id_b: str) -> tuple[bool, DependencyEdge | None]:
        """Check if a pair result is cached and still valid."""
        key = (min(id_a, id_b), max(id_a, id_b))
        if key in self._cache:
            edge, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return True, edge
            del self._cache[key]
        return False, None

    def _store_cache(self, id_a: str, id_b: str, edge: DependencyEdge | None) -> None:
        key = (min(id_a, id_b), max(id_a, id_b))
        self._cache[key] = (edge, time.time())

    def detect(self, markets: list[Market]) -> DependencyGraph:
        """Detect dependencies between markets and build a dependency graph.

        Args:
            markets: List of active markets to analyze.

        Returns:
            DependencyGraph with detected dependency edges.
        """
        graph = DependencyGraph()

        if self._client is None:
            return graph

        pairs = self._get_candidate_pairs(markets)
        for m1, m2 in pairs:
            # Check cache first
            cached, edge = self._check_cache(m1.id, m2.id)
            if cached:
                if edge is not None:
                    graph.add_edge(edge)
                continue

            if not self._can_call():
                logger.debug("DependencyDetector rate limit reached; skipping remaining pairs")
                break

            prompt = _DEPENDENCY_PROMPT_TEMPLATE.format(
                question_a=m1.question,
                outcomes_a=m1.outcomes,
                question_b=m2.question,
                outcomes_b=m2.outcomes,
            )

            try:
                raw = self._call_llm(prompt)
                result = json.loads(raw)
            except (json.JSONDecodeError, Exception):
                logger.debug("Failed to parse dependency response for %s / %s", m1.id, m2.id)
                self._store_cache(m1.id, m2.id, None)
                continue

            if result.get("dependent"):
                combos = [tuple(c) for c in result.get("valid_combinations", [])]
                edge = DependencyEdge(
                    market_id_a=m1.id,
                    market_id_b=m2.id,
                    valid_combinations=[(int(c[0]), int(c[1])) for c in combos if len(c) == 2],
                    relationship_type=result.get("relationship", "unknown"),
                )
                graph.add_edge(edge)
                self._store_cache(m1.id, m2.id, edge)
            else:
                self._store_cache(m1.id, m2.id, None)

        return graph
