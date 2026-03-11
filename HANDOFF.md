# Polymarket Agent — Handoff Document

Last updated: 2026-03-11

---

## Session Entry — 2026-03-11 (Review Pass: Cross-Market Arbitrage Path Simplification)

### Problem
Reviewing the newly added arbitrageur upgrade found that the cross-market dependency path was not actually executable for dependent pairs:
1. `_process_component()` built a market-outcome price vector (`sum(len(outcomes))`) but `DependencyGraph.get_constraints()` returned a polytope sized to the number of valid joint combinations, so the advanced path bailed out on a dimension mismatch and never emitted cross-market signals
2. The implementation mixed pairwise dependency edges with a generic multi-market optimizer shape, which made the code harder to reason about than the supported behavior justified
3. The new optimizer module was not type-clean under the repo’s current mypy setup because `scipy` is untyped here and `milp().x` flowed back as `Any`

### Solution
- Simplified the cross-market arbitrage path to the behavior the detector actually supports today: binary market pairs connected by a single dependency edge
- Replaced the broken market-outcome vector approach with a joint-combination projection:
  - build the implied joint weights from the edge’s valid outcome pairs
  - optimize in joint-combination space with Frank-Wolfe
  - project the result back to per-market fair prices
  - emit buy signals only for materially underpriced outcomes that also pass execution validation
- Added a regression test proving a dependent pair now emits a real `cross_market` signal
- Tightened the touched files to pass Ruff and mypy cleanly

### Edits
- **Modified** `src/polymarket_agent/strategies/arbitrageur.py`
  - Removed the broken cross-market dimension-mismatch flow from `_process_component()`
  - Added explicit pairwise dependency handling via `_process_market_pair()`
  - Added `_build_cross_market_signal()` to keep cross-market signal construction small and consistent
  - Limited advanced cross-market optimization to supported binary market pairs; larger dependency components now fall back cleanly instead of pretending to be supported
- **Modified** `src/polymarket_agent/strategies/arb/frank_wolfe.py`
  - Widened `build_multi_market_polytope()` to accept `Sequence[tuple[int, ...]]`
  - Added local typing cleanup for untyped `scipy` imports and the MILP result cast
- **Modified** `tests/test_arbitrageur.py`
  - Added `test_arbitrageur_detects_cross_market_dependency_mispricing`
  - Moved dependency imports to the module top level and removed an unused import flagged by Ruff

### NOT Changed
- No changes to the dependency detector’s LLM prompt, heuristics, cache, or rate limiting
- No changes to single-market Bregman checks, simple price-sum fallback logic, or execution Kelly sizing
- No changes to config shape, orchestrator behavior, or execution-layer interfaces
- No full-suite rerun in this review pass; verification stayed scoped to the touched arbitrageur/arb files

### Verification
```bash
uv run pytest tests/test_arbitrageur.py::test_arbitrageur_detects_cross_market_dependency_mispricing -q
# 1 passed

uv run pytest tests/test_arbitrageur.py tests/test_arb_dependency.py tests/test_arb_frank_wolfe.py tests/test_arb_bregman.py tests/test_arb_execution_validator.py tests/test_position_sizing_execution_kelly.py -q
# 70 passed

uv run pytest tests/test_arbitrageur.py tests/test_arb_frank_wolfe.py -q
# 19 passed

uv run ruff check src/polymarket_agent/strategies/arbitrageur.py src/polymarket_agent/strategies/arb/frank_wolfe.py tests/test_arbitrageur.py
# All checks passed

uv run mypy src/polymarket_agent/strategies/arbitrageur.py src/polymarket_agent/strategies/arb/frank_wolfe.py
# Success: no issues found in 2 source files
```

---

## Session Entry — 2026-03-11 (Arbitrageur Upgrade: Research Paper Techniques)

### Problem
The existing Arbitrageur (~85 lines) only checked single-market yes+no price sum deviations. No cross-market dependency detection, no optimal trade computation, no execution-aware sizing. Research from "Unravelling the Probabilistic Forest" (arXiv:2508.03474v1) showed sophisticated bots extracted $40M from Polymarket using Bregman projections, Frank-Wolfe algorithms, and integer programming.

### Solution
Implemented a 5-layer pipeline upgrade to the Arbitrageur strategy:
- **L1: Dependency Detection** — LLM-driven cross-market dependency graph with candidate pair heuristics, TTL cache, and rate limiting (reuses SportsDerivativeTrader LLM infra pattern)
- **L2: Bregman Divergence** — Pure math module: negative entropy, LMSR cost, KL divergence, price↔theta conversions
- **L3: Frank-Wolfe Optimizer** — Barrier Frank-Wolfe with IP oracle via `scipy.optimize.milp`, marginal polytope constraints
- **L4: Execution Kelly** — Modified Kelly criterion `f = (bp - q) / b * sqrt(p)` incorporating execution probability, shared across all strategies
- **L5: Execution Validation** — VWAP estimation, slippage checking, and profit validation against real order book depth

