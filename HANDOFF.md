# Polymarket Agent — Handoff Document

Last updated: 2026-03-03

---

## Session Entry — 2026-03-03 (Review Pass 2: Toggle Semantics + Metrics Script Fixes)

### Problem
- The latest paper-trading toggle rollout worked, but review found two practical issues:
- `TechnicalAnalyst` reason strings still included `macd=`/`regime=` even when those features were disabled via config flags, which made A/B logs misleading.
- `scripts/compare_test_runs.py` counted all `signal_log` rows as “signals generated” (including `executed`/`rejected` statuses), and depended on SQL `SQRT`, which is less portable across SQLite builds.
- New AIAnalyst toggle flags (`platt_scaling`, `sigmoid_confidence`) had no direct tests.

### Solution
- Aligned TA reason output with feature toggles:
- `macd=` is now emitted only when `macd_enabled` is true.
- `regime=` is now emitted only when `regime_adaptive` is true.
- Hardened comparison metrics:
- Signal/confidence metrics now use only `status='generated'` rows to avoid double counting.
- Standard deviation is now computed in Python (`math.sqrt`) from mean and mean-square values.
- Added SQLite read error handling in the script for clearer failures.
- Added focused tests for AIAnalyst toggle behavior and stronger TA toggle assertions.

### Edits
- `src/polymarket_agent/strategies/technical_analyst.py`
  - Converted `_build_reason()` from static to instance method.
  - Gated `macd=` and `regime=` reason fields behind `_macd_enabled` and `_regime_adaptive`.
- `tests/test_technical_analyst.py`
  - Strengthened existing toggle tests to assert reason-field presence/absence with flags on/off.
- `tests/test_ai_analyst.py`
  - Added `test_ai_analyst_platt_scaling_toggle_controls_extremization`.
  - Added `test_ai_analyst_sigmoid_confidence_toggle_controls_mapping`.
- `scripts/compare_test_runs.py`
  - Filtered signal metrics to `status='generated'`.
  - Replaced SQL `SQRT` usage with Python variance/std-dev computation.
  - Added `sqlite3.Error` handling and returned structured script errors.

### NOT Changed
- No changes to strategy signal direction logic.
- No changes to execution layer, DB schema, or orchestrator control flow.
- No changes to default flag values (all remain `true` unless disabled in config profiles).

