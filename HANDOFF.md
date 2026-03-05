# Polymarket Agent — Handoff Document

Last updated: 2026-03-05

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

## Session Entry — 2026-03-05 (Market Filtering Preferences + Configurable max_tokens)

### Problem
- Agent analyzes all 50 markets equally, including sports markets where the LLM has no edge, causing portfolio bleed.
- `max_tokens` was hardcoded at 1024 in AIAnalyst LLM calls, not configurable via YAML.

### Solution
- Added market categorization (`categorize_market()`) with keyword-based classification (politics, crypto, finance, tech, sports, entertainment, science).
- Extended `FocusConfig` with volume filtering (`min_volume_24h`), category filtering (`categories.preferred` / `categories.excluded`), trending prioritization (`prioritize_trending`), and configurable fetch limit (`fetch_limit`).
- Filters run unconditionally as Phases 1-3 in `_apply_focus_filter()`, before the existing focus logic (Phase 4-5).
- Made `max_tokens` configurable in AIAnalyst via `configure()`, replacing all hardcoded values.

### Edits
- `src/polymarket_agent/data/models.py` — Added `one_day_price_change`, `is_new` fields to `Market`; added `_CATEGORY_KEYWORDS` dict and `categorize_market()` function
- `src/polymarket_agent/config.py` — Added `CategoryConfig` model; extended `FocusConfig` with `categories`, `min_volume_24h`, `prioritize_trending`, `fetch_limit`
- `src/polymarket_agent/strategies/ai_analyst.py` — Added `self._max_tokens` field, `configure()` support, replaced hardcoded `max_tokens` in both Anthropic and OpenAI paths
- `src/polymarket_agent/orchestrator.py` — Imported `categorize_market`; passed `fetch_limit` to `get_active_markets()` in `tick()` and `generate_signals()`; extended `_apply_focus_filter()` with volume, category, and trending phases
- `config.yaml` — Added `max_tokens: 2048` to ai_analyst; added `fetch_limit`, `min_volume_24h`, `prioritize_trending`, `categories` to focus config
- `tests/test_models.py` — 8 new tests: `categorize_market` categories, case insensitivity, priority, new Market fields
- `tests/test_config.py` — 3 new tests: `CategoryConfig` defaults, `FocusConfig` new fields, YAML loading
- `tests/test_orchestrator.py` — 3 new tests: volume filter, category exclude, trending sort
- `tests/test_ai_analyst.py` — 3 new tests: `max_tokens` default, Anthropic flow-through, OpenAI flow-through

### NOT Changed
- No changes to strategy logic, signal aggregation, or execution layer.
- Existing focus filter logic (Phase 4-5) unchanged.
- No DB schema changes.

### Verification
```bash
uv run pytest tests/test_models.py tests/test_config.py tests/test_orchestrator.py tests/test_ai_analyst.py -v  # 63 passed
uv run ruff check src/  # All checks passed
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-03-04 (Review Pass 3: Simplification + Robustness Fixes)

### Problem
- The newly added slippage path executed at fill prices, but `trades.price` still logged target price, creating audit/analytics drift.
- `CalibrationTable._confidence_to_bin()` did not clamp negative confidences, which could produce invalid negative bin keys.
- `reload_config()` rebuilt strategies/sizer but did not rebuild reflection wiring, so stale `ReflectionEngine` state could survive strategy reloads.

### Solution
- Updated `PaperTrader` trade logging to persist executed fill prices in DB records.
- Hardened calibration binning to clamp confidence values to `[0, 9]` bins defensively.
- Rebuilt reflection engine wiring during hot reload and reset calibration refresh timestamp after reload refresh.
- Removed unused `kelly_size_calibrated()` helper from `PositionSizer` to reduce redundant API surface.

### Edits
- `src/polymarket_agent/execution/paper.py`
  - `_log_trade()` now accepts `executed_price` and logs actual fill price.
  - Buy/sell/writeoff paths pass the executed fill price into trade logging.
- `src/polymarket_agent/position_sizing.py`
  - `_confidence_to_bin()` now clamps lower bound (`max(..., 0)`).
  - Removed unused `kelly_size_calibrated()` method.
- `src/polymarket_agent/orchestrator.py`
  - `reload_config()` now rebuilds `_reflection_engine` and resets `_last_calibration_at`.
  - Simplified `_build_reflection_engine()` to wire only reflection-enabled AI analysts.
- `tests/test_paper_trader.py`
  - Added `test_paper_trader_logs_fill_price_with_slippage`.
- `tests/test_position_sizing.py`
  - Extended bin-boundary test with negative-confidence clamp assertion.
- `tests/test_config_reload.py`
  - Added `test_reload_config_rebuilds_reflection_engine`.

### NOT Changed
- No changes to strategy scoring, signal generation semantics, or execution risk rules.
- No schema changes and no new CLI commands.

### Verification
```bash
uv run pytest tests/test_paper_trader.py::test_paper_trader_logs_fill_price_with_slippage \
  tests/test_position_sizing.py::TestCalibrationTable::test_confidence_to_bin_boundaries \
  tests/test_config_reload.py::test_reload_config_rebuilds_reflection_engine -q