Degrades gracefully: when `dependency_detection: false` or LLM unavailable, falls back to original price-sum checks. All existing tests pass unchanged.

### Edits
- **New** `src/polymarket_agent/strategies/arb/__init__.py` — empty package init
- **New** `src/polymarket_agent/strategies/arb/bregman.py` — negative_entropy, lmsr_cost, kl_divergence, bregman_gradient, prices_to_theta, theta_to_prices
- **New** `src/polymarket_agent/strategies/arb/frank_wolfe.py` — MarginalPolytope, build_single/multi_market_polytope, ip_oracle, barrier_frank_wolfe
- **New** `src/polymarket_agent/strategies/arb/dependency.py` — DependencyEdge, DependencyGraph, DependencyDetector (LLM client, caching, candidate pair selection)
- **New** `src/polymarket_agent/strategies/arb/execution_validator.py` — estimate_vwap, estimate_slippage, validate_execution
- **Modified** `src/polymarket_agent/strategies/arbitrageur.py` — rewritten as pipeline coordinator (~300 lines): advanced pipeline + simple fallback
- **Modified** `src/polymarket_agent/strategies/base.py` — added `execution_probability: float | None = None` to Signal
- **Modified** `src/polymarket_agent/position_sizing.py` — added `execution_kelly_size()` static method + `execution_kelly` branch in `compute_size()`
- **Modified** `src/polymarket_agent/config.py` — added `"execution_kelly"` to PositionSizingConfig.method Literal
- **Modified** `config.yaml` — added all new arbitrageur config params (dependency, bregman, frank-wolfe, execution)
- **Modified** `pyproject.toml` — added numpy>=1.26 and scipy>=1.12 dependencies
- **New** `tests/test_arb_bregman.py` — 13 tests (roundtrips, edge cases, numerical stability)
- **New** `tests/test_arb_frank_wolfe.py` — 8 tests (convergence, simplex, IP oracle)
- **New** `tests/test_arb_dependency.py` — 13 tests (graph, similarity, caching, mock LLM detection)
- **New** `tests/test_arb_execution_validator.py` — 10 tests (VWAP, slippage, validation scenarios)
- **New** `tests/test_position_sizing_execution_kelly.py` — 8 tests (computation, edge cases, compute_size integration)
- **Modified** `tests/test_arbitrageur.py` — expanded with backward compat, advanced config, pipeline integration, execution_probability tests

### NOT Changed
- No orchestrator changes (Arbitrageur still conforms to Strategy ABC)
- No changes to PaperTrader/LiveTrader execution layer
- No changes to other strategies
- No changes to signal aggregation or risk management

### Verification
```bash
uv run pytest tests/ -v
# 633 passed

uv run ruff check src/
# All checks passed

uv run ruff format src/
# Formatted
```

---

## Session Entry — 2026-03-06 (Review Pass: Autotune Param Exposure Cleanup)

### Problem
Reviewing the latest autotune parameter exposure changes found one maintainability issue and one weak test:
1. `_build_tunable_params()` in `cli.py` added another large run of repetitive `if key in strat_cfg` blocks, making future parameter additions easy to drift or mis-range
2. `tests/test_autotune.py` validated new parameter handling with placeholder paths (`strategies.sports...`, `strategies.date_curve...`) instead of the real config paths used by the app

### Solution
- Simplified `_build_tunable_params()` into table-driven loops for strategy params and exit-manager params, preserving the same ranges and descriptions while reducing repetition
- Tightened the autotune validation test to use the real dotted config paths for `sports_derivative_trader` and `date_curve_trader`

### Edits
- `src/polymarket_agent/cli.py`
  - Added `strategy_param_specs` metadata table for strategy-level tunables
  - Added `exit_manager_param_specs` metadata table
  - Replaced repetitive append blocks with loops over those spec tables
- `tests/test_autotune.py`
  - Replaced placeholder validation paths with real config paths:
    - `strategies.sports_derivative_trader.bracket_sum_tolerance`
    - `strategies.date_curve_trader.arb_confidence`

### NOT Changed
- No changes to autotune decision logic, clamping logic, or YAML apply behavior
- No changes to parameter ranges or descriptions exposed by the latest autotune feature
- No changes to strategy behavior, execution, or config schema

### Verification
```bash
uv run pytest tests/test_evaluate.py tests/test_autotune.py -q
# 44 passed

uv run ruff check src/polymarket_agent/cli.py src/polymarket_agent/autotune.py tests/test_evaluate.py tests/test_autotune.py
# All checks passed

uv run mypy src/polymarket_agent/cli.py src/polymarket_agent/autotune.py
# Success: no issues found in 2 source files
```

---

## Session Entry — 2026-03-06 (Autotune: Expose All Strategy Params)