### Verification
```bash
uv run pytest tests/test_ai_analyst.py tests/test_technical_analyst.py tests/test_aggregator.py -q
# 50 passed in 0.71s

uv run ruff check src/polymarket_agent/strategies/technical_analyst.py scripts/compare_test_runs.py tests/test_ai_analyst.py tests/test_technical_analyst.py tests/test_aggregator.py
# All checks passed!

uv run mypy src/polymarket_agent/strategies/technical_analyst.py
# Success: no issues found in 1 source file

uv run python scripts/compare_test_runs.py --db-dir data
# Script runs; warns and prints N/A table when profile DBs are not present.
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-03-03 (Paper-Trading Test Plan: Config Flags + A/B Profiles)

### Problem
- Phase 1–3 strategy improvements (Platt scaling, sigmoid confidence, MACD, regime detection, confidence blending, conflict resolution, trailing stop) were all hardcoded — no way to disable them for A/B comparison.
- The implementation plan (`docs/plans/2026-03-03-strategy-research-improvements.md`) was missing 3 items from the research doc: multi-timeframe analysis, multi-model ensemble voting, signal accuracy tracking + performance-weighted aggregation + agentic search. These were added as Phases 4–8.
- No test infrastructure existed for running the agent in paper mode against live data with controlled feature toggles.

### Solution
- Added 6 config toggle flags across 3 strategy files, each defaulting to `true` (preserving current behavior):
  - `ai_analyst.platt_scaling`, `ai_analyst.sigmoid_confidence`
  - `technical_analyst.macd_enabled`, `technical_analyst.regime_adaptive`
  - `aggregation.conflict_resolution`, `aggregation.blend_confidence`
- Created 4 YAML config profiles in `configs/test/` (baseline → phase1 → phase2 → phase3), each layering one more phase of features.
- Built `scripts/compare_test_runs.py` to query each profile's SQLite DB and print a side-by-side metrics table.
- Updated the strategy research plan with Phases 4–8 from a separate design (ensemble voting, multi-timeframe, signal accuracy, performance-weighted aggregation, agentic search).

### Edits
- `src/polymarket_agent/strategies/ai_analyst.py` — added `_platt_scaling` and `_sigmoid_confidence` flags to `__init__`/`configure()`; guarded `_extremize()` and sigmoid formula behind flags
- `src/polymarket_agent/strategies/technical_analyst.py` — added `_macd_enabled` and `_regime_adaptive` flags; converted `_compute_confidence` from `@staticmethod` to instance method; guarded regime weights and MACD scoring
- `src/polymarket_agent/strategies/aggregator.py` — added `conflict_resolution` and `blend_confidence` params to `aggregate_signals()`; guarded conflict suppression and confidence blending
- `src/polymarket_agent/config.py` — added `conflict_resolution` and `blend_confidence` fields to `AggregationConfig`
- `src/polymarket_agent/orchestrator.py` — pass new aggregation flags to `aggregate_signals()` at both call sites
- `tests/test_aggregator.py` — added `test_conflict_resolution_disabled_keeps_both_sides`, `test_blend_confidence_disabled_uses_max`
- `tests/test_technical_analyst.py` — added `test_macd_disabled_zeroes_macd_weight`, `test_regime_adaptive_disabled_uses_fixed_weights`
- `configs/test/baseline.yaml` — all Phase 1–3 features off, `method: fixed`
- `configs/test/phase1.yaml` — Platt scaling + sigmoid + fractional Kelly
- `configs/test/phase2.yaml` — + MACD + regime-adaptive weights
- `configs/test/phase3.yaml` — + conflict resolution + blending + trailing stop
- `scripts/compare_test_runs.py` — queries signal_log/trades tables, prints comparison
- `docs/plans/2026-03-03-strategy-research-improvements.md` — added Phases 4–8, dependency graph, updated status
- `docs/plans/2026-03-03-paper-trading-test-plan-design.md` — design doc for this work
- `docs/plans/2026-03-03-paper-trading-test-plan.md` — implementation plan for this work

### NOT Changed
- No changes to indicator computation logic (`indicators.py`), exit manager logic, or position sizing
- No changes to DB schema or CLI commands
- Strategy behavior unchanged when all flags are `true` (the default)

### Verification
```bash
uv run pytest tests/ -q                          # 447 passed
uv run python -c "from polymarket_agent.config import load_config; from pathlib import Path; c = load_config(Path('configs/test/baseline.yaml')); print(c.position_sizing.method)"  # fixed
uv run python -c "from polymarket_agent.config import load_config; from pathlib import Path; c = load_config(Path('configs/test/phase3.yaml')); print(c.position_sizing.method, c.aggregation.conflict_resolution)"  # fractional_kelly True
uv run python scripts/compare_test_runs.py --db-dir data  # prints clean table with zeros
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-03-03 (Code Review Follow-Up: Strategy Simplification Pass)

### Problem
- The newly added strategy-research code was functional, but review found a few cleanup opportunities:
- `adaptive_rsi_thresholds()` used `list.index()` percentile ranking, which can misclassify duplicate ATR values as low-volatility.
- `ExitManager` maintained trailing-stop high-water state even when trailing stops were disabled.
- Minor clarity issues remained (wording mismatch in aggregator docs and stale confidence description in `TechnicalAnalyst` docstring).

### Solution
- Simplified and hardened indicator internals:
- Replaced duplicate-sensitive ATR ranking logic with bisect-based midpoint ranking.
- Added an empty-input guard in `_compute_ema_series()`.
- Replaced MACD magic numbers with module constants used consistently by `compute_macd()` and divergence-series construction.
- Simplified `ExitManager` trailing-stop state handling:
- High-water marks are now tracked/cleaned only when `trailing_stop_enabled` is active.
- Removed an unnecessary default argument from `_check_exit()` to make token context explicit.
- Clarified docs/comments without behavior changes:
- Updated aggregator wording from “weighted average” to “average” (matching implementation).
- Updated `TechnicalAnalyst` class docstring to reflect regime-adaptive confidence.
- Added targeted regression tests for the reviewed paths.

### Edits
- `src/polymarket_agent/strategies/indicators.py`
  - Added duplicate-safe ATR percentile ranking (`bisect_left`/`bisect_right` midpoint).
  - Added empty-input guard in `_compute_ema_series()`.
  - Introduced `_MACD_*` constants and reused them in MACD/divergence paths.
  - Removed redundant branch in MACD crossover previous-signal selection.
- `src/polymarket_agent/strategies/exit_manager.py`
  - Limited high-water mark tracking/cleanup to trailing-stop-enabled mode.
  - Removed default `token_id` argument from `_check_exit()`.
- `src/polymarket_agent/strategies/aggregator.py`
  - Updated confidence blending comments/docstring wording for accuracy.
- `src/polymarket_agent/strategies/technical_analyst.py`
  - Updated confidence description in class docstring.
- `tests/test_indicators.py`
  - Added `test_adaptive_rsi_thresholds_flat_volatility_is_neutral`.
- `tests/test_exit_manager.py`
  - Added trailing-stop trigger test after pullback.
  - Added test ensuring disabled trailing-stop mode does not track high-water marks.

