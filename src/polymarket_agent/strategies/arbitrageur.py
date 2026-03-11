"""Arbitrageur strategy — exploits pricing inconsistencies within and across markets.

Implements a 5-layer pipeline from "Unravelling the Probabilistic Forest:
Arbitrage in Prediction Markets" (arXiv:2508.03474v1):
  L1: Cross-market dependency detection (LLM-driven)
  L2: Bregman divergence measurement
  L3: Frank-Wolfe optimization over marginal polytopes
  L4: Execution-aware Kelly sizing
  L5: Order book execution validation

Degrades gracefully to simple price-sum checks when dependency detection
is disabled or the LLM is unavailable.
"""

import logging
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.arb.bregman import bregman_gradient, prices_to_theta
from polymarket_agent.strategies.arb.dependency import DependencyDetector, DependencyEdge, DependencyGraph
from polymarket_agent.strategies.arb.execution_validator import validate_execution
from polymarket_agent.strategies.arb.frank_wolfe import (
    barrier_frank_wolfe,
    build_multi_market_polytope,
    build_single_market_polytope,
)
from polymarket_agent.strategies.base import Signal, Strategy

if TYPE_CHECKING:
    from polymarket_agent.data.provider import DataProvider

logger = logging.getLogger(__name__)

_DEFAULT_PRICE_SUM_TOLERANCE: float = 0.02
_DEFAULT_MIN_DEVIATION: float = 0.03
_DEFAULT_ORDER_SIZE: float = 25.0
_DEFAULT_MIN_BREGMAN_DIVERGENCE: float = 0.01
_DEFAULT_FW_MAX_ITERATIONS: int = 150
_DEFAULT_FW_CONVERGENCE_THRESHOLD: float = 1e-6
_DEFAULT_FW_ALPHA: float = 0.9
_DEFAULT_FW_INITIAL_EPSILON: float = 0.1
_DEFAULT_MIN_PROFIT_THRESHOLD: float = 0.05
_DEFAULT_MAX_SLIPPAGE_PCT: float = 0.02