### Problem
After adding 4 new strategies (technical_analyst, whale_follower, date_curve_trader, sports_derivative_trader), their strategy-specific parameters were not exposed to the autotune LLM. The `_build_tunable_params()` function only covered generic keys (order_size, volume_threshold, etc.) and the exit_manager section was entirely missing.

### Solution
- Added 9 new tunable parameter blocks to `_build_tunable_params()` covering whale_follower (`top_n`, `min_trade_size`), date_curve_trader (`arb_confidence`, `cache_ttl_seconds`), sports_derivative_trader (`bracket_sum_tolerance`, `cascade_min_move`, `cascade_confidence`, `hierarchy_confidence`, `min_volume_24h`)
- Added exit_manager section (`profit_target_pct`, `stop_loss_pct`, `max_hold_hours`) gated by `cfg.exit_manager.enabled`
- Updated LLM system prompt with guidance for new parameter categories
- Removed `from __future__ import annotations` per CLAUDE.md rule (Python 3.12+ target)

### Edits
- `src/polymarket_agent/cli.py`
  - Added 9 `if "key" in strat_cfg` blocks after `max_calls_per_hour` in `_build_tunable_params()`
  - Added exit_manager param section after conditional_orders block
- `src/polymarket_agent/autotune.py`
  - Added whale follower, date curve trader, sports derivative, and exit manager guidance to `_SYSTEM_PROMPT`
  - Removed `from __future__ import annotations` (line 7)
- `tests/test_evaluate.py`
  - Added 6 tests: whale_follower params, date_curve_trader params, sports_derivative params, exit_manager enabled/disabled
- `tests/test_autotune.py`
  - Added `test_validate_new_strategy_params` covering clamping, int type preservation, and valid ranges

### NOT Changed
- No changes to strategy logic, execution, orchestrator, or config schema
- No changes to existing tunable parameter blocks or their min/max ranges
- No changes to autotune validation/apply logic

### Verification
```bash
uv run pytest tests/test_autotune.py -v
# 22 passed

uv run pytest tests/ -v --tb=short
# 570 passed

ruff check src/polymarket_agent/autotune.py src/polymarket_agent/cli.py tests/test_autotune.py tests/test_evaluate.py
# All checks passed
```

---

## Session Entry — 2026-03-06 (Review Pass: Strategy Bugfix Rollout Follow-up)

### Problem
Reviewing the newly added strategy bugfix diff exposed two regressions:
1. `technical_analyst` converted bearish entries into `buy` orders on the No token, but still passed `side="buy"` into `_compute_confidence()`, so bearish setups were scored with bullish RSI/squeeze/MACD logic
2. `whale_follower` now always emits `buy` orders, but binary whale `sell` trades still reused the sold token instead of flipping to the complementary outcome

### Solution
- Kept the executed `buy` order for bearish `technical_analyst` entries, but separated the directional thesis used for confidence scoring (`sell` for bearish crossover)
- Simplified `whale_follower` token/price mapping behind a helper that flips binary sell trades to the complementary outcome before building the follow signal
- Added focused regression tests for both issues

### Edits
- `src/polymarket_agent/strategies/technical_analyst.py`
  - Added `confidence_side` in `_generate_signal()` so bearish No-token buys still use bearish confidence semantics
- `src/polymarket_agent/strategies/whale_follower.py`
  - Extracted `_resolve_follow_target()`
  - Binary `sell` trades now map to the complementary token and price
- `tests/test_technical_analyst.py`
  - Added `test_bearish_no_signal_uses_bearish_confidence_direction`
- `tests/test_whale_follower.py`
  - Added `test_whale_follower_sell_trade_buys_complementary_binary_token`

### NOT Changed
- No changes to strategy thresholds, order sizing, or dedup semantics
- No changes to the broader strategy bugfix batch already in progress
- No changes to config, orchestration, or execution-layer behavior

### Verification
```bash
uv run pytest tests/test_technical_analyst.py::test_bearish_no_signal_uses_bearish_confidence_direction -q
# 1 passed

uv run pytest tests/test_whale_follower.py::test_whale_follower_sell_trade_buys_complementary_binary_token -q
# 1 passed

uv run pytest tests/test_technical_analyst.py tests/test_whale_follower.py -q
# 25 passed

uv run ruff check src/polymarket_agent/strategies/technical_analyst.py src/polymarket_agent/strategies/whale_follower.py tests/test_technical_analyst.py tests/test_whale_follower.py
# All checks passed

uv run mypy src/polymarket_agent/strategies/technical_analyst.py src/polymarket_agent/strategies/whale_follower.py
# Success: no issues found in 2 source files
```

---

## Session Entry — 2026-03-06 (Strategy Bugfix Rollout: Tasks 1-4, 6)