### NOT Changed
- No changes to strategy/executor interfaces or config shape.
- No DB/schema/orchestrator flow changes.
- No dependency changes.

### Verification
```bash
uv run pytest tests/test_indicators.py tests/test_exit_manager.py tests/test_technical_analyst.py tests/test_aggregator.py -q
# 54 passed in 0.21s

uv run ruff check src/polymarket_agent/strategies/indicators.py src/polymarket_agent/strategies/exit_manager.py src/polymarket_agent/strategies/aggregator.py src/polymarket_agent/strategies/technical_analyst.py tests/test_indicators.py tests/test_exit_manager.py
# All checks passed!

uv run mypy src/polymarket_agent/strategies/indicators.py src/polymarket_agent/strategies/exit_manager.py src/polymarket_agent/strategies/aggregator.py src/polymarket_agent/strategies/technical_analyst.py
# Success: no issues found in 4 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-03-03 (Strategy Research Improvements: 3-Phase Implementation)

### Problem
- AIAnalyst LLM estimates were hedged toward 0.5 (known LLM calibration bias), producing weak divergence signals.
- Confidence mapping was linear, giving too much confidence to small divergences and not enough to large ones.
- TechnicalAnalyst used fixed indicator weights regardless of market regime (trending vs ranging).
- MACD, divergence detection, ATR-based adaptive thresholds, and market regime classification were missing from the indicator toolkit.
- Stochastic RSI was computed but never used.
- Aggregator used winner-takes-all confidence (no blending) and allowed conflicting signals (opposite sides on same market+token) to pass through independently.
- ExitManager had no trailing stop despite config keys being defined in `config.yaml`.
- Position sizing was fixed instead of using the already-implemented fractional Kelly.

### Solution
**Phase 1 — Quick Wins:**
- Added Platt scaling (`_extremize()`, α=√3) to AIAnalyst output to correct LLM hedging bias (0.6→0.74, 0.8→0.89).
- Replaced linear confidence with sigmoid mapping: small divergences (<5%) → ~0, 15% → 0.5, 25%+ → ~1.0.
- Enabled fractional Kelly positioning in config (fraction=0.25, max_bet_pct=0.10).

**Phase 2 — Technical Indicators:**
- Added MACD (6/13/5 periods) with crossover detection.
- Added RSI/MACD divergence detection via swing-point analysis.
- Added ATR approximation (close-only) with adaptive RSI thresholds (low/normal/high vol → 65/35, 70/30, 75/25).
- Added market regime detection (trending/ranging/transitional from EMA slope + BB expansion).
- Replaced fixed TechnicalAnalyst weights with regime-adaptive weights (e.g., trending: EMA=0.45, MACD=0.30; ranging: RSI=0.40).
- Activated StochRSI as timing boost (+0.2 when confirming signal direction).

**Phase 3 — Aggregation & Exit:**
- Replaced winner-takes-all aggregation with confidence blending (group average).
- Added conflict resolution: opposite sides on same (market_id, token_id) → suppress both.
- Added trailing stop to ExitManager with high-water mark tracking, gated by `trailing_stop_enabled` config.
- Wired `trailing_stop_enabled`/`trailing_stop_pct` from config.yaml into `ExitManagerConfig`.
- Added structured scratchpad prompt for AIAnalyst (gated by `structured_prompt: false` — disabled by default for A/B testing).

### Edits
- `src/polymarket_agent/strategies/ai_analyst.py` — added `_extremize()`, sigmoid confidence, `_structured_prompt` config, scratchpad prompt template, updated regex parser for structured mode, adjusted OpenAI `max_tokens`
- `src/polymarket_agent/strategies/indicators.py` — added `MACDResult`, `DivergenceResult`, `RegimeResult` models; added `compute_macd()`, `compute_atr()`, `adaptive_rsi_thresholds()`, `detect_divergence()`, `detect_regime()`, `_compute_ema_series()`; updated `TechnicalContext` with new optional fields; updated `analyze_market_technicals()` to compute all new indicators
- `src/polymarket_agent/strategies/technical_analyst.py` — regime-adaptive weights in `_compute_confidence()`, MACD score component, StochRSI timing boost, updated `_build_reason()` with MACD/regime
- `src/polymarket_agent/strategies/aggregator.py` — conflict resolution pass, confidence blending replacing winner-takes-all
- `src/polymarket_agent/strategies/exit_manager.py` — trailing stop rule with high-water mark tracking, updated rule priority numbering
- `src/polymarket_agent/config.py` — added `trailing_stop_enabled`, `trailing_stop_pct` to `ExitManagerConfig`
- `config.yaml` — changed `position_sizing.method` from `fixed` to `fractional_kelly`
- `tests/test_aggregator.py` — updated for blended confidence, added conflict resolution test
- `tests/test_integration.py` — relaxed signal count assertions for conflict suppression
- `tests/test_technical_analyst.py` — updated bearish test data for adaptive RSI thresholds
- `docs/plans/2026-03-03-strategy-research-improvements.md` — **NEW** implementation plan document

### NOT Changed
- No changes to existing strategy interfaces or ABCs.
- No changes to execution layer, MCP server, or dashboard.
- No DB schema changes.
- No new dependencies.
- Position sizing code unchanged (fractional Kelly was already implemented, only config toggled).

### Verification
```bash
.venv/bin/python -m pytest tests/ -q  # 440 passed in 12.34s
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-03-02 (Paper Trading Test: New Strategy Capabilities)

