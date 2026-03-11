# Plan: Upgrade Arbitrageur with Research Paper Techniques

## Context

Prompted by [RohOnChain's article](https://x.com/RohOnChain/status/2017314080395296995) (saved in `docs/math_for_trading_polymarket.txt`) which breaks down the math from "Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets" (arXiv:2508.03474v1). The paper shows how sophisticated bots extracted $40M from Polymarket using Bregman projections, Frank-Wolfe algorithms, and integer programming — not just simple "do prices sum to 1?" checks.

**Current state:** Our `Arbitrageur` (~85 lines) only checks single-market yes+no sum deviations. No cross-market dependency detection, no optimal trade computation, no execution-aware sizing.

**Goal:** Upgrade the existing Arbitrageur and position sizing infrastructure with all 5 layers from the article, while preserving backward compatibility.

---

## Architecture: 5-Layer Pipeline

The upgraded Arbitrageur's `analyze()` method orchestrates a pipeline through composable modules in `src/polymarket_agent/strategies/arb/`.

```
Markets → [L1: Dependency Detection] → [L2: Bregman Divergence] → [L3: Frank-Wolfe] → [L4: Kelly Sizing] → [L5: Execution Validation] → Signals
                                                                                                              ↑ shared component
                                                                                                              (all strategies)
```

---

## Step 1: Mathematical Foundation — Bregman Module

**New file:** `src/polymarket_agent/strategies/arb/bregman.py`

Pure math, no dependencies. Functions:

- `negative_entropy(mu: ndarray) -> float` — R(mu) = sum(mu_i * ln(mu_i))
- `lmsr_cost(theta: ndarray) -> float` — C(theta) = ln(sum(exp(theta_i)))
- `kl_divergence(mu: ndarray, theta: ndarray) -> float` — D(mu||theta) = R(mu) + C(theta) - dot(theta, mu)
- `bregman_gradient(mu: ndarray, theta: ndarray) -> ndarray` — trading direction
- `prices_to_theta(prices: ndarray) -> ndarray` — market prices to LMSR parameters
- `theta_to_prices(theta: ndarray) -> ndarray` — LMSR parameters back to probabilities

Uses `numpy` (already a transitive dep via scipy).

**Test:** `tests/test_arb_bregman.py` — verify against known values, edge cases (prices near 0/1).

---

## Step 2: Frank-Wolfe Optimizer

**New file:** `src/polymarket_agent/strategies/arb/frank_wolfe.py`

Uses `scipy.optimize.milp` for the IP oracle.

- `MarginalPolytope` dataclass — constraint matrices (A_ub, b_ub, A_eq, b_eq, bounds)
- `build_single_market_polytope(n_outcomes: int) -> MarginalPolytope` — simplex constraint (sum=1)
- `build_multi_market_polytope(valid_combinations: list[tuple]) -> MarginalPolytope` — from dependency data
- `ip_oracle(gradient: ndarray, polytope: MarginalPolytope) -> ndarray` — find extreme point via `scipy.optimize.milp`
- `barrier_frank_wolfe(theta, polytope, alpha=0.9, epsilon=0.1, max_iter=150, tol=1e-6) -> tuple[ndarray, float]` — returns (mu_star, divergence)

Key parameters from article: alpha=0.9, initial_epsilon=0.1, convergence_threshold=1e-6, 50-150 iterations typical.

**Test:** `tests/test_arb_frank_wolfe.py` — convergence on 2-outcome, 3-outcome, and multi-market synthetic problems.

---

## Step 3: Cross-Market Dependency Detection

**New file:** `src/polymarket_agent/strategies/arb/dependency.py`

Reuses LLM infrastructure pattern from `SportsDerivativeTrader` (lines 121-230): `_init_client()`, `_call_llm()`, `_can_call()`, `set_news_provider()`.

- `DependencyEdge` dataclass — market_id_a, market_id_b, valid_combinations, relationship_type
- `DependencyGraph` class — holds edges, `connected_components()`, `get_constraints(market_ids) -> MarginalPolytope`
- `DependencyDetector` class — manages LLM calls + TTL cache
  - `detect(markets: list[Market]) -> DependencyGraph`
  - Candidate pair selection heuristics (same event group, question prefix similarity, keyword overlap) to avoid O(n^2) LLM calls
  - LLM prompt: given two market questions + outcomes, return valid outcome combinations as JSON

Config params (under `strategies.arbitrageur`):
```yaml
dependency_detection: true
dependency_provider: openai          # reuse same infra
dependency_model: openai/gpt-oss-120b
dependency_base_url: http://localhost:11567/v1
dependency_max_calls_per_hour: 50
dependency_cache_ttl: 3600
```

**Test:** `tests/test_arb_dependency.py` — mock LLM, test graph construction, caching, candidate pair filtering.

---

## Step 4: Execution Validation

**New file:** `src/polymarket_agent/strategies/arb/execution_validator.py`

Uses existing `DataProvider.get_orderbook(token_id)` and `OrderBook`/`OrderBookLevel` models from `data/models.py`.

- `estimate_vwap(orderbook: OrderBook, size: float, side: str) -> tuple[float, float]` — returns (vwap_price, available_size)
- `estimate_slippage(vwap_price: float, mid_price: float) -> float`
- `validate_execution(data: DataProvider, token_id: str, size: float, side: str, expected_profit: float, min_profit: float, max_slippage: float) -> tuple[bool, str]`

Lives inside the strategy (pre-signal-emission filter), not in the executor. PaperTrader/LiveTrader remain unchanged.

Config params:
```yaml
min_profit_threshold: 0.05
max_slippage_pct: 0.02
vwap_depth_levels: 5
```

**Test:** `tests/test_arb_execution_validator.py` — mock orderbooks, edge cases.

---

## Step 5: Modified Kelly Criterion (Shared Component)

**Modify:** `src/polymarket_agent/position_sizing.py`

Add `execution_kelly_size()` static method to `PositionSizer`:
```python
f = (b * p - q) / b * math.sqrt(p)
# b = (1/price) - 1 (decimal odds)
# p = confidence * execution_probability
# q = 1 - p
```

Cap at 50% of order book depth (applied in Arbitrageur before emitting signal).

**Modify:** `src/polymarket_agent/strategies/base.py`

Add optional field to Signal: `execution_probability: float | None = None`

**Modify:** `src/polymarket_agent/config.py`

Add `"execution_kelly"` to `PositionSizingConfig.method` Literal.

**Modify:** `src/polymarket_agent/position_sizing.py` — `compute_size()` branch for `execution_kelly`.

**Test:** `tests/test_position_sizing_execution_kelly.py`

---

## Step 6: Upgraded Arbitrageur

**Modify:** `src/polymarket_agent/strategies/arbitrageur.py`

Rewrite as pipeline coordinator (~150-200 lines). The `analyze()` method:

1. Build/update dependency graph via `DependencyDetector` (Layer 1)
2. For each connected component of dependent markets:
   a. Build `MarginalPolytope` from dependency constraints
   b. Compute `prices_to_theta()` for current prices
   c. Run `barrier_frank_wolfe()` to get mu* and divergence (Layers 2-3)
   d. Skip if divergence < `min_bregman_divergence`
   e. Compute trade direction from `bregman_gradient()`
   f. For each trade leg: validate execution (Layer 5), size via Kelly (Layer 4)
   g. Emit Signal with `execution_probability` set
3. Fallback: run existing `_check_price_sum()` for independent markets (backward compat)

When `dependency_detection: false` or LLM unavailable, the strategy degrades gracefully to the current simple mode.

New config params:
```yaml
arbitrageur:
  enabled: true
  price_sum_tolerance: 0.015    # existing (kept for simple mode)
  order_size: 10.0              # existing
  # Layer 1
  dependency_detection: true
  dependency_provider: openai
  dependency_model: openai/gpt-oss-120b
  dependency_base_url: http://localhost:11567/v1
  dependency_max_calls_per_hour: 50
  dependency_cache_ttl: 3600
  # Layer 2
  min_bregman_divergence: 0.01
  # Layer 3
  fw_max_iterations: 150
  fw_convergence_threshold: 0.000001
  fw_alpha: 0.9
  fw_initial_epsilon: 0.1
  # Layer 5
  min_profit_threshold: 0.05
  max_slippage_pct: 0.02
```

**Test:** `tests/test_arbitrageur.py` — expand with full pipeline integration (mocked LLM + data), backward compat test.

---

## Step 7: Package Init & Config Updates

**New file:** `src/polymarket_agent/strategies/arb/__init__.py` — empty package init

**Modify:** `config.yaml` — add new arbitrageur params (above)

No orchestrator changes needed — Arbitrageur still conforms to Strategy ABC.

---

## Files Summary

| Action | File |
|--------|------|
| **New** | `src/polymarket_agent/strategies/arb/__init__.py` |
| **New** | `src/polymarket_agent/strategies/arb/bregman.py` |
| **New** | `src/polymarket_agent/strategies/arb/frank_wolfe.py` |
| **New** | `src/polymarket_agent/strategies/arb/dependency.py` |
| **New** | `src/polymarket_agent/strategies/arb/execution_validator.py` |
| **New** | `tests/test_arb_bregman.py` |
| **New** | `tests/test_arb_frank_wolfe.py` |
| **New** | `tests/test_arb_dependency.py` |
| **New** | `tests/test_arb_execution_validator.py` |
| **New** | `tests/test_position_sizing_execution_kelly.py` |
| **Modify** | `src/polymarket_agent/strategies/arbitrageur.py` — rewrite as pipeline coordinator |
| **Modify** | `src/polymarket_agent/strategies/base.py` — add `execution_probability` to Signal |
| **Modify** | `src/polymarket_agent/position_sizing.py` — add `execution_kelly_size()` method |
| **Modify** | `src/polymarket_agent/config.py` — add `execution_kelly` to method Literal |
| **Modify** | `config.yaml` — add new arbitrageur config params |
| **Modify** | `tests/test_arbitrageur.py` — expand with integration tests |

## Reuse Points

- LLM infra pattern: `SportsDerivativeTrader._init_client()`, `_call_llm()`, `_can_call()` (lines 121-230 of `sports_derivative_trader.py`)
- OrderBook/OrderBookLevel models: `data/models.py` (lines 174-327)
- TTL caching pattern: `data/cache.py`
- Existing `PositionSizer.kelly_size()` as base for execution_kelly variant
- Existing `_check_price_sum()` preserved as fallback in upgraded Arbitrageur

---

## Verification

```bash
# Unit tests for each layer
uv run pytest tests/test_arb_bregman.py -v
uv run pytest tests/test_arb_frank_wolfe.py -v
uv run pytest tests/test_arb_dependency.py -v
uv run pytest tests/test_arb_execution_validator.py -v
uv run pytest tests/test_position_sizing_execution_kelly.py -v

# Integration test
uv run pytest tests/test_arbitrageur.py -v

# All tests (ensure no regressions)
uv run pytest tests/ -v

# Type checking
mypy src/

# Lint
ruff check src/
ruff format src/

# Smoke test: run a single tick
uv run polymarket-agent tick
```
