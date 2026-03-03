# Strategy Research Improvements — Implementation Plan

**Date:** 2026-03-03
**Status:** PHASES 1–3 IMPLEMENTED · PHASES 4–8 PLANNED

---

## Context

Research identified concrete improvements to AIAnalyst, TechnicalAnalyst, aggregation, and position sizing. All changes are pure Python within the existing codebase — no new dependencies required. Phases 1–3 are implemented, ordered by ROI (impact / effort). Phases 4–8 cover remaining research items, also ordered by ROI.

---

## Phase 1 — High-ROI Quick Wins

### 1.1 Platt Scaling (α=√3) on AIAnalyst Output

**File:** `src/polymarket_agent/strategies/ai_analyst.py`

Added `_extremize()` static method that applies log-odds rescaling to correct LLM hedging bias. Applied immediately after parsing the probability from the LLM response and before divergence calculation.

```python
@staticmethod
def _extremize(p: float, alpha: float = math.sqrt(3)) -> float:
    if p <= 0.0 or p >= 1.0:
        return p
    log_odds = math.log(p / (1.0 - p))
    return 1.0 / (1.0 + math.exp(-alpha * log_odds))
```

**Effect:** 0.6→0.74, 0.8→0.89. Corrects the well-documented tendency of LLMs to hedge toward 0.5.

### 1.2 Sigmoid Confidence Mapping

**File:** `src/polymarket_agent/strategies/ai_analyst.py`

Replaced the linear confidence formula `min(abs(divergence) / 0.3, 1.0)` with a sigmoid:

```python
confidence = 1.0 / (1.0 + math.exp(-20.0 * (abs(divergence) - 0.15)))
```

**Effect:** Small divergences (<5%) → near-zero confidence; 15% → 0.5; 25%+ → saturates near 1.0.

### 1.3 Fractional Kelly Positioning

**File:** `config.yaml`

Changed `position_sizing.method` from `fixed` to `fractional_kelly`. Safe because Phase 1.1 + 1.2 produce calibrated confidence inputs. The `PositionSizer` already implemented fractional Kelly with `fraction=0.25` and `max_bet_pct=0.10`.

---

## Phase 2 — TechnicalAnalyst Indicators

### 2.1 MACD (6/13/5)

**File:** `src/polymarket_agent/strategies/indicators.py`

Added `MACDResult` model and `compute_macd()` with halved periods (6/13/5 instead of standard 12/26/9) to match prediction market timeframes. Includes crossover detection (bullish/bearish/neutral).

Helper `_compute_ema_series()` added for producing full EMA time series needed by MACD and divergence.

### 2.2 Divergence Detection

**File:** `src/polymarket_agent/strategies/indicators.py`

Added `DivergenceResult` model and `detect_divergence()` function. Uses swing-point detection over a configurable lookback window to identify when price and indicator directions disagree:
- Bullish divergence: price lower low + indicator higher low
- Bearish divergence: price higher high + indicator lower high

Applied to both RSI and MACD histogram series.

### 2.3 ATR-Based Adaptive RSI Thresholds

**File:** `src/polymarket_agent/strategies/indicators.py`

Added `compute_atr()` using close-only approximation (`|close[i] - close[i-1]|` with Wilder smoothing).

Added `adaptive_rsi_thresholds()` that classifies current ATR into percentile buckets:
- Low vol (ATR < 20th pctile): overbought=65, oversold=35
- Normal: overbought=70, oversold=30
- High vol (ATR > 80th pctile): overbought=75, oversold=25

Applied in `analyze_market_technicals()` via `model_copy()` to update RSI flags.

### 2.4 Market Regime Detection

**File:** `src/polymarket_agent/strategies/indicators.py`

Added `RegimeResult` model and `detect_regime()`:
- Trending: steep EMA slope (>1%) + expanding BB width
- Ranging: flat EMA slope + contracting BB width
- Transitional: mixed signals

### 2.5 Regime-Adaptive Confidence Weighting

**File:** `src/polymarket_agent/strategies/technical_analyst.py`

Replaced fixed weights (`ema=0.4, rsi=0.3, squeeze=0.3`) with regime-dependent weights:

| Regime | EMA | RSI | Squeeze | MACD |
|--------|-----|-----|---------|------|
| Trending | 0.45 | 0.10 | 0.15 | 0.30 |
| Ranging | 0.15 | 0.40 | 0.25 | 0.20 |
| Transitional | 0.30 | 0.25 | 0.20 | 0.25 |

Added MACD score component: `min(|histogram| / threshold, 1.0)`, boosted when crossover or divergence confirms.

### 2.6 StochRSI Activation

**File:** `src/polymarket_agent/strategies/technical_analyst.py`

Integrated the already-computed Stochastic RSI as a timing refinement:
- StochRSI < 0.2 on a buy signal → +0.2 boost to RSI score
- StochRSI > 0.8 on a sell signal → +0.2 boost to RSI score

---

## Phase 3 — Aggregation & Exit Improvements

### 3.1 Confidence Blending

**File:** `src/polymarket_agent/strategies/aggregator.py`

Replaced winner-takes-all (`max(group, key=confidence)`) with weighted average blending. The best signal's reason is preserved but confidence is the group average.

### 3.2 Conflict Resolution

**File:** `src/polymarket_agent/strategies/aggregator.py`

Added conflict detection pass: when strategies emit opposite sides for the same `(market_id, token_id)`, all signals for that pair are suppressed. Prevents contradictory signals from leaking through as separate groups.

### 3.3 Trailing Stop

**File:** `src/polymarket_agent/strategies/exit_manager.py` + `src/polymarket_agent/config.py`

Added `trailing_stop_enabled` and `trailing_stop_pct` to `ExitManagerConfig` (wiring the config keys that were defined in `config.yaml` but never loaded).

ExitManager now:
- Tracks high-water marks per position in `_high_water_marks` dict
- Updates on each evaluation: `hw = max(hw, current_price)`
- Triggers exit if `current_price < hw * (1 - trailing_stop_pct)` and `hw > avg_price`
- Only active when `trailing_stop_enabled: true`
- Cleans up marks for positions no longer held

Rule priority: profit target → trailing stop → stop loss → signal reversal → stale position.

### 3.4 Scratchpad Prompt

**File:** `src/polymarket_agent/strategies/ai_analyst.py`

Added structured CoT prompt template gated behind `structured_prompt: true/false` config flag:

```
Think step by step:
1. COMPREHENSION: Rephrase what "resolves Yes" means.
2. BASE RATE: Historical base rate for similar events.
3. ARGUMENTS FOR YES: 2-3 key reasons.
4. ARGUMENTS FOR NO: 2-3 key reasons.
5. WEIGHTING: Strongest arguments and why.
6. INITIAL ESTIMATE: First probability (0.0-1.0).
7. CALIBRATION CHECK: Over/under-confident?
8. FINAL PROBABILITY: [number]
```

Parser updated to first try `FINAL PROBABILITY:` extraction before falling back to last-float regex. OpenAI `max_tokens` increased to 1024 when structured prompt is active. System prompt adjusted for structured mode.

**Note:** Disabled by default (`structured_prompt: false`). Should be A/B tested with the current Qwen 35B model before enabling in production.

---

## Phase 4 — Temperature Sampling Ensemble (~40 lines)

**Status:** NOT YET IMPLEMENTED
**Research ref:** Sections 1.4, 1.7. Run the same model N times with temperature > 0, take median. Cheapest ensemble approach — 3× cost, zero infrastructure.

**File:** `src/polymarket_agent/strategies/ai_analyst.py`

**Changes:**

1. Add config fields to `__init__` and `configure()`:
   - `_ensemble_samples: int = 1` (1 = disabled, current behavior)
   - `_ensemble_temperature: float = 0.3`

2. Add `_call_llm_with_temperature(prompt, temperature) -> str` — mirrors `_call_llm()` but accepts temperature parameter. Both Anthropic and OpenAI paths override `temperature=0` with the provided value.

3. Add `_call_llm_ensemble(prompt) -> list[str]`:
   - When `ensemble_samples <= 1`: returns `[self._call_llm(prompt)]`
   - Otherwise: calls `_call_llm_with_temperature()` N times, returns all responses