### Problem
- 4 new capabilities (WhaleFollower, CrossPlatformArb, volatility enrichment, sentiment/keyword enrichment) needed live paper trading validation before production use.
- WhaleFollower's `get_leaderboard()` used wrong CLI path (`polymarket leaderboard` instead of `polymarket data leaderboard`), and `Trader.from_cli()` didn't parse the CLI's `user_name`/`proxy_wallet` fields.
- Kalshi and Metaculus APIs require authentication (401/403), making CrossPlatformArb inoperable without API keys.
- Reentry cooldown of 24h + max_open_orders of 10 blocked nearly all trading activity during experiments.

### Solution
- Fixed `get_leaderboard()` CLI path to `polymarket data leaderboard`.
- Added `address` field to `Trader` model; updated `from_cli()` to parse `user_name` and `proxy_wallet` from CLI output.
- Refactored `WhaleFollower` to use CLI-based `data.get_trader_trades()` (via `polymarket data trades`) instead of broken Gamma API `/activity` endpoint (404). Added slug-based market matching alongside ID matching.
- Added `get_trader_trades()` to `PolymarketData` client and `DataProvider` protocol.
- Updated whale follower tests for new CLI-based data flow.
- Ran 8 experiments (Phase 1: 1A/1B/1C enrichment smoke tests, Phase 2: 2A/2B strategy tests, Phase 3: 3A/3B combined, Phase 4: 4G tuning). All experiments pass with 0 errors.
- Config tuned: `min_divergence` 0.10→0.05, `reentry_cooldown_hours` 24→1, `max_open_orders` 10→30, sentiment+keyword enrichments enabled, focus disabled.

### Edits
- `src/polymarket_agent/data/client.py` — fixed leaderboard CLI path (`data leaderboard`); added `get_trader_trades()` method using `polymarket data trades`
- `src/polymarket_agent/data/models.py` — added `address` and `slug` fields to `Trader` and `WhaleTrade`; updated `Trader.from_cli()` to parse `user_name`, `proxy_wallet`, `rank` from CLI output
- `src/polymarket_agent/data/provider.py` — added `get_trader_trades()` to `DataProvider` protocol
- `src/polymarket_agent/strategies/whale_follower.py` — removed Gamma client dependency; refactored to use `data.get_trader_trades()` for CLI-based trade fetching; added slug-based market matching
- `tests/test_whale_follower.py` — rewritten tests for CLI-based data format (condition_id, slug, side fields)
- `config.yaml` — `min_divergence` 0.10→0.05, `reentry_cooldown_hours` 24→1, `max_open_orders` 10→30, sentiment+keyword enabled, focus disabled
- `scripts/run_experiment.sh` — **NEW** experiment runner helper
- `results/` — **NEW** directory with baseline and experiment JSON evaluations

### NOT Changed
- No changes to CrossPlatformArb code (blocked by API auth — needs Kalshi/Metaculus API keys)
- No changes to execution layer, DB schema, or orchestrator flow
- No changes to sentiment/keyword/volatility enrichment code (all passed smoke tests unchanged)

### Verification
```bash
uv run pytest tests/test_whale_follower.py -q  # 7 passed
uv run pytest tests/ -q                        # 439 passed
# Experiment results in results/exp_*.json
# Experiment logs in logs/exp_*.log
```

### Key Findings
| Capability | Status | Notes |
|---|---|---|
| Sentiment enrichment | WORKING | Extra LLM call per market, 0 errors |
| Keyword spike | WORKING | Cold start (needs 24h baseline), RSS queries execute |
| Both enrichments | WORKING | No resource contention |
| WhaleFollower | CODE FIXED | 0 signals in practice (no overlap between top-10 monthly traders and active 50 markets) |
| CrossPlatformArb | BLOCKED | Kalshi 401, Metaculus 403 — needs API credentials |
| Full stack (all 4) | WORKING | 0 crashes, 0 errors |
| min_divergence 0.05 | IMPROVED | Doubled signal count (3→6 per tick) |

---