### Problem
Five bugs materially reduced live P&L:
1. Bearish entry signals emit `side="sell"` but executor requires existing position → every bearish entry silently fails
2. `date_curve_trader` and `sports_derivative_trader` return `[]` when LLM client is None, blocking pure-math structural checks
3. `whale_follower` defaults to Yes token regardless of actual whale trade outcome
4. `whale_follower` dedup key `trader:market` suppresses distinct trades by same whale
5. `cross_platform_arb` is unhedged basis trade, not real arbitrage

### Solution
- **Task 1**: Normalized all bearish entries to buy complementary (No) token across 6 strategies: signal_trader, arbitrageur, ai_analyst, technical_analyst, date_curve_trader, sports_derivative_trader
- **Task 2**: Removed `if self._client is None: return []` guard from analyze(), gated only LLM enrichment calls, allowing structural checks (term structure, bracket sum, hierarchy, cascade) to run without LLM
- **Task 3**: Added `outcome_index` field to WhaleTrade model; whale_follower now maps trades to correct outcome token
- **Task 4**: Added `transaction_hash` field to WhaleTrade; dedup key changed from `trader:market` to transaction hash (or full trade fingerprint fallback)
- **Task 6**: Added `_enabled` guard (default False) to CrossPlatformArb.analyze(); all existing tests updated to pass `enabled: True`

### Edits
- `src/polymarket_agent/strategies/signal_trader.py` — bearish: `side="buy"`, `token_id=clob_token_ids[1]`, `target_price=1.0-yes_price`
- `src/polymarket_agent/strategies/arbitrageur.py` — overpriced sum: buy cheapest outcome instead of sell most expensive
- `src/polymarket_agent/strategies/ai_analyst.py` — negative divergence: buy No token instead of sell Yes
- `src/polymarket_agent/strategies/technical_analyst.py` — bearish EMA: buy No token, thread market through `_generate_signal`
- `src/polymarket_agent/strategies/date_curve_trader.py` — term_structure sell→buy No; curve divergence sell→buy No; removed LLM guard from analyze()
- `src/polymarket_agent/strategies/sports_derivative_trader.py` — bracket_sum/hierarchy/cascade/derivative sell→buy No; removed LLM guard from analyze()
- `src/polymarket_agent/strategies/whale_follower.py` — added outcome_index/transaction_hash parsing; dedup by tx hash; always buy
- `src/polymarket_agent/strategies/cross_platform_arb.py` — added `_enabled` guard defaulting to False
- `src/polymarket_agent/data/models.py` — added `outcome_index: int = 0` and `transaction_hash: str = ""` to WhaleTrade
- Updated 9 test files to match new semantics (buy No instead of sell, dedup by tx hash, enabled flag)

### NOT Changed
- ExitManager sell logic (position exits)
- MarketMaker sell logic (ask quotes)
- Paper/Live executor internals
- config.yaml (cross_platform_arb already had `enabled: false`)
- Orchestrator, aggregation, risk management

### Verification
```bash
uv run pytest tests/ -v --tb=short
# 562 passed

ruff check src/ tests/
# All checks passed
```

---

## Session Entry — 2026-03-05 (Review Pass: Sports Derivative Parser Robustness)

### Problem
`SportsDerivativeTrader._parse_derivative_analysis()` raised `ValueError` when an LLM response contained a malformed probability entry (for example `"probability": "not-a-number"`), causing the entire parse to fail instead of using remaining valid estimates.

### Solution
Hardened estimate parsing to skip malformed/non-finite probabilities and continue processing valid entries.

### Edits
- `src/polymarket_agent/strategies/sports_derivative_trader.py`
  - In `_parse_derivative_analysis()`, wrapped probability conversion in `try/except` and skipped non-finite values via `math.isfinite()`.
- `tests/test_sports_derivative_trader.py`
  - Added `test_parse_derivative_analysis_skips_invalid_probability_entries` (RED → GREEN).

### NOT Changed
- No changes to signal confidence math, divergence thresholds, or event graph construction.
- No config/schema/orchestrator changes in this pass.

### Verification
```bash
uv run pytest tests/test_sports_derivative_trader.py::test_parse_derivative_analysis_skips_invalid_probability_entries -q
# 1 passed

uv run pytest tests/test_sports_derivative_trader.py -q
# 27 passed

uv run ruff check src/polymarket_agent/strategies/sports_derivative_trader.py tests/test_sports_derivative_trader.py
# All checks passed

uv run mypy src/polymarket_agent/strategies/sports_derivative_trader.py
# Success: no issues found in 1 source file
```

---

## Session Entry — 2026-03-05 (SportsDerivativeTrader Strategy)

### Problem
Polymarket has sports prediction markets with derivative markets (series winner, championship, MVP) that are less efficiently priced than individual games. The agent excluded all sports markets via `config.yaml`. Needed a strategy to exploit cross-market inefficiencies in sports derivatives.