4. Modify `_evaluate()`:
   - Call `_call_llm_ensemble(prompt)` instead of `_call_llm(prompt)`
   - Parse probability from each response (reuse existing regex logic)
   - Take median of valid parses
   - Apply `_extremize()` to the median
   - Append N timestamps to `_call_timestamps` in `finally` block

**Config:** `ai_analyst.ensemble_samples: 3`, `ai_analyst.ensemble_temperature: 0.3` (both default to disabled)

**Tests (in `tests/test_ai_analyst.py`):**
- `test_ensemble_disabled_by_default` — single call, backward compatible
- `test_ensemble_takes_median` — mock 3 responses (0.6, 0.7, 0.8) → median 0.7
- `test_ensemble_handles_partial_parse_failure` — 1 of 3 unparseable → median of 2
- `test_ensemble_rate_limit_counts_all_calls` — 3 samples consume 3 rate slots

---

## Phase 5 — Multi-Timeframe Analysis (~80 lines)

**Status:** NOT YET IMPLEMENTED
**Research ref:** Section 2.5. Fetch price data at short/medium/long intervals, require majority consensus across timeframes.

**Files:** `src/polymarket_agent/strategies/technical_analyst.py`, `src/polymarket_agent/strategies/indicators.py`

**Changes:**

1. Add to `indicators.py`:
   ```python
   class MultiTimeframeContext(BaseModel):
       short: TechnicalContext | None = None
       medium: TechnicalContext | None = None
       long_term: TechnicalContext | None = None
   ```

2. Add config fields to `TechnicalAnalyst.__init__` and `configure()`:
   - `_multi_timeframe: bool = False`
   - `_timeframes: list[dict]` defaulting to `[{interval: "4h", fidelity: 60, label: "short"}, {interval: "1w", fidelity: 60, label: "medium"}, {interval: "1m", fidelity: 240, label: "long"}]`

3. Branch `_evaluate()` on `self._multi_timeframe`:
   - When disabled: existing single-timeframe path (unchanged)
   - When enabled: call `_evaluate_multi_timeframe()`

4. New `_evaluate_multi_timeframe(market, token_id, data)`:
   - Fetch price history at each timeframe interval (reuse `analyze_market_technicals()`)
   - Compute side from `_determine_side()` per timeframe
   - Require majority agreement (>50% of valid timeframes agree on buy/sell)
   - Base confidence from medium timeframe + alignment bonus (+0.1 per agreeing extra timeframe)

5. New `_build_multi_tf_reason()` for signal reason string.

**Config:** `technical_analyst.multi_timeframe: false` (disabled by default)

**Cost:** Up to 3 API calls per market per tick (CLI cached at 30s TTL).

**Tests (in `tests/test_technical_analyst.py`):**
- `test_multi_timeframe_disabled_by_default` — existing behavior
- `test_multi_timeframe_all_agree` — 3 timeframes bullish → buy with bonus
- `test_multi_timeframe_no_consensus` — mixed → no signal
- `test_multi_timeframe_partial_data_failure` — 1 timeframe fails → uses remaining 2

---

## Phase 6 — Signal Accuracy Tracking (~100 lines)

**Status:** NOT YET IMPLEMENTED
**Research ref:** Prerequisite for performance-weighted aggregation (Section 3.4). Need to track which signals were correct after market resolution.

**Files:** `src/polymarket_agent/db.py`, `src/polymarket_agent/orchestrator.py`

**Changes:**

1. New DB table `signal_outcomes` in `_create_tables()`:
   - Columns: `id`, `signal_id`, `strategy`, `market_id`, `token_id`, `side`, `confidence`, `predicted_price`, `resolved_price`, `outcome` (pending/correct/incorrect), `brier_score`, `correct` (0/1), `resolved_at`, `created_at`

2. New DB methods:
   - `record_signal_outcome(signal_id, strategy, market_id, token_id, side, confidence, predicted_price) -> int`
   - `resolve_signal_outcomes(market_id, resolved_price) -> int` — resolves all pending outcomes for a market, computes Brier score and correctness
   - `get_strategy_accuracy(strategy, window_days=30) -> dict` — returns `{win_rate, avg_brier, sample_count}`

3. Change `record_signal()` to return `int` (the inserted row ID via `cursor.lastrowid`)