## Session Entry — 2026-03-02 (Review Follow-Up: External Price Parsing + Whale Dedup State)

### Problem
- Review of the newly added strategy/data modules found two correctness risks:
- `WhaleFollower` consumed dedup state before confirming the traded market was currently active, which could suppress valid future signals.
- External price clients (`KalshiClient`, `MetaculusClient`) could raise on malformed numeric fields (`yes_ask`, `q2`) and drop otherwise valid rows in the same payload.

### Solution
- Added regression tests first (red) to lock expected behavior for:
- Whale dedup state only after market validation.
- Graceful skip of malformed external-price rows while preserving valid rows.
- Updated `WhaleFollower` signal generation order so dedup keys are added only after active-market checks pass.
- Simplified external client parsing with a shared probability coercion helper and defensive dict/list checks.
- Replaced cache return `type: ignore` patterns with explicit runtime list checks and fixed mypy-typed string/side normalization.

### Edits
- `src/polymarket_agent/strategies/whale_follower.py`
  - Moved dedup key check/set to occur after market/token validation.
  - Added explicit `Literal["buy", "sell"]` side normalization for type safety.
- `src/polymarket_agent/data/external_prices.py`
  - Added `_coerce_probability()` helper for robust probability parsing/clamping.
  - Hardened Kalshi/Metaculus row parsing with defensive type checks and invalid-row skipping.
  - Removed `type: ignore[return-value]` cache returns by using explicit list checks.
- `tests/test_whale_follower.py`
  - Added `test_whale_follower_does_not_dedup_before_market_match`.
- `tests/test_external_prices.py` (**NEW**)
  - Added parsing resilience tests for invalid Kalshi `yes_ask` and invalid Metaculus `q2`.

### NOT Changed
- No strategy scoring math changes (confidence/divergence formulas unchanged).
- No execution layer, DB schema, or orchestrator flow changes.
- No config shape/default changes.

### Verification
```bash
uv run pytest tests/test_whale_follower.py::test_whale_follower_does_not_dedup_before_market_match tests/test_external_prices.py -q
# RED first: 3 failed (expected), then GREEN: 3 passed

uv run pytest tests/test_whale_follower.py tests/test_cross_platform_arb.py tests/test_external_prices.py -q
# 17 passed

uv run ruff check src/polymarket_agent/data/external_prices.py src/polymarket_agent/strategies/whale_follower.py tests/test_external_prices.py tests/test_whale_follower.py
# All checks passed

uv run mypy src/polymarket_agent/data/external_prices.py src/polymarket_agent/strategies/whale_follower.py
# Success: no issues found in 2 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-03-02 (Strategy Enhancement: 4 New Trading Capabilities)

### Problem
- The agent lacked whale/smart money tracking, cross-platform arbitrage, volatility anomaly detection, and news sentiment analysis — all key capabilities identified from the Polymarket ecosystem.

### Solution
- **WhaleFollower strategy (standalone):** Queries leaderboard for top traders, fetches their recent activity via new `GammaClient`, emits follow signals for large trades. Confidence inversely proportional to rank. Deduplicates trader/market pairs.
- **CrossPlatformArb strategy (standalone):** Fetches prices from Kalshi and Metaculus via new `KalshiClient`/`MetaculusClient`, fuzzy-matches questions to Polymarket markets using `difflib.SequenceMatcher`, signals when divergence exceeds combined fee threshold.
- **Volatility anomaly detection (AIAnalyst enrichment):** New `volatility.py` module computes composite anomaly score from rate of change, acceleration, volume spike, BB width percentile, and spread widening. Injected as `--- VOLATILITY ANALYSIS ---` prompt section.
- **News sentiment + keyword spikes (AIAnalyst enrichment):** New `news/sentiment.py` with LLM-based sentiment scoring and `KeywordTracker` for Google RSS frequency tracking. Both inject prompt sections (`--- SENTIMENT ANALYSIS ---`, `--- KEYWORD SPIKES ---`). Toggleable via config.
- **GammaClient extraction:** Refactored orchestrator's inline Gamma API urllib code into a proper `GammaClient` class with TTL caching.
- **DataProvider protocol extended:** Added `get_leaderboard()` to support WhaleFollower.

### Edits
- `src/polymarket_agent/data/models.py` — added 5 Pydantic models: `WhaleTrade`, `CrossPlatformPrice`, `VolatilityReport`, `SentimentScore`, `KeywordSpike`
- `src/polymarket_agent/data/gamma_client.py` — **NEW** Gamma API client with TTL caching
- `src/polymarket_agent/data/external_prices.py` — **NEW** Kalshi + Metaculus API clients
- `src/polymarket_agent/data/provider.py` — extended `DataProvider` protocol with `get_leaderboard()`
- `src/polymarket_agent/strategies/volatility.py` — **NEW** volatility anomaly computation module
- `src/polymarket_agent/news/sentiment.py` — **NEW** sentiment scoring + keyword spike tracking
- `src/polymarket_agent/strategies/whale_follower.py` — **NEW** WhaleFollower standalone strategy
- `src/polymarket_agent/strategies/cross_platform_arb.py` — **NEW** CrossPlatformArb standalone strategy
- `src/polymarket_agent/strategies/ai_analyst.py` — integrated volatility report, sentiment scoring, keyword spike detection; added enrichment config flags
- `src/polymarket_agent/orchestrator.py` — registered WhaleFollower + CrossPlatformArb in `STRATEGY_REGISTRY`; refactored `_fetch_focus_markets_from_api()` to use `GammaClient`
- `config.yaml` — added `whale_follower` and `cross_platform_arb` strategy configs (disabled by default); added `volatility_enabled`, `sentiment_enabled`, `keyword_spike_enabled` toggles to `ai_analyst`
- `tests/test_volatility.py` — **NEW** 15 tests
- `tests/test_sentiment.py` — **NEW** 12 tests
- `tests/test_whale_follower.py` — **NEW** 6 tests
- `tests/test_cross_platform_arb.py` — **NEW** 8 tests

### NOT Changed
- No changes to existing strategy logic (SignalTrader, MarketMaker, Arbitrageur, TechnicalAnalyst)
- No changes to execution layer, MCP server, or dashboard
- No DB schema changes
- All 395 existing tests pass unchanged

### Verification
```bash
uv run pytest tests/ -v                    # 436 passed (41 new)
ruff check src/ tests/                     # All checks passed
ruff format --check src/ tests/            # All formatted
python3 -c "from polymarket_agent.orchestrator import STRATEGY_REGISTRY; print(list(STRATEGY_REGISTRY.keys()))"
# ['signal_trader', 'market_maker', 'arbitrageur', 'ai_analyst', 'technical_analyst', 'whale_follower', 'cross_platform_arb']
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-03-01 (Reentry Cooldown, Min Divergence, Thinking Model Support)