class Arbitrageur(Strategy):
    """Detect and trade pricing inconsistencies using Bregman divergence.

    When dependency detection is enabled and an LLM is available, runs the
    full 5-layer pipeline for cross-market arbitrage. Otherwise falls back
    to simple single-market price-sum checks.
    """

    name: str = "arbitrageur"

    def __init__(self) -> None:
        # Simple mode params (backward compat)
        self._price_sum_tolerance: float = _DEFAULT_PRICE_SUM_TOLERANCE
        self._min_deviation: float = _DEFAULT_MIN_DEVIATION
        self._order_size: float = _DEFAULT_ORDER_SIZE

        # Layer 1: Dependency detection
        self._dependency_detection: bool = False
        self._dependency_detector: DependencyDetector | None = None

        # Layer 2: Bregman divergence
        self._min_bregman_divergence: float = _DEFAULT_MIN_BREGMAN_DIVERGENCE

        # Layer 3: Frank-Wolfe
        self._fw_max_iterations: int = _DEFAULT_FW_MAX_ITERATIONS
        self._fw_convergence_threshold: float = _DEFAULT_FW_CONVERGENCE_THRESHOLD
        self._fw_alpha: float = _DEFAULT_FW_ALPHA
        self._fw_initial_epsilon: float = _DEFAULT_FW_INITIAL_EPSILON

        # Layer 5: Execution validation
        self._min_profit_threshold: float = _DEFAULT_MIN_PROFIT_THRESHOLD
        self._max_slippage_pct: float = _DEFAULT_MAX_SLIPPAGE_PCT

        # Cached dependency graph
        self._dep_graph: DependencyGraph | None = None

    def configure(self, config: dict[str, Any]) -> None:
        # Simple mode
        self._price_sum_tolerance = float(config.get("price_sum_tolerance", _DEFAULT_PRICE_SUM_TOLERANCE))
        self._min_deviation = float(config.get("min_deviation", _DEFAULT_MIN_DEVIATION))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))

        # Layer 1
        self._dependency_detection = bool(config.get("dependency_detection", False))
        if self._dependency_detection:
            self._dependency_detector = DependencyDetector(
                provider=str(config.get("dependency_provider", "openai")),
                model=str(config.get("dependency_model", "gpt-4o")),
                base_url=config.get("dependency_base_url"),
                api_key_env=config.get("dependency_api_key_env"),
                max_calls_per_hour=int(config.get("dependency_max_calls_per_hour", 50)),
                cache_ttl=int(config.get("dependency_cache_ttl", 3600)),
                extra_params=config.get("extra_params"),
            )

        # Layer 2
        self._min_bregman_divergence = float(config.get("min_bregman_divergence", _DEFAULT_MIN_BREGMAN_DIVERGENCE))

        # Layer 3
        self._fw_max_iterations = int(config.get("fw_max_iterations", _DEFAULT_FW_MAX_ITERATIONS))
        self._fw_convergence_threshold = float(
            config.get("fw_convergence_threshold", _DEFAULT_FW_CONVERGENCE_THRESHOLD)
        )
        self._fw_alpha = float(config.get("fw_alpha", _DEFAULT_FW_ALPHA))
        self._fw_initial_epsilon = float(config.get("fw_initial_epsilon", _DEFAULT_FW_INITIAL_EPSILON))

        # Layer 5
        self._min_profit_threshold = float(config.get("min_profit_threshold", _DEFAULT_MIN_PROFIT_THRESHOLD))
        self._max_slippage_pct = float(config.get("max_slippage_pct", _DEFAULT_MAX_SLIPPAGE_PCT))

    def analyze(self, markets: list[Market], data: "DataProvider") -> list[Signal]:
        """Run the arbitrage pipeline on active markets."""
        active_markets = [m for m in markets if m.active and not m.closed]
        if not active_markets:
            return []

        signals: list[Signal] = []

        # Try advanced pipeline first
        if self._dependency_detection and self._dependency_detector is not None:
            signals.extend(self._run_advanced_pipeline(active_markets, data))

        # Simple fallback for markets not covered by advanced pipeline
        covered_ids = {s.market_id for s in signals}
        for market in active_markets:
            if market.id not in covered_ids:
                sig = self._check_price_sum(market)
                if sig is not None:
                    signals.append(sig)

        return signals

    def _run_advanced_pipeline(self, markets: list[Market], data: "DataProvider") -> list[Signal]:
        """Run the full 5-layer pipeline for cross-market arbitrage."""
        signals: list[Signal] = []

        # Layer 1: Build/update dependency graph
        assert self._dependency_detector is not None
        self._dep_graph = self._dependency_detector.detect(markets)

        # Process connected components of dependent markets
        components = self._dep_graph.connected_components()
        market_map = {m.id: m for m in markets}

        for component in components:
            comp_markets = [market_map[mid] for mid in component if mid in market_map]
            if len(comp_markets) < 2:
                continue
            signals.extend(self._process_component(comp_markets, data))

        # Also run Bregman on individual markets (single-market arb)
        for market in markets:
            if len(market.outcome_prices) < 2:
                continue
            sig = self._check_bregman_single(market, data)
            if sig is not None:
                signals.append(sig)

        return signals

    def _process_component(self, markets: list[Market], data: "DataProvider") -> list[Signal]:
        """Process a connected component of dependent markets.

        The dependency detector currently returns pairwise edges, so the
        advanced cross-market optimizer only supports binary market pairs.
        Larger components fall back to the single-market checks below.
        """
        assert self._dep_graph is not None

        if len(markets) != 2:
            logger.debug("Skipping unsupported dependency component with %d markets", len(markets))
            return []

        edges = self._dep_graph.get_edges_for_component({market.id for market in markets})
        if not edges:
            return []

        market_by_id = {market.id: market for market in markets}
        edge = edges[0]
        market_a = market_by_id.get(edge.market_id_a)
        market_b = market_by_id.get(edge.market_id_b)
        if market_a is None or market_b is None:
            return []

        return self._process_market_pair(market_a, market_b, edge, data)

    def _process_market_pair(
        self,
        market_a: Market,
        market_b: Market,
        edge: DependencyEdge,
        data: "DataProvider",
    ) -> list[Signal]:
        """Project a dependent binary market pair onto the valid joint outcomes."""
        if len(market_a.outcome_prices) != 2 or len(market_b.outcome_prices) != 2:
            logger.debug("Skipping non-binary dependency pair %s/%s", market_a.id, market_b.id)
            return []

        valid_combinations = [
            (outcome_a, outcome_b)
            for outcome_a, outcome_b in edge.valid_combinations
            if outcome_a in (0, 1) and outcome_b in (0, 1)
        ]
        if not valid_combinations:
            return []

        implied_joint = np.array(
            [market_a.outcome_prices[a_idx] * market_b.outcome_prices[b_idx] for a_idx, b_idx in valid_combinations],
            dtype=np.float64,
        )
        implied_joint = np.clip(implied_joint, 1e-6, None)
        theta = prices_to_theta(implied_joint)
        polytope = build_multi_market_polytope(valid_combinations)

        mu_star, divergence = barrier_frank_wolfe(
            theta,
            polytope,
            alpha=self._fw_alpha,
            epsilon=self._fw_initial_epsilon,
            max_iter=self._fw_max_iterations,
            tol=self._fw_convergence_threshold,
        )

        if divergence < self._min_bregman_divergence:
            return []

        fair_prices_a = np.zeros(len(market_a.outcome_prices), dtype=np.float64)
        fair_prices_b = np.zeros(len(market_b.outcome_prices), dtype=np.float64)
        for weight, (a_idx, b_idx) in zip(mu_star, valid_combinations, strict=False):
            fair_prices_a[a_idx] += weight
            fair_prices_b[b_idx] += weight

        signals: list[Signal] = []
        signal_a = self._build_cross_market_signal(market_a, fair_prices_a, divergence, data)
        if signal_a is not None:
            signals.append(signal_a)

        signal_b = self._build_cross_market_signal(market_b, fair_prices_b, divergence, data)
        if signal_b is not None:
            signals.append(signal_b)

        return signals

    def _build_cross_market_signal(
        self,
        market: Market,
        fair_prices: np.ndarray,
        divergence: float,
        data: "DataProvider",
    ) -> Signal | None:
        """Emit a buy signal for the most underpriced outcome in a dependent market."""
        if len(fair_prices) != len(market.outcome_prices):
            return None

        deltas = fair_prices - np.array(market.outcome_prices, dtype=np.float64)
        best_idx = int(np.argmax(deltas))
        price_gap = float(deltas[best_idx])
        if price_gap < self._min_deviation:
            return None

        if best_idx >= len(market.clob_token_ids):
            return None

        token_id = market.clob_token_ids[best_idx]
        expected_profit = price_gap * self._order_size
        valid, reason = validate_execution(
            data,
            token_id,
            self._order_size,
            "buy",
            expected_profit,
            min_profit=self._min_profit_threshold,
            max_slippage=self._max_slippage_pct,
        )
        if not valid:
            logger.debug("Execution validation failed for %s: %s", token_id, reason)
            return None

        return Signal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            side="buy",
            confidence=round(min(price_gap / 0.5, 1.0), 4),
            target_price=market.outcome_prices[best_idx],
            size=self._order_size,
            reason=(
                f"cross_market: div={divergence:.6f}, "
                f"fair_price={fair_prices[best_idx]:.4f}, observed_price={market.outcome_prices[best_idx]:.4f}, {reason}"
            ),
            execution_probability=0.9,
        )

    def _check_bregman_single(self, market: Market, data: "DataProvider") -> Signal | None:
        """Check a single market for Bregman divergence arbitrage."""
        prices = np.array(market.outcome_prices, dtype=np.float64)
        prices = np.clip(prices, 1e-6, 1.0 - 1e-6)

        price_sum = float(np.sum(prices))
        if abs(price_sum - 1.0) < self._price_sum_tolerance:
            return None

        theta = prices_to_theta(prices)

        polytope = build_single_market_polytope(len(prices))
        mu_star, divergence = barrier_frank_wolfe(
            theta,
            polytope,
            alpha=self._fw_alpha,
            epsilon=self._fw_initial_epsilon,
            max_iter=min(self._fw_max_iterations, 50),
            tol=self._fw_convergence_threshold,
        )

        if divergence < self._min_bregman_divergence:
            return None

        gradient = bregman_gradient(mu_star, theta)
        best_idx = int(np.argmax(np.abs(gradient)))

        if best_idx >= len(market.clob_token_ids):
            return None

        direction = gradient[best_idx]
        token_id = market.clob_token_ids[best_idx]
        side: Literal["buy", "sell"] = "buy" if direction > 0 else "sell"
        confidence = round(min(abs(direction) / 0.5, 1.0), 4)

        # Layer 5: Execution validation
        expected_profit = abs(direction) * self._order_size
        valid, reason = validate_execution(
            data,
            token_id,
            self._order_size,
            side,
            expected_profit,
            min_profit=self._min_profit_threshold,
            max_slippage=self._max_slippage_pct,
        )
        exec_prob = 0.9 if valid else 0.5
        if not valid:
            logger.debug("Execution validation failed for %s: %s", token_id, reason)
            return None

        return Signal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            side=side,
            confidence=confidence,
            target_price=market.outcome_prices[best_idx],
            size=self._order_size,
            reason=f"bregman_single: div={divergence:.6f}, direction={direction:.4f}",
            execution_probability=exec_prob,
        )

    def _check_price_sum(self, market: Market) -> Signal | None:
        """Check if outcome prices sum to approximately 1.0 (simple fallback)."""
        if len(market.outcome_prices) < 2:
            return None

        price_sum = sum(market.outcome_prices)
        deviation = abs(price_sum - 1.0)

        if deviation <= self._price_sum_tolerance:
            return None

        if deviation < self._min_deviation:
            return None

        if price_sum < 1.0:
            idx = market.outcome_prices.index(min(market.outcome_prices))
            side: Literal["buy", "sell"] = "buy"
        else:
            idx = market.outcome_prices.index(min(market.outcome_prices))
            side = "buy"

        if idx >= len(market.clob_token_ids):
            return None
        token_id = market.clob_token_ids[idx]

        return Signal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            side=side,
            confidence=round(min(deviation / 0.1, 1.0), 4),
            target_price=market.outcome_prices[idx],
            size=self._order_size,
            reason=f"price_sum={price_sum:.4f}, deviation={deviation:.4f}",
        )