4. Orchestrator changes:
   - Update `_record_signal()` to also call `db.record_signal_outcome()` when status is `"generated"`
   - Add `_check_signal_resolutions(markets)` — iterates fetched markets, resolves outcomes for closed markets
   - Call `_check_signal_resolutions()` at start of `tick()`

**Tests (in `tests/test_db.py`):**
- `test_signal_outcomes_table_created`
- `test_record_and_resolve_signal_outcome` — full lifecycle
- `test_strategy_accuracy_empty_history` — returns sensible defaults (win_rate=0.5)
- `test_strategy_accuracy_with_data` — correct calculations

---

## Phase 7 — Performance-Weighted Aggregation (~40 lines)

**Status:** NOT YET IMPLEMENTED
**Research ref:** Section 3.4. Weight each strategy's confidence by its historical accuracy.

**Files:** `src/polymarket_agent/strategies/aggregator.py`, `src/polymarket_agent/orchestrator.py`, `src/polymarket_agent/config.py`

**Changes:**

1. Add `performance_weighted: bool = False` to `AggregationConfig` in `config.py`

2. Add optional `strategy_weights: dict[str, float] | None = None` parameter to `aggregate_signals()`:
   - When provided: weighted average `sum(confidence * weight) / sum(weights)` instead of simple average
   - When None: existing simple average (backward compatible)

3. Add `_compute_strategy_weights()` to orchestrator:
   - Query `db.get_strategy_accuracy(strategy.name)` for each active strategy
   - Require min 20 resolved samples per strategy; use equal weight (1.0) if insufficient
   - Weight = `max(0.3, win_rate)` (floor prevents zeroing out struggling strategies)
   - Normalize weights so they sum to N (preserves average-case behavior)
   - Return `None` if all weights are equal (no effect)

4. Pass weights to `aggregate_signals()` in `tick()` when `config.aggregation.performance_weighted` is enabled.

**Config:** `aggregation.performance_weighted: false` (disabled by default, needs Phase 6 data first)

**Tests (in `tests/test_aggregator.py`):**
- `test_weighted_aggregation_favors_accurate_strategy` — weight 2.0 vs 1.0
- `test_weighted_aggregation_none_uses_simple_average` — backward compatible
- `test_strategy_weights_insufficient_data` — returns None

---

## Phase 8 — Agentic Search / Iterative RAG (~120 lines)

**Status:** NOT YET IMPLEMENTED
**Research ref:** Section 1.2. AIA Forecaster achieves 7.3% Brier improvement with agentic search (0.1140 vs 0.1230).

**Files:** `src/polymarket_agent/news/agentic_search.py` (new), `src/polymarket_agent/strategies/ai_analyst.py`

**Changes:**

1. New file `src/polymarket_agent/news/agentic_search.py`:
   - `AgenticSearchResult` dataclass: `articles: list[NewsItem]`, `rounds_completed: int`, `search_log: list[str]`
   - `AgenticSearcher` class:
     - `__init__(news_provider, llm_call, max_rounds=3, max_queries_per_round=2, max_total_articles=15)`
     - `search(question, description) -> AgenticSearchResult`
     - Round 1: `_generate_queries()` — LLM generates 2 search queries from market question
     - Each round: fetch articles, then `_identify_gaps()` — LLM reviews evidence, generates follow-up queries or returns DONE
     - `_summarize_evidence()` — LLM summarizes collected articles for gap identification
     - Deduplicates articles by title

2. AIAnalyst integration:
   - Add config fields: `_agentic_search: bool = False`, `_agentic_max_rounds: int = 3`
   - In `_evaluate()`, when `agentic_search` enabled: use `AgenticSearcher` instead of `_fetch_news()`
   - Append `--- RESEARCH LOG ---` section to prompt with search log
   - Move rate-limit timestamp tracking from `_evaluate()` finally-block into `_call_llm()` itself, so agentic search LLM calls are also counted

**Cost:** 3–7 extra LLM calls per market. With ensemble (3×) + agentic (7×) both enabled = ~10 calls/market. At 30 markets = 300 calls/tick. With `max_calls_per_hour=5000` and 10s poll: exhausts in ~16 ticks. Recommendation: gate agentic search to focus-filtered markets only.