### Problem
- The AI analyst (using local Qwen3.5-35b-a3b via OpenAI-compatible API) was churning positions: exiting losers then immediately re-entering the same tokens, crystallizing losses repeatedly.
- `min_divergence: 0.01` was far too aggressive — the model traded on 1% disagreements with the market, generating noisy signals.
- The Qwen thinking model returned reasoning text before the final probability number, which the regex parser picked up as the first match (wrong number). Also `max_tokens` was too low for thinking output.
- No way to pass framework-specific parameters (e.g. `thinking_budget`, `enable_thinking`) to the OpenAI-compatible endpoint.

### Solution
- **Reentry cooldown:** Added `reentry_cooldown_hours` (default 24) to `RiskConfig`. Orchestrator tracks exited token IDs with timestamps in `_exited_tokens` dict. `_check_risk()` rejects buy signals for tokens exited within the cooldown window. Exits recorded from both ExitManager sells and conditional order triggers.
- **Raised min_divergence:** Changed from 0.01 to 0.10 in config — model only trades when its estimate diverges 10%+ from the market price.
- **Thinking model response parsing:** Changed `re.search()` (first match) to `re.findall()` + `matches[-1]` (last match) so thinking model reasoning text doesn't pollute the probability extraction. Truncated warning log to `text[:200]`.
- **Extra params passthrough:** Added `_extra_params` dict to AIAnalyst, loaded from `extra_params` config key, passed as `extra_body` to the OpenAI SDK. Allows setting `thinking_budget`, `enable_thinking`, etc.
- **Removed debug print:** Cleaned up leftover `print(content)` in the OpenAI response path.

### Edits
- `src/polymarket_agent/config.py` — added `reentry_cooldown_hours: int = 24` to `RiskConfig`
- `src/polymarket_agent/orchestrator.py` — added `_exited_tokens` tracking dict; record exits in exit manager execution and conditional order trigger paths; added reentry cooldown check in `_check_risk()`
- `src/polymarket_agent/strategies/ai_analyst.py` — added `_extra_params` field + `extra_body` passthrough; changed response parsing to use last regex match; removed debug `print`
- `config.yaml` — `min_divergence` 0.01→0.10; added `reentry_cooldown_hours: 24`; added `extra_params` block

### NOT Changed
- No changes to strategy logic, exit manager rules, or signal aggregation.
- No changes to DB schema, MCP server, or dashboard.
- No changes to Anthropic provider path (only OpenAI-compatible path affected).