### Solution
Created `SportsDerivativeTrader` strategy following the `DateCurveTrader` pattern. Implements four analysis components:
1. **Bracket sum validation** — checks if sibling market probabilities sum to ~1.0
2. **Hierarchy consistency** — validates P(championship) <= P(series) per team
3. **Cascade signal detection** — detects when game resolution hasn't propagated to derivative markets
4. **LLM derivative analysis** — full event graph context for fair price estimation

### Edits
- `src/polymarket_agent/data/models.py` — Added `SportsMarketNode` and `SportsEventGraph` Pydantic models
- `src/polymarket_agent/strategies/sports_derivative_trader.py` — **New file**: full strategy with LLM client, cached event graph construction (LLM + regex fallback), four analysis components, news wiring
- `src/polymarket_agent/orchestrator.py` — Registered `SportsDerivativeTrader` in `STRATEGY_REGISTRY`, wired news provider
- `config.yaml` — Added `sports_derivative_trader` config section, removed `sports` from `excluded` categories
- `tests/test_sports_derivative_trader.py` — **New file**: 26 tests covering regex classification, bracket sum, hierarchy, cascade, LLM parsing, market identification, cache repricing
- `docs/plans/2026-03-05-sports-derivative-trader-design.md` — **New file**: design plan

### NOT Changed
- No changes to existing strategies, data client, execution layer, or other tests
- No changes to `config.py` (strategy config is dict-based, no schema changes needed)

### Verification
```bash
uv run pytest tests/test_sports_derivative_trader.py -v  # 26 passed
uv run pytest tests/ -v                                   # 558 passed
ruff check src/                                           # clean
mypy src/                                                 # only pre-existing import-not-found errors
```

---

## Session Entry — 2026-03-05 (Review Pass: DateCurve Regex Base Question Fix)

### Problem
`DateCurveTrader._detect_curves_regex()` built `base_question` using lowercase string splits (`" by "` / `" before "`), which failed on case variants like `"BY"` and left date fragments in the curve label.

### Solution
Simplified `_detect_curves_regex()` to reuse `_extract_base_question()` for base label extraction, making behavior case-insensitive and consistent with existing helper logic.

### Edits
- `src/polymarket_agent/strategies/date_curve_trader.py`
  - Replaced inline split-chain parsing with `_extract_base_question(items[0][1].question)` when constructing regex-detected curves.
- `tests/test_date_curve_trader.py`
  - Added `test_detect_curves_regex_base_question_handles_case_variants` (RED → GREEN).

### NOT Changed
- No changes to LLM prompts, divergence thresholds, term-structure math, or orchestration wiring.
- No config or schema changes.

### Verification
```bash
uv run pytest tests/test_date_curve_trader.py::test_detect_curves_regex_base_question_handles_case_variants -q
# 1 passed

uv run pytest tests/test_date_curve_trader.py -q
# 23 passed

uv run ruff check src/polymarket_agent/strategies/date_curve_trader.py tests/test_date_curve_trader.py
# All checks passed

uv run mypy src/polymarket_agent/strategies/date_curve_trader.py
# Success: no issues found in 1 source file
```

---

## Session Entry — 2026-03-05 (DateCurveTrader Strategy)

### Problem
Polymarket has date-based prediction markets (e.g., "X by March 7?", "X by March 14?") forming cumulative probability curves. The agent treated each market independently, missing two edges: news-driven curve repricing and term structure arbitrage.

### Solution
Implemented a new `DateCurveTrader` strategy that:
1. Detects date-based market groups via LLM (cached 1hr) with regex fallback
2. Validates term structure monotonicity — violations emit high-confidence (0.9) arbitrage signals
3. Analyzes curves with LLM + news to find divergences — one LLM call per curve, not per market
4. Applies Platt scaling and sigmoid confidence (same as AIAnalyst)

### Edits
- `src/polymarket_agent/data/models.py` — Added `DateCurvePoint` and `DateCurve` Pydantic models
- `src/polymarket_agent/strategies/date_curve_trader.py` — **New file**: full strategy with curve detection (LLM + regex), term structure validation, news-driven analysis, LLM client init
- `src/polymarket_agent/orchestrator.py` — Imported `DateCurveTrader`, registered in `STRATEGY_REGISTRY`, wired news provider alongside `AIAnalyst`
- `config.yaml` — Added `date_curve_trader` section with defaults (enabled, min_divergence=0.10, arb_confidence=0.9, etc.)
- `tests/test_date_curve_trader.py` — **New file**: 22 unit tests covering date extraction, term structure, curve detection, LLM parsing, Platt scaling, integration

### NOT Changed
- No changes to existing strategies, signal aggregation, execution, or risk management
- AIAnalyst unchanged — DateCurveTrader replicates patterns but is fully independent
- No config schema changes (strategy configs are dict[str, Any])

### Verification
```bash
uv run pytest tests/test_date_curve_trader.py -v  # 22 passed
uv run pytest tests/ -v                            # 531 passed
ruff check src/                                    # All checks passed
mypy src/                                          # Only pre-existing import-not-found errors
```

---