# 3 passed

uv run pytest tests/test_paper_trader.py tests/test_position_sizing.py tests/test_config_reload.py -q
# 51 passed

uv run ruff check src/polymarket_agent/execution/paper.py src/polymarket_agent/position_sizing.py src/polymarket_agent/orchestrator.py tests/test_paper_trader.py tests/test_position_sizing.py tests/test_config_reload.py
# All checks passed

uv run mypy src/polymarket_agent/execution/paper.py src/polymarket_agent/position_sizing.py src/polymarket_agent/orchestrator.py
# Success: no issues found in 3 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-03-04 (P&L Improvements: 7-Phase TradingAgents-Inspired Implementation)

### Problem
- No signal outcome tracking or P&L attribution — impossible to know which strategy makes or loses money.
- Paper trader overstated performance by 2-5% (no slippage modeling).
- `max_daily_loss` bypassed in paper mode; mark-to-market missing; Sharpe annualization used hardcoded `sqrt(252)`.
- AIAnalyst used single LLM call — no adversarial debate to reduce overconfidence.
- Kelly criterion used raw sigmoid confidence as P(win) instead of calibrated historical win rate.
- No post-trade reflection or institutional memory across resolved trades.
- Signal aggregation used all-or-nothing conflict resolution with equal strategy weights.

### Solution
Implemented 7-phase P&L improvement plan (`docs/plans/2026-03-04-pnl-improvements-tradingagents.md`) inspired by TauricResearch/TradingAgents:

**Phase A — Signal Outcome Tracking:** Added `signal_outcomes` table with Brier score computation. `strategy-stats` CLI command for per-strategy accuracy/P&L reporting. Outcome recording uses actual fill price/size from Orders (not target values).

**Phase B — Slippage Modeling:** PaperTrader applies configurable `slippage_bps` (default 50 = 0.5%). Buy fills higher, sell fills lower. `PaperTradingConfig` with Pydantic-bounded `slippage_bps` (0-10000).

**Phase C — Mark-to-Market & Risk:** Added `mark_to_market(current_prices)` to PaperTrader. Removed paper mode bypass on `max_daily_loss`. Fixed Sharpe annualization to use actual snapshot timestamps.

**Phase D — Adversarial Debate:** Bull/bear/judge LLM debate pattern with anti-hold bias prompt. Toggled via `debate_mode` config. 3x LLM cost but higher-quality signals.

**Phase E — Kelly Calibration:** `CalibrationTable` maps `(strategy, confidence_bin)` → historical win rate. Falls back to raw confidence with <20 samples. Refreshes hourly via public `db.get_resolved_outcomes()` method.

**Phase F — Reflection & Memory:** `ReflectionEngine` generates LLM-based post-trade reflections. SQLite FTS5 full-text search for retrieving relevant past lessons. Lessons injected into AIAnalyst prompts.

**Phase G — Weighted Aggregation:** Performance-weighted confidence blending and conflict resolution. Higher-weight side wins conflicts instead of suppressing both. Backward compatible when `strategy_weights=None`.