### Verification
```bash
uv run pytest tests/ -v                     # 395 passed
# Live paper trading run confirmed:
# - Old settings (8h): -$32.90 (-7.3%), constant churn and re-entries
# - New settings (2.5h): -$5.74 (-1.4%), clean exits only, 0 re-entries
# - Positions reduced from 15 to 5 via disciplined exits, no new entries
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-28 (Review Follow-Up: Focus Limit Scope + Gamma Parsing Robustness)

### Problem
- Review of the latest focus fallback changes found two behavior risks:
  - `focus.max_brackets` truncated results even when the operator explicitly selected markets via `market_ids`/`market_slugs`.
  - Gamma fallback parsing could drop an entire response when one malformed market row raised during `Market.from_cli(...)`.

### Solution
- **Scoped bracket limiting:** Kept `max_brackets` for query-driven discovery, but skipped truncation when explicit selectors (`market_ids` or `market_slugs`) are present.
- **Robust Gamma row handling:** In `_fetch_focus_markets_from_api()`, parse each market row with per-item error handling so malformed rows are skipped while valid rows are still accepted.
- **TDD regression coverage:** Added two failing orchestrator tests first, then implemented the minimal fix.

### Edits
- `src/polymarket_agent/orchestrator.py` — limited `max_brackets` enforcement to non-explicit focus selectors; hardened Gamma market parsing to skip malformed rows
- `tests/test_orchestrator.py` — added:
  - `test_focus_filter_does_not_limit_explicit_market_ids`
  - `test_fetch_focus_markets_from_api_skips_malformed_rows`

### NOT Changed
- No changes to strategy logic, order execution, or DB schema.
- No changes to config defaults or polling/risk thresholds.
- No changes to CLI command behavior outside focus filtering internals.

### Verification
```bash
uv run python -m pytest tests/test_orchestrator.py::test_focus_filter_does_not_limit_explicit_market_ids tests/test_orchestrator.py::test_fetch_focus_markets_from_api_skips_malformed_rows -q  # failed first, then passed after fix
uv run python -m pytest tests/test_orchestrator.py -q  # 8 passed
ruff check src/polymarket_agent/orchestrator.py tests/test_orchestrator.py  # All checks passed
uv run mypy src/polymarket_agent/orchestrator.py  # Success: no issues found in 1 source file
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-28 (CLI Resilience, Gamma API Fallback, Paper Mode Tuning)

### Problem
- The `polymarket` CLI intermittently failed with exit code 1, crashing the entire trading loop on transient errors.
- The focus filter only searched CLI results (top ~50 markets by volume), so niche events like "US strikes Iran by...?" were invisible.
- Daily loss limits blocked all trades in paper mode, slowing iteration.
- Kelly position sizing produced near-zero bet sizes when confidence/divergence was tiny.
- Config thresholds (min_confidence, min_divergence) were too high for focused single-event trading.

### Solution
- **CLI retry logic:** `_run_cli()` now retries up to 3 times with 1s sleep between attempts on non-zero exit codes. Timeouts still raise immediately.
- **Tick error resilience:** Wrapped `orch.tick()` in try/except in the `run` loop so transient failures don't kill the continuous loop.
- **Gamma API fallback:** `_apply_focus_filter()` falls back to `_fetch_focus_markets_from_api()` when CLI results have no matches. Converts search queries to slugs and queries `https://gamma-api.polymarket.com/events?slug=...` with suffix variants (`-by`, `-in`) for bracket events.
- **Max brackets limiting:** New `FocusConfig.max_brackets` field (default 5). Sorts filtered bracket markets by `end_date` and keeps only the nearest N.
- **Daily loss bypass in paper mode:** `_check_risk()` skips the daily loss gate when `config.mode == "paper"`.
- **Config tuning:** Switched to fixed position sizing ($5), lowered min_divergence to 0.01, min_confidence to 0.01, enabled Tavily news with 600s cache.
- **Tavily dependency:** Added `tavily-python>=0.7.22` to pyproject.toml.

### Edits
- `src/polymarket_agent/data/client.py` — added retry loop (3 attempts, 1s sleep) to `_run_cli()`
- `src/polymarket_agent/cli.py` — wrapped `orch.tick()` in try/except for loop resilience
- `src/polymarket_agent/orchestrator.py` — added `_fetch_focus_markets_from_api()` static method with Gamma API slug lookup; added max_brackets limiting in `_apply_focus_filter()`; bypassed daily loss check in paper mode
- `src/polymarket_agent/config.py` — added `max_brackets: int = 5` to `FocusConfig`
- `config.yaml` — Iran focus config, Tavily news, fixed sizing, lower thresholds, 10s polling
- `pyproject.toml` — added `tavily-python>=0.7.22` dependency
- `uv.lock` — updated lockfile
- `tests/test_risk_gate.py` — updated `test_risk_gate_blocks_when_daily_loss_exceeded` → `test_risk_gate_skips_daily_loss_in_paper_mode` to match new paper mode behavior

