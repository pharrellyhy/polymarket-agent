"""Tests for the Frank-Wolfe optimizer."""

import numpy as np
import pytest

from polymarket_agent.strategies.arb.bregman import prices_to_theta, theta_to_prices
from polymarket_agent.strategies.arb.frank_wolfe import (
    MarginalPolytope,
    barrier_frank_wolfe,
    build_single_market_polytope,
    ip_oracle,
)


def test_build_single_market_polytope_2_outcomes() -> None:
    """2-outcome simplex should have sum-to-one equality constraint."""
    poly = build_single_market_polytope(2)
    assert poly.n_vars == 2
    assert poly.A_eq is not None
    np.testing.assert_array_equal(poly.A_eq, [[1.0, 1.0]])
    np.testing.assert_array_equal(poly.b_eq, [1.0])


def test_build_single_market_polytope_3_outcomes() -> None:
    poly = build_single_market_polytope(3)
    assert poly.n_vars == 3
    assert poly.A_eq.shape == (1, 3)


def test_ip_oracle_simplex() -> None:
    """IP oracle on a simplex should return a vertex."""
    poly = build_single_market_polytope(3)
    gradient = np.array([1.0, -1.0, 0.5])
    vertex = ip_oracle(gradient, poly)
    # Should pick the vertex minimizing dot(gradient, vertex)
    # With gradient [1, -1, 0.5], the min vertex is [0, 1, 0]
    assert abs(np.sum(vertex) - 1.0) < 1e-6
    assert vertex[1] > 0.9  # should concentrate on outcome 2


def test_frank_wolfe_converges_uniform() -> None:
    """Frank-Wolfe should converge for uniform market prices."""
    prices = np.array([0.5, 0.5])
    theta = prices_to_theta(prices)
    polytope = build_single_market_polytope(2)

    mu_star, divergence = barrier_frank_wolfe(theta, polytope, max_iter=50)

    # Should converge to near-uniform (prices are already fair)
    assert divergence < 0.1
    assert abs(np.sum(mu_star) - 1.0) < 1e-6


def test_frank_wolfe_converges_mispriced() -> None:
    """Frank-Wolfe should find opportunity in mispriced market."""
    # Prices don't sum to 1 (mispriced)
    prices = np.array([0.4, 0.4])
    theta = prices_to_theta(prices)
    polytope = build_single_market_polytope(2)

    mu_star, divergence = barrier_frank_wolfe(theta, polytope, max_iter=100)

    # Should converge to a valid distribution on simplex
    assert abs(np.sum(mu_star) - 1.0) < 1e-6
    assert all(m >= 0 for m in mu_star)


def test_frank_wolfe_three_outcome() -> None:
    """Should work for 3-outcome markets."""
    prices = np.array([0.3, 0.3, 0.3])
    theta = prices_to_theta(prices)
    polytope = build_single_market_polytope(3)

    mu_star, divergence = barrier_frank_wolfe(theta, polytope, max_iter=100)

    assert abs(np.sum(mu_star) - 1.0) < 1e-6
    assert all(m >= 0 for m in mu_star)


def test_frank_wolfe_returns_valid_distribution() -> None:
    """Output should always be a valid probability distribution."""
    prices = np.array([0.6, 0.3, 0.1])
    theta = prices_to_theta(prices)
    polytope = build_single_market_polytope(3)

    mu_star, divergence = barrier_frank_wolfe(theta, polytope, max_iter=50)

    assert abs(np.sum(mu_star) - 1.0) < 1e-6
    assert all(m > -1e-10 for m in mu_star)
    assert divergence >= 0


def test_marginal_polytope_default_bounds() -> None:
    """MarginalPolytope should default to [0, 1] bounds."""
    poly = MarginalPolytope(n_vars=3)
    assert len(poly.bounds) == 3
    assert all(b == (0.0, 1.0) for b in poly.bounds)