## Session Entry — 2026-03-05 (Review Pass 4: Reflection Prompt Simplification)

### Problem
- Reflection prompt enrichment in `AIAnalyst` used a runtime import plus strict `isinstance(ReflectionEngine)` gating.
- That added unnecessary branching and made the integration harder to test with compatible engines/stubs.

### Solution
- Simplified the reflection path to use the attached engine interface directly (duck-typed method calls) inside the existing best-effort `try/except`.
- Removed the runtime import/type gate without changing fallback behavior: failures still degrade gracefully and skip lesson enrichment.
- Added a focused regression test that failed before the change and now passes.

### Edits
- `src/polymarket_agent/strategies/ai_analyst.py`
  - Simplified reflection enrichment block in `_evaluate()`:
    - removed inline `ReflectionEngine` import
    - removed `isinstance` check
    - now calls `retrieve_relevant_lessons()` and `format_lessons_for_prompt()` directly on the attached engine
- `tests/test_ai_analyst.py`
  - Added `test_ai_analyst_reflection_prompt_uses_attached_engine`

### NOT Changed
- No changes to signal direction, confidence math, debate mode, or rate limiting.
- No config or schema changes.

### Verification
```bash
uv run pytest tests/test_ai_analyst.py::test_ai_analyst_reflection_prompt_uses_attached_engine -q
# 1 passed

uv run pytest tests/test_ai_analyst.py -q
# 30 passed

uv run ruff check src/polymarket_agent/strategies/ai_analyst.py tests/test_ai_analyst.py
# All checks passed

uv run mypy src/polymarket_agent/strategies/ai_analyst.py
# Success: no issues found in 1 source file
```

### Branch
- Working branch: `main`

---

## Project Summary

**Polymarket Agent** is a Python auto-trading pipeline for Polymarket prediction markets. It wraps the official `polymarket` CLI (v0.1.4, installed via Homebrew) into a structured system with pluggable trading strategies, paper/live execution, and MCP server integration for AI agents.

## Current State: Phase 4 COMPLETE + Strategy Research + P&L Improvements

- 570 tests passing, ruff lint clean, mypy strict clean
- All 4 strategies implemented: SignalTrader, MarketMaker, Arbitrageur, AIAnalyst
- Signal aggregation integrated (groups by market+token+side, unique strategy consensus)
- MCP server with 14 tools: search_markets, get_market_detail, get_price_history, get_leaderboard, get_portfolio, get_signals, refresh_signals, place_trade, analyze_market, get_event, get_price, get_spread, get_volume, get_positions
- LiveTrader with py-clob-client for real order execution
- Risk management: max_position_size, max_daily_loss, max_open_orders enforced in Orchestrator
- CLI commands: `run` (with `--live` safety flag + config hot-reload), `status`, `tick`, `research`, `report`, `evaluate`, `backtest`, `dashboard`, `mcp`, `strategy-stats`
- Focus trading: configurable market filtering by search queries, IDs, or slugs; bracket market context for AI analyst
- Auto-tune pipeline: `scripts/autotune.sh` + launchd plist for periodic Claude Code-driven config tuning
- Exit manager: automatic position exits via profit target, stop loss, signal reversal, and staleness rules
- P&L attribution: signal outcome tracking with Brier scores, per-strategy accuracy/P&L reporting
- Adversarial debate: bull/bear/judge LLM debate pattern for higher-quality AIAnalyst signals
- Reflection memory: post-trade LLM reflection with FTS5 retrieval for institutional learning
- Kelly calibration: historical win-rate calibration table for position sizing
- Slippage modeling: configurable basis-point slippage in paper trading
- Performance-weighted aggregation: strategy weights from historical accuracy

## Architecture

```
CLI (Typer) → Orchestrator → Data Layer (CLI wrapper + cache + 30s timeout)
                           → Strategy Engine (pluggable ABCs)
                           → Signal Aggregation (dedup, confidence, consensus)
                           → Risk Gate (position size, daily loss, open orders)
                           → Execution Layer (Paper/Live via factory)
                           → SQLite (trade logging, context manager)

MCP Server (FastMCP, stdio) → AppContext → Orchestrator + Data + Config
  ├── search_markets, get_market_detail, get_price_history, get_leaderboard
  ├── get_portfolio, get_signals, refresh_signals
  ├── place_trade
  └── analyze_market
```

## File Map

