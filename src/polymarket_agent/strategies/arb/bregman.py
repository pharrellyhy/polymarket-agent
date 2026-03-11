"""Bregman divergence and LMSR cost functions for arbitrage detection.

Implements the mathematical foundation from "Unravelling the Probabilistic
Forest: Arbitrage in Prediction Markets" (arXiv:2508.03474v1).
"""

import numpy as np
from numpy.typing import NDArray

_EPS: float = 1e-12


def negative_entropy(mu: NDArray[np.float64]) -> float:
    """Compute negative entropy R(mu) = sum(mu_i * ln(mu_i)).

    Args:
        mu: Probability distribution vector (must sum to ~1.0, all > 0).

    Returns:
        Negative entropy value.
    """
    safe_mu = np.clip(mu, _EPS, None)
    return float(np.sum(safe_mu * np.log(safe_mu)))


def lmsr_cost(theta: NDArray[np.float64]) -> float:
    """Compute LMSR cost function C(theta) = ln(sum(exp(theta_i))).

    Uses the log-sum-exp trick for numerical stability.

    Args:
        theta: LMSR parameter vector.

    Returns:
        Cost function value.
    """
    max_theta = np.max(theta)
    return float(max_theta + np.log(np.sum(np.exp(theta - max_theta))))


def kl_divergence(mu: NDArray[np.float64], theta: NDArray[np.float64]) -> float:
    """Compute KL divergence D(mu||theta) = R(mu) + C(theta) - dot(theta, mu).

    This is the Bregman divergence measuring mispricing between a belief
    distribution mu and market parameters theta.

    Args:
        mu: Probability distribution vector.
        theta: LMSR parameter vector.

    Returns:
        Non-negative divergence value.
    """
    return negative_entropy(mu) + lmsr_cost(theta) - float(np.dot(theta, mu))


def bregman_gradient(mu: NDArray[np.float64], theta: NDArray[np.float64]) -> NDArray[np.float64]:
    """Compute the gradient of the Bregman divergence w.r.t. mu.

    The gradient gives the optimal trading direction: grad = ln(mu) + 1 - theta.

    Args:
        mu: Probability distribution vector.
        theta: LMSR parameter vector.

    Returns:
        Gradient vector indicating trading direction.
    """
    safe_mu = np.clip(mu, _EPS, None)
    return np.log(safe_mu) + 1.0 - theta


def prices_to_theta(prices: NDArray[np.float64]) -> NDArray[np.float64]:
    """Convert market prices to LMSR parameter vector.

    theta_i = ln(p_i) (the inverse of the softmax mapping).

    Args:
        prices: Market outcome prices (should sum to ~1.0).

    Returns:
        LMSR parameter vector.
    """
    safe_prices = np.clip(prices, _EPS, None)
    return np.log(safe_prices)


def theta_to_prices(theta: NDArray[np.float64]) -> NDArray[np.float64]:
    """Convert LMSR parameters back to probability prices.

    Uses the softmax function: p_i = exp(theta_i) / sum(exp(theta_j)).

    Args:
        theta: LMSR parameter vector.

    Returns:
        Probability distribution (sums to 1.0).
    """
    max_theta = np.max(theta)
    exp_theta = np.exp(theta - max_theta)
    return exp_theta / np.sum(exp_theta)