**Codex Review Fixes:** Addressed 4 of 12 findings:
- #1 (HIGH): Outcome recording uses actual fill price/size from Orders
- #3 (MEDIUM): Sell-side shares computed from fill_price (not target_price)
- #6 (MEDIUM): `slippage_bps` bounded with `Field(ge=0, le=10000)`
- #8 (MEDIUM): `CalibrationTable.refresh()` uses public `db.get_resolved_outcomes()` instead of `db._conn`

### Edits
- `src/polymarket_agent/db.py` — added `signal_outcomes` table, `trade_reflections` table with FTS5 index; methods: `record_signal_outcome()`, `resolve_signal_outcomes()`, `get_strategy_accuracy()`, `get_strategy_pnl()`, `get_pending_outcomes_by_market()`, `get_resolved_outcomes()`, `record_reflection()`, `search_reflections()`
- `src/polymarket_agent/orchestrator.py` — `_record_signal()` accepts `fill_price`/`fill_size`; `tick()` calls `_maybe_refresh_calibration()`, `_check_market_resolutions()`, `_mark_positions_to_market()`; added `_trigger_reflection()`, `_compute_strategy_weights()`, `_build_reflection_engine()`; removed paper mode bypass on `max_daily_loss`; passes `strategy_weights` to `aggregate_signals()`
- `src/polymarket_agent/config.py` — added `PaperTradingConfig(slippage_bps)`, `paper_trading` field on `AppConfig`, `performance_weighted` on `AggregationConfig`
- `src/polymarket_agent/execution/paper.py` — added `slippage_bps` param, `_apply_slippage()`, `mark_to_market()`; sell path uses `fill_price` for shares computation
- `src/polymarket_agent/position_sizing.py` — added `CalibrationTable` class with `refresh()`, `calibrated_confidence()`; `PositionSizer` accepts optional `calibration` param
- `src/polymarket_agent/strategies/aggregator.py` — added `strategy_weights` param for weighted confidence blending and conflict resolution
- `src/polymarket_agent/strategies/debate.py` — **NEW** `DebateResult`, `run_debate()` with bull/bear/judge LLM calls
- `src/polymarket_agent/strategies/reflection.py` — **NEW** `ReflectionEngine` with `reflect_on_outcome()`, `retrieve_relevant_lessons()`, FTS5 search
- `src/polymarket_agent/strategies/ai_analyst.py` — added `debate_mode`, `reflection_enabled`, `set_reflection_engine()`; debate branches to `_evaluate_with_debate()`
- `src/polymarket_agent/backtest/metrics.py` — fixed `_compute_sharpe_ratio()` to use `_estimate_periods_per_year()` from timestamps
- `src/polymarket_agent/data/provider.py` — added `get_market()` to DataProvider protocol
- `src/polymarket_agent/backtest/historical.py` — added `get_market()` implementation
- `src/polymarket_agent/cli.py` — added `strategy-stats` command with `--strategy`, `--json` options
- `tests/test_db.py` — 10 new tests for signal outcomes
- `tests/test_paper_trader.py` — 5 new tests (slippage + mark-to-market)
- `tests/test_debate.py` — **NEW** 8 tests
- `tests/test_reflection.py` — **NEW** 7 tests
- `tests/test_position_sizing.py` — 5 new tests for CalibrationTable
- `tests/test_aggregator.py` — 5 new tests for weighted aggregation
- `tests/test_risk_gate.py` — updated daily loss test to expect enforcement in paper mode

### NOT Changed
- No changes to existing strategy scoring logic (SignalTrader, MarketMaker, Arbitrageur, TechnicalAnalyst)
- No changes to MCP server or dashboard
- No changes to CLI commands other than adding `strategy-stats`
- No changes to WhaleFollower, CrossPlatformArb, or enrichment modules

### Verification
```bash
uv run pytest tests/ -v              # 489 passed in 12.30s
uv run ruff check src/               # All checks passed
```

### Branch
- Working branch: `main`

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

## Project Summary

**Polymarket Agent** is a Python auto-trading pipeline for Polymarket prediction markets. It wraps the official `polymarket` CLI (v0.1.4, installed via Homebrew) into a structured system with pluggable trading strategies, paper/live execution, and MCP server integration for AI agents.

## Current State: Phase 4 COMPLETE + Strategy Research + P&L Improvements

- 489 tests passing, ruff lint clean, mypy strict clean
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