| File | Purpose |
|------|---------|
| `src/polymarket_agent/cli.py` | Typer CLI: `run` (hot-reload), `status`, `tick`, `research`, `report`, `evaluate`, `backtest`, `dashboard`, `mcp` |
| `src/polymarket_agent/orchestrator.py` | Main loop: fetch → analyze → aggregate → execute |
| `src/polymarket_agent/config.py` | Pydantic config from YAML (incl. AggregationConfig) |
| `src/polymarket_agent/data/models.py` | Market, Event, OrderBook, PricePoint, Trader, Spread, Volume, Position (Pydantic) |
| `src/polymarket_agent/data/client.py` | CLI wrapper with TTL caching + 12 public methods |
| `src/polymarket_agent/data/cache.py` | In-memory TTL cache |
| `src/polymarket_agent/db.py` | SQLite trade logging |
| `src/polymarket_agent/strategies/base.py` | Strategy ABC + Signal dataclass |
| `src/polymarket_agent/strategies/signal_trader.py` | Volume-filtered directional signals |
| `src/polymarket_agent/strategies/market_maker.py` | Bid/ask around orderbook midpoint |
| `src/polymarket_agent/strategies/arbitrageur.py` | Price-sum deviation detection |
| `src/polymarket_agent/strategies/ai_analyst.py` | Claude probability estimates + divergence trading |
| `src/polymarket_agent/strategies/exit_manager.py` | ExitManager: generates sell signals for held positions (5 exit rules incl. trailing stop) |
| `src/polymarket_agent/strategies/aggregator.py` | Signal dedup, confidence blending, conflict resolution, consensus |
| `src/polymarket_agent/execution/base.py` | Executor ABC + Portfolio + Order + cancel_order/get_open_orders |
| `src/polymarket_agent/execution/paper.py` | Paper trading with virtual USDC |
| `src/polymarket_agent/execution/live.py` | Live trading via py-clob-client (optional dep) |
| `src/polymarket_agent/mcp_server.py` | MCP server: 14 tools, lifespan context, module-level `mcp` + `configure()` |
| `config.yaml` | Default config (paper mode, $1000, 4 strategies, aggregation) |
| `pyproject.toml` | Project config (deps incl. mcp>=1.0, ruff, mypy) |

## Key Design Decisions

1. **All data comes from `polymarket` CLI** — subprocess with `-o json`, parsed through `PolymarketData._run_cli()`. Never call subprocess directly elsewhere.
2. **Null-safe parsing** — Polymarket CLI returns `null` for optional fields. Helper functions `_str_field()`, `_float_field()`, `_parse_json_field()` in models.py.
3. **Strategy ABC** — All strategies implement `analyze(markets, data) -> list[Signal]`. Register in `STRATEGY_REGISTRY` dict in `orchestrator.py`.
4. **Executor ABC** — `place_order(signal) -> Order | None`. Paper and Live share interface.
5. **Signal dataclass** — `strategy`, `market_id`, `token_id`, `side` (buy/sell), `confidence`, `target_price`, `size` (USDC), `reason`.
6. **Signal aggregation** — `aggregate_signals()` first suppresses conflicting signals (opposite sides on same market+token), then groups by `(market_id, token_id, side)`, blends confidence (group average), filters by `min_confidence`, enforces `min_strategies` consensus (unique strategy names). Runs between strategy collection and execution in `tick()`.
7. **MCP server** — FastMCP with lifespan context. `AppContext` dataclass holds Orchestrator + PolymarketData + AppConfig. Tools are thin wrappers that delegate to existing layers. `configure()` sets config/db paths for the module-level `mcp` instance before `mcp.run()`.
8. **Executor factory** — `_build_executor()` in Orchestrator selects PaperTrader or LiveTrader based on `config.mode`. Live mode lazily imports LiveTrader and reads env vars.
9. **Risk gate** — `_check_risk()` enforces max_position_size, max_daily_loss, max_open_orders before every trade in `tick()`.
10. **LiveTrader** — Thin wrapper around py-clob-client ClobClient. Limit orders (GTC), lazy imports, `from_env()` factory for env var auth.

## Known Issues from Code Review

### Critical
1. ~~**mypy strict fails**~~ — FIXED in Phase 2 Task 1.
2. ~~**OrderBook.midpoint/spread**~~ — FIXED. Returns 0.0 when either side is empty.
3. ~~**Risk config not enforced**~~ — FIXED in Phase 4. `_check_risk()` enforces all 3 limits.
4. ~~**Signal.size semantics**~~ — FIXED. Docstring clarifies size is always USDC; execution layer converts to shares.
5. ~~**Database connection never closed**~~ — FIXED in Phase 4. Context manager + `orch.close()` in CLI.

### Important
6. ~~**No subprocess timeout**~~ — FIXED in Phase 4. `_run_cli()` has 30s timeout.
7. ~~**Empty token_id signals**~~ — FIXED. SignalTrader now skips markets with missing token IDs.
8. ~~**No sell-side test coverage**~~ — FIXED. 5 sell-side PaperTrader tests added.
9. ~~**Config path relative to CWD**~~ — FIXED. CLI now logs a warning when config file not found.
10. ~~**MarketMaker `_max_inventory` not enforced**~~ — FIXED in Phase 4. Removed dead config; position limits enforced by Orchestrator risk gate.
11. ~~**Arbitrageur `_min_deviation` not used**~~ — FIXED in Phase 4. Now used in signal gating logic.
12. ~~**Prompt injection surface**~~ — FIXED in Phase 4. AIAnalyst sanitizes external text (strip control chars, truncate, delimiters).
13. ~~**MCP tools access private attributes**~~ — FIXED in Phase 3. Public methods added to Orchestrator.