### NOT Changed
- No changes to strategy logic (AI Analyst, TechnicalAnalyst, SignalTrader, etc.)
- No changes to news provider implementations or indicator computations
- No DB schema changes
- No changes to MCP server or dashboard

### Verification
```bash
uv run pytest tests/ -v                     # 393 passed
uv run pytest tests/test_risk_gate.py -v    # 10 passed (updated daily loss test)
ruff check src/                              # All checks passed
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-28 (Review Follow-Up: Focus Filter + Research Simplification)

### Problem
- Review of the latest focus-trading/research code found two correctness and maintainability issues:
  - `focus.search_queries` values containing only whitespace could filter out all markets when focus was enabled.
  - Focus selectors (`search_queries`, `market_slugs`, `market_ids`) were not trimmed/normalized, so values with extra spaces failed to match.
- The new `research` CLI command had avoidable complexity (redundant grouping flow, repeated sorting/label logic, brittle reason-field parsing).

### Solution
- **Focus filter normalization:** Updated `_apply_focus_filter()` to trim selectors, ignore empty values, and use a single OR check per market with less repeated string work.
- **TDD regression coverage:** Added two orchestrator tests that failed first, then passed after the fix:
  - blank query handling
  - whitespace normalization for slug/query matching
- **Research command simplification:** Reduced grouping logic to a direct prefix bucket map, removed repeated sort calls, centralized label extraction, and replaced split-chain parsing with a small key/value extractor helper.

### Edits
- `src/polymarket_agent/orchestrator.py` — simplified and hardened `_apply_focus_filter()` (trim/normalize/ignore-empty selectors)
- `src/polymarket_agent/cli.py` — simplified `research` grouping/display flow and reason parsing helpers
- `tests/test_orchestrator.py` — added:
  - `test_focus_filter_ignores_blank_queries`
  - `test_focus_filter_normalizes_slug_and_query_whitespace`

### NOT Changed
- No changes to trading strategy math or aggregation thresholds.
- No changes to DB schema, execution layer, or market data client subprocess behavior.
- No config defaults changed in this follow-up.

### Verification
```bash
uv run python -m pytest tests/test_orchestrator.py::test_focus_filter_ignores_blank_queries tests/test_orchestrator.py::test_focus_filter_normalizes_slug_and_query_whitespace -q  # failed first, then passed after fix
uv run python -m pytest tests/test_orchestrator.py tests/test_cli.py -q  # 12 passed
ruff check src/polymarket_agent/orchestrator.py src/polymarket_agent/cli.py tests/test_orchestrator.py  # All checks passed
uv run mypy src/polymarket_agent/orchestrator.py src/polymarket_agent/cli.py  # Success: no issues found in 2 source files
```

### Branch
- Working branch: `main`

---

## Project Summary

**Polymarket Agent** is a Python auto-trading pipeline for Polymarket prediction markets. It wraps the official `polymarket` CLI (v0.1.4, installed via Homebrew) into a structured system with pluggable trading strategies, paper/live execution, and MCP server integration for AI agents.

## Current State: Phase 4 COMPLETE + Strategy Research Improvements

- 447 tests passing, ruff lint clean, mypy strict clean
- All 4 strategies implemented: SignalTrader, MarketMaker, Arbitrageur, AIAnalyst
- Signal aggregation integrated (groups by market+token+side, unique strategy consensus)
- MCP server with 14 tools: search_markets, get_market_detail, get_price_history, get_leaderboard, get_portfolio, get_signals, refresh_signals, place_trade, analyze_market, get_event, get_price, get_spread, get_volume, get_positions
- LiveTrader with py-clob-client for real order execution
- Risk management: max_position_size, max_daily_loss, max_open_orders enforced in Orchestrator
- CLI commands: `run` (with `--live` safety flag + config hot-reload), `status`, `tick`, `research`, `report`, `evaluate`, `backtest`, `dashboard`, `mcp`
- Focus trading: configurable market filtering by search queries, IDs, or slugs; bracket market context for AI analyst
- Auto-tune pipeline: `scripts/autotune.sh` + launchd plist for periodic Claude Code-driven config tuning
- Exit manager: automatic position exits via profit target, stop loss, signal reversal, and staleness rules

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

## Verification Commands

```bash
# Everything should pass
uv run pytest tests/ -v           # 447 tests passing
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 35 source files
uv run polymarket-agent tick      # Fetches live data, paper trades (focus-filtered)
uv run polymarket-agent research "Elon Musk"  # Deep bracket market analysis
uv run polymarket-agent mcp       # Starts MCP server (stdio transport)
uv run polymarket-agent run --live  # Live trading (requires POLYMARKET_PRIVATE_KEY)
```
