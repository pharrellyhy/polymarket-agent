"""Frank-Wolfe optimizer with integer programming oracle.

Implements the barrier Frank-Wolfe algorithm from "Unravelling the
Probabilistic Forest" for finding optimal arbitrage portfolios over
marginal polytopes.
"""

import logging
from dataclasses import dataclass, field
from typing import Sequence, cast

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import LinearConstraint, milp  # type: ignore[import-untyped]
from scipy.sparse import eye as speye  # type: ignore[import-untyped]

from polymarket_agent.strategies.arb.bregman import kl_divergence

logger = logging.getLogger(__name__)

_DEFAULT_ALPHA: float = 0.9
_DEFAULT_EPSILON: float = 0.1
_DEFAULT_MAX_ITER: int = 150
_DEFAULT_TOL: float = 1e-6


@dataclass
class MarginalPolytope:
    """Constraint representation for a marginal polytope.

    Encodes constraints of the form:
        A_ub @ x <= b_ub  (inequality)
        A_eq @ x == b_eq  (equality)
        bounds[i] = (lower, upper)  for each variable
    """

    n_vars: int
    A_ub: NDArray[np.float64] | None = None
    b_ub: NDArray[np.float64] | None = None
    A_eq: NDArray[np.float64] | None = None
    b_eq: NDArray[np.float64] | None = None
    bounds: list[tuple[float, float]] = field(default_factory=list)
    integrality: NDArray[np.int32] | None = None

    def __post_init__(self) -> None:
        if not self.bounds:
            self.bounds = [(0.0, 1.0)] * self.n_vars
        if self.integrality is None:
            self.integrality = np.zeros(self.n_vars, dtype=np.int32)


def build_single_market_polytope(n_outcomes: int) -> MarginalPolytope:
    """Build a simplex polytope for a single market (probabilities sum to 1).

    Args:
        n_outcomes: Number of outcomes in the market.

    Returns:
        MarginalPolytope with sum-to-one equality constraint.
    """
    A_eq = np.ones((1, n_outcomes), dtype=np.float64)
    b_eq = np.array([1.0], dtype=np.float64)
    return MarginalPolytope(
        n_vars=n_outcomes,
        A_eq=A_eq,
        b_eq=b_eq,
    )


def build_multi_market_polytope(valid_combinations: Sequence[tuple[int, ...]]) -> MarginalPolytope:
    """Build a marginal polytope from valid outcome combinations.

    Each combination is a tuple of outcome indices (one per market).
    The polytope is the convex hull of indicator vectors for valid combos.

    Args:
        valid_combinations: List of tuples, each being a valid joint outcome.

    Returns:
        MarginalPolytope encoding the valid combinations.
    """
    if not valid_combinations:
        raise ValueError("valid_combinations must not be empty")

    n_combos = len(valid_combinations)
    n_vars = n_combos

    # Each variable represents the weight on a valid combination.
    # Weights must sum to 1 and be in [0, 1].
    A_eq = np.ones((1, n_vars), dtype=np.float64)
    b_eq = np.array([1.0], dtype=np.float64)

    return MarginalPolytope(
        n_vars=n_vars,
        A_eq=A_eq,
        b_eq=b_eq,
        integrality=np.ones(n_vars, dtype=np.int32),
    )


def ip_oracle(gradient: NDArray[np.float64], polytope: MarginalPolytope) -> NDArray[np.float64]:
    """Find the extreme point of the polytope minimizing the linear objective.

    Solves: min <gradient, x> subject to polytope constraints.
    Uses scipy.optimize.milp for integer programming when needed.

    Args:
        gradient: Objective coefficient vector.
        polytope: Constraint polytope.

    Returns:
        Extreme point (vertex) of the polytope.
    """
    constraints = []
    if polytope.A_eq is not None and polytope.b_eq is not None:
        constraints.append(LinearConstraint(polytope.A_eq, polytope.b_eq, polytope.b_eq))
    if polytope.A_ub is not None and polytope.b_ub is not None:
        constraints.append(LinearConstraint(polytope.A_ub, -np.inf, polytope.b_ub))

    lb = np.array([b[0] for b in polytope.bounds], dtype=np.float64)
    ub = np.array([b[1] for b in polytope.bounds], dtype=np.float64)
    bounds_constraint = LinearConstraint(speye(polytope.n_vars), lb, ub)
    constraints.append(bounds_constraint)

    result = milp(
        c=gradient.astype(np.float64),
        constraints=constraints,
        integrality=polytope.integrality,
    )

    if not result.success:
        logger.warning("IP oracle failed: %s; returning uniform", result.message)
        x = np.ones(polytope.n_vars, dtype=np.float64) / polytope.n_vars
        return x

    return cast(NDArray[np.float64], result.x.astype(np.float64))


def barrier_frank_wolfe(
    theta: NDArray[np.float64],
    polytope: MarginalPolytope,
    alpha: float = _DEFAULT_ALPHA,
    epsilon: float = _DEFAULT_EPSILON,
    max_iter: int = _DEFAULT_MAX_ITER,
    tol: float = _DEFAULT_TOL,
) -> tuple[NDArray[np.float64], float]:
    """Run the barrier Frank-Wolfe algorithm to find optimal distribution.

    Finds mu* = argmin D_R(mu || theta) over the marginal polytope,
    where D_R is the KL divergence (Bregman divergence with negative entropy).

    Args:
        theta: LMSR parameter vector (from current market prices).
        polytope: Marginal polytope defining valid distributions.
        alpha: Barrier weight parameter (0 < alpha < 1).
        epsilon: Initial step size damping.
        max_iter: Maximum number of iterations.
        tol: Convergence tolerance on objective change.

    Returns:
        Tuple of (mu_star, divergence) where mu_star is the optimal
        distribution and divergence is the final KL divergence.
    """
    n = polytope.n_vars
    # Initialize mu at the center of the simplex
    mu = np.ones(n, dtype=np.float64) / n

    prev_obj = float("inf")
    for iteration in range(max_iter):
        # Gradient of the Bregman divergence: ln(mu) + 1 - theta
        safe_mu = np.clip(mu, 1e-12, None)
        grad = np.log(safe_mu) + 1.0 - theta

        # Add barrier term to prevent mu from hitting boundaries
        barrier_grad = -alpha / (safe_mu + 1e-12)
        total_grad = grad + epsilon * barrier_grad

        # Linear minimization oracle
        vertex = ip_oracle(total_grad, polytope)

        # Step size: diminishing 2/(k+2) schedule
        gamma = 2.0 / (iteration + 2.0)

        # Update mu
        mu = (1.0 - gamma) * mu + gamma * vertex
        mu = np.clip(mu, 1e-12, None)
        mu = mu / np.sum(mu)  # re-normalize

        # Check convergence
        obj = kl_divergence(mu, theta)
        if abs(prev_obj - obj) < tol:
            logger.debug("Frank-Wolfe converged at iteration %d (obj=%.8f)", iteration, obj)
            break
        prev_obj = obj

        # Decay epsilon
        epsilon *= 0.95

    divergence = kl_divergence(mu, theta)
    return mu, divergence