## How to Work With This Codebase

### Setup
```bash
cd /Users/pharrelly/codebase/github/polymarket-agent
uv sync
```

### Run Tests
```bash
uv run pytest tests/ -v                    # all 141 tests
```

### Lint & Type Check
```bash
uv run ruff check src/                     # lint (currently clean)
uv run mypy src/                           # type check (currently clean)
```

### Smoke Test
```bash
uv run polymarket-agent tick               # single trading cycle with live data
uv run polymarket-agent status             # portfolio view
uv run polymarket-agent run                # continuous loop (Ctrl+C to stop)
uv run polymarket-agent mcp                # start MCP server (stdio transport)
```

### Adding a New Strategy
1. Create `src/polymarket_agent/strategies/<name>.py` implementing `Strategy` ABC
2. Add to `STRATEGY_REGISTRY` in `orchestrator.py`
3. Add config block in `config.yaml` under `strategies:`
4. Write tests in `tests/test_<name>.py`

### Using the MCP Server
The MCP server runs via stdio transport. To use with Claude Code, add to your MCP config:
```json
{
  "mcpServers": {
    "polymarket": {
      "command": "uv",
      "args": ["run", "polymarket-agent", "mcp"]
    }
  }
}
```

## Phase Plan

| Phase | Status | Description |
|-------|--------|-------------|
| **1: Data + Paper Trading** | COMPLETE | CLI wrapper, models, cache, paper executor, SignalTrader, orchestrator, CLI |
| **2: Strategy Modules** | COMPLETE | MarketMaker, Arbitrageur, AIAnalyst, signal aggregation, config, integration test |
| **3: MCP Server** | COMPLETE | 9 MCP tools, lifespan context, CLI integration, expanded MCP test coverage |
| **4: Live Trading** | COMPLETE | LiveTrader (py-clob-client), risk management, executor factory, DB cleanup, CLI safety |

## Phase 3 Implementation Plan

See `docs/plans/2026-02-26-polymarket-agent-phase3.md` for detailed plan with 6 tasks:

1. ~~Add mcp dependency + data layer additions~~ DONE
2. ~~MCP server core — lifespan + read-only tools~~ DONE
3. ~~MCP portfolio, signals, trading tools~~ DONE
4. ~~MCP analyze_market tool~~ DONE
5. ~~CLI integration + config~~ DONE
6. ~~Tests + final verification~~ DONE

## Dependencies

**Runtime:** pydantic>=2.0, pyyaml>=6.0, typer>=0.9, anthropic>=0.84, mcp>=1.0
**Optional (live):** py-clob-client>=0.0.1 (`pip install polymarket-agent[live]`)
**Dev:** pytest>=8.0, pytest-mock>=3.0, ruff>=0.4, mypy>=1.10, types-PyYAML
**External:** `polymarket` CLI (Homebrew: `brew install polymarket`)

## Design Documents

- `docs/plans/2026-02-25-polymarket-agent-trader-design.md` — Full system design (architecture, all phases)
- `docs/plans/2026-02-25-polymarket-agent-phase1.md` — Phase 1 implementation plan (COMPLETE)
- `docs/plans/2026-02-25-polymarket-agent-phase2.md` — Phase 2 implementation plan (COMPLETE)
- `docs/plans/2026-02-26-polymarket-agent-phase3.md` — Phase 3 implementation plan (COMPLETE)
- `docs/plans/2026-02-27-exit-manager-design.md` — Exit manager design document
- `docs/plans/2026-02-27-exit-manager-plan.md` — Exit manager implementation plan
- `docs/plans/2026-02-28-focus-trading.md` — Focus trading + research command design
- `docs/plans/2026-03-03-strategy-research-improvements.md` — Strategy research improvements (Phases 1–3 implemented, Phases 4–8 planned)
- `docs/plans/2026-03-03-paper-trading-test-plan-design.md` — Paper-trading A/B test plan design
- `docs/plans/2026-03-03-paper-trading-test-plan.md` — Paper-trading test plan implementation
- `docs/plans/2026-03-04-pnl-improvements-tradingagents.md` — P&L improvements plan (7 phases, all implemented)

## Verification Commands

```bash
# Everything should pass
uv run pytest tests/ -v           # 489 tests passing
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 35 source files
uv run polymarket-agent tick      # Fetches live data, paper trades (focus-filtered)
uv run polymarket-agent research "Elon Musk"  # Deep bracket market analysis
uv run polymarket-agent mcp       # Starts MCP server (stdio transport)
uv run polymarket-agent run --live  # Live trading (requires POLYMARKET_PRIVATE_KEY)
```
