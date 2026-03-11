"""Tests for the Bregman divergence module."""

import numpy as np
import pytest

from polymarket_agent.strategies.arb.bregman import (
    bregman_gradient,
    kl_divergence,
    lmsr_cost,
    negative_entropy,
    prices_to_theta,
    theta_to_prices,
)


def test_negative_entropy_uniform() -> None:
    """Uniform distribution should have negative entropy = -ln(n)."""
    mu = np.array([0.5, 0.5])
    result = negative_entropy(mu)
    expected = 2 * (0.5 * np.log(0.5))
    assert abs(result - expected) < 1e-10


def test_negative_entropy_deterministic() -> None:
    """Deterministic distribution should have negative entropy = 0."""
    mu = np.array([1.0, 0.0])
    result = negative_entropy(mu)
    # ln(1) = 0, ln(eps) * eps ~ 0
    assert result <= 0.01


def test_lmsr_cost_equal_params() -> None:
    """Equal theta should give ln(n)."""
    theta = np.array([0.0, 0.0])
    result = lmsr_cost(theta)
    expected = np.log(2.0)
    assert abs(result - expected) < 1e-10


def test_lmsr_cost_numerical_stability() -> None:
    """Large theta values should not overflow."""
    theta = np.array([1000.0, 1001.0])
    result = lmsr_cost(theta)
    # Should be close to 1001 + ln(1 + exp(-1))
    expected = 1001.0 + np.log(1 + np.exp(-1.0))
    assert abs(result - expected) < 1e-6


def test_kl_divergence_zero_when_matched() -> None:
    """KL divergence should be ~0 when mu matches softmax(theta)."""
    theta = np.array([1.0, 2.0])
    mu = theta_to_prices(theta)
    div = kl_divergence(mu, theta)
    assert abs(div) < 1e-6


def test_kl_divergence_positive() -> None:
    """KL divergence should be positive when mu != softmax(theta)."""
    theta = np.array([0.0, 0.0])
    mu = np.array([0.9, 0.1])
    div = kl_divergence(mu, theta)
    assert div > 0


def test_prices_to_theta_roundtrip() -> None:
    """Converting prices to theta and back should give original prices."""
    prices = np.array([0.3, 0.7])
    theta = prices_to_theta(prices)
    recovered = theta_to_prices(theta)
    np.testing.assert_allclose(recovered, prices, atol=1e-10)


def test_theta_to_prices_sums_to_one() -> None:
    """Output prices should always sum to 1."""
    theta = np.array([1.5, -0.5, 0.3])
    prices = theta_to_prices(theta)
    assert abs(np.sum(prices) - 1.0) < 1e-10
    assert all(p > 0 for p in prices)


def test_bregman_gradient_shape() -> None:
    """Gradient should have same shape as inputs."""
    mu = np.array([0.4, 0.6])
    theta = np.array([0.0, 0.0])
    grad = bregman_gradient(mu, theta)
    assert grad.shape == mu.shape


def test_bregman_gradient_zero_at_optimum() -> None:
    """Gradient should be near zero when mu = softmax(theta)."""
    theta = np.array([1.0, 2.0])
    mu = theta_to_prices(theta)
    grad = bregman_gradient(mu, theta)
    # At optimum: ln(mu_i) + 1 - theta_i should be constant (not zero)
    # but the gradient differences should be near zero
    diff = np.max(grad) - np.min(grad)
    assert diff < 1e-6


def test_prices_near_zero() -> None:
    """Prices near zero should not cause errors."""
    prices = np.array([0.001, 0.999])
    theta = prices_to_theta(prices)
    recovered = theta_to_prices(theta)
    np.testing.assert_allclose(recovered, prices, atol=1e-6)


def test_prices_near_one() -> None:
    """Prices near one should not cause errors."""
    prices = np.array([0.999, 0.001])
    theta = prices_to_theta(prices)
    recovered = theta_to_prices(theta)
    np.testing.assert_allclose(recovered, prices, atol=1e-6)


def test_three_outcome_market() -> None:
    """Functions should work with 3+ outcomes."""
    prices = np.array([0.5, 0.3, 0.2])
    theta = prices_to_theta(prices)
    mu = theta_to_prices(theta)
    np.testing.assert_allclose(mu, prices, atol=1e-10)
    div = kl_divergence(mu, theta)
    assert abs(div) < 1e-6