**Config:** `ai_analyst.agentic_search: false`, `ai_analyst.agentic_max_rounds: 3`

**Tests (new `tests/test_agentic_search.py`):**
- `test_generates_queries_from_question` — mock LLM returns parseable queries
- `test_stops_on_done` — gap identification returns DONE, stops early
- `test_respects_max_rounds`
- `test_deduplicates_articles`
- `test_handles_llm_failure` — graceful fallback
- `test_agentic_search_disabled_by_default` (in `tests/test_ai_analyst.py`)

---

## Dependency Graph

```
Phase 4 (Ensemble)      Phase 5 (Multi-TF)      Phase 8 (Agentic Search)
    [independent]          [independent]             [independent]

              Phase 6 (Signal Accuracy Tracking)
                         ↓
              Phase 7 (Performance-Weighted Aggregation)
```

Phases 4, 5, 8 are fully independent. Phase 7 requires Phase 6.

---

## File Change Summary (Phases 4–8)

| Phase | File | Change |
|-------|------|--------|
| 4 | `strategies/ai_analyst.py` | `_ensemble_samples`, `_ensemble_temperature`, `_call_llm_with_temperature()`, `_call_llm_ensemble()`, median in `_evaluate()` |
| 5 | `strategies/technical_analyst.py` | `_multi_timeframe` config, `_evaluate_multi_timeframe()`, `_build_multi_tf_reason()` |
| 5 | `strategies/indicators.py` | `MultiTimeframeContext` model |
| 6 | `db.py` | `signal_outcomes` table, `record_signal_outcome()`, `resolve_signal_outcomes()`, `get_strategy_accuracy()` |
| 6 | `orchestrator.py` | `_check_signal_resolutions()`, record outcomes in `_record_signal()` |
| 7 | `strategies/aggregator.py` | `strategy_weights` param, weighted average blending |
| 7 | `config.py` | `performance_weighted` in `AggregationConfig` |
| 7 | `orchestrator.py` | `_compute_strategy_weights()`, pass to `aggregate_signals()` |
| 8 | `news/agentic_search.py` | **NEW** `AgenticSearcher`, `AgenticSearchResult` |
| 8 | `strategies/ai_analyst.py` | `_agentic_search` config, integrate searcher, move rate-limit tracking into `_call_llm()` |

## File Change Summary (Phases 1–3, implemented)

| Phase | File | Change Type |
|-------|------|-------------|
| 1 | `strategies/ai_analyst.py` | Add `_extremize()`, sigmoid confidence, structured prompt |
| 1 | `config.yaml` | `position_sizing.method: fractional_kelly` |
| 2 | `strategies/indicators.py` | Add MACD, divergence, ATR, regime detection, adaptive RSI |
| 2 | `strategies/technical_analyst.py` | Regime-adaptive weights, StochRSI boost, MACD score |
| 3 | `strategies/aggregator.py` | Confidence blending, conflict resolution |
| 3 | `strategies/exit_manager.py` | Trailing stop, high-water mark tracking |
| 3 | `config.py` | Add trailing stop fields to `ExitManagerConfig` |

## Tests Updated (Phases 1–3)

| File | Changes |
|------|---------|
| `tests/test_aggregator.py` | Updated for blended confidence; added conflict resolution test |
| `tests/test_integration.py` | Relaxed signal count assertions for conflict suppression |
| `tests/test_technical_analyst.py` | Updated bearish test data for adaptive RSI thresholds |

## Verification

After each phase:
```bash
.venv/bin/python -m pytest tests/ -q  # Phases 1–3: 440 passed
```

**Phase 4:** Mock 3 LLM responses with different probabilities → verify median is selected. Verify rate limit consumed 3× per evaluation.

**Phase 5:** Mock `get_price_history` for 3 intervals → verify majority consensus logic. Verify single-timeframe behavior unchanged when disabled.

**Phase 6:** Create signal outcome, resolve it, query accuracy → verify Brier score and win_rate calculations.

**Phase 7:** Provide strategy_weights to aggregator → verify weighted blending. Verify None weights produce identical results to current behavior.

**Phase 8:** Mock news provider + LLM → verify iterative search stops on DONE, respects max_rounds, deduplicates. Verify disabled by default.
