# P&L Improvement Plan — Insights from TradingAgents

**Save to:** `docs/plans/2026-03-04-pnl-improvements-tradingagents.md` (no implementation yet, plan only)

## Context

Research into [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) — a LangGraph multi-agent trading system — revealed several patterns that directly improve prediction market P&L. TradingAgents uses adversarial bull/bear debate, reflection-based memory, and multi-tier LLM architecture to generate trading decisions. Combined with analysis of our own gaps (no P&L attribution, miscalibrated Kelly, no slippage modeling), this plan targets 7 improvements ordered by expected P&L impact.

---

## Phase A: Signal Outcome Tracking & P&L Attribution (FOUNDATIONAL)

**Why:** Every other optimization is blind without knowing which strategy makes or loses money. This is the #1 prerequisite.

### Changes
- **`src/polymarket_agent/db.py`** — Add `signal_outcomes` table (`strategy, market_id, token_id, confidence, predicted_price, entry_price, resolved_price, pnl, brier_score, outcome`). Add methods: `record_signal_outcome()`, `resolve_signal_outcomes()`, `get_strategy_accuracy()`, `get_strategy_pnl()`
- **`src/polymarket_agent/orchestrator.py`** — In `_record_signal()`, also record a signal outcome when status="executed". Add `_check_market_resolutions()` to detect closed markets and compute Brier scores + P&L. Call it at the start of `tick()`
- **`src/polymarket_agent/cli.py`** — Add `strategy-stats` command to print per-strategy accuracy table

### Verification
- `uv run pytest tests/test_db.py -v` — unit tests for new DB methods
- Run paper trading, verify `signal_outcomes` table populates
- After market resolution, verify Brier scores computed correctly

---

## Phase B: Paper Trader Slippage & Spread Modeling

**Why:** Paper P&L currently overstates live performance by 2-5%. Slippage modeling prevents false-positive optimizations.

### Changes
- **`src/polymarket_agent/execution/paper.py`** — Add `_apply_slippage(price, side) -> float` using configurable `slippage_bps` (default 50 = 0.5%). Apply in `_execute_buy()` and `_execute_sell()`
- **`src/polymarket_agent/config.py`** — Add `PaperTradingConfig(slippage_bps=50)`
- **`src/polymarket_agent/orchestrator.py`** — Pass slippage config to PaperTrader

### Verification
- Unit test: buy at 0.50 with 50bps → fill at 0.5025, higher cost
- Existing paper trader tests pass with `slippage_bps=0`

---

## Phase C: Mark-to-Market & Risk Enforcement Fixes

**Why:** Hidden unrealized losses + `max_daily_loss` bypassed in paper mode = unrealistic test environment.

### Changes
- **`src/polymarket_agent/execution/paper.py`** — Add `mark_to_market(current_prices)` to update all position values with live prices
- **`src/polymarket_agent/orchestrator.py`** — Call `mark_to_market()` each tick after fetching prices. Remove the `mode != "paper"` bypass on `max_daily_loss` check (~line 600)
- **`src/polymarket_agent/backtest/metrics.py`** — Fix Sharpe annualization: compute actual periods/year from snapshot timestamps instead of hardcoded `sqrt(252)`

### Verification
- Portfolio `total_value` changes between ticks even without trades
- Paper mode rejects trades when `max_daily_loss` exceeded

---

## Phase D: Adversarial Debate for AIAnalyst

**Why:** TradingAgents' highest-impact pattern. Bull/bear debate forces steelmanning both sides, reducing overconfidence. Research shows debate-style prompting improves Brier score 5-10% on forecasting tasks.

### Changes
- **NEW `src/polymarket_agent/strategies/debate.py`** — `DebateAnalyst` class with `debate(market_context, current_price) -> DebateResult`. Three LLM calls:
  1. **Bull**: must argue FOR Yes, provide probability above market price
  2. **Bear**: must argue AGAINST Yes, provide probability below market price
  3. **Judge**: synthesizes debate, explicitly told "do NOT default to the midpoint" (anti-hold bias from TradingAgents)
- **`src/polymarket_agent/strategies/ai_analyst.py`** — Add `debate_mode: bool` config toggle. When enabled, replace single LLM call with debate. Apply Platt scaling to judge's output. Costs 3x LLM calls but produces higher-quality signals

### Design notes
- Reuse existing `_call_llm()` from AIAnalyst — no new LLM client needed
- Anti-hold bias prompt: "You MUST NOT default to the midpoint between bull and bear — that is intellectually lazy. Commit to a stance."
- Rate limit: evaluates ~1/3 as many markets but with better calibration per signal

### Verification
- Unit test with mocked LLM calls verifying DebateResult parsing
- `debate_mode=false` preserves existing single-call behavior
- A/B test: debate vs. no-debate on same markets

---

## Phase E: Kelly Criterion Calibration Fix

**Why:** Current Kelly uses sigmoid confidence as P(win), but it's a signal-strength measure, not a calibrated probability. This causes overbetting on noisy signals.

**Depends on:** Phase A (needs outcome data for calibration table)

### Changes
- **`src/polymarket_agent/position_sizing.py`** — Add `CalibrationTable` class. Maps `(strategy, confidence_bin)` → historical win rate. Bins confidence into 0.1-width buckets, computes `win_rate = correct / total` per bin. Falls back to raw confidence when <20 samples per bin
- **`src/polymarket_agent/orchestrator.py`** — Pass DB to CalibrationTable, refresh hourly. Use calibrated P(win) instead of raw confidence in Kelly formula

### Verification
- Unit test: CalibrationTable with known data returns correct bin lookup
- PositionSizer with calibration produces smaller sizes when `raw_confidence=0.8` but `historical_win_rate=0.55`

---

## Phase F: Reflection & Memory Loop

**Why:** TradingAgents' `reflect_and_remember` pattern creates institutional memory. After each resolved trade, LLM reflects on what went right/wrong. Lessons are retrieved for similar future markets via keyword search.

**Depends on:** Phase A (resolution triggers)

### Changes
- **`src/polymarket_agent/db.py`** — Add `trade_reflections` table with FTS5 index for keyword search. Methods: `record_reflection()`, `search_reflections(keywords, limit)`
- **NEW `src/polymarket_agent/strategies/reflection.py`** — `ReflectionEngine`:
  - `reflect_on_outcome(market_question, strategy, side, confidence, predicted_price, actual_result, entry_reason)` → generates lesson via LLM
  - `retrieve_relevant_lessons(market_question, limit=3)` → keyword search over past reflections
- **`src/polymarket_agent/strategies/ai_analyst.py`** — When `reflection_enabled: true`, inject `--- PAST LESSONS ---` section into prompt with retrieved reflections
- **`src/polymarket_agent/orchestrator.py`** — In `_check_market_resolutions()`, trigger reflection generation for each resolved outcome

### Design notes
- Uses SQLite FTS5 for retrieval (no extra dependencies, no vector DB needed)
- Reflection prompt asks for: key factor, what to do differently, applicable market types, keywords
- Lightweight: one short LLM call per resolved trade

### Verification
- Insert reflections, search by keywords, verify ranking
- After market resolution, verify reflection is generated and stored
- Verify lessons appear in AIAnalyst prompts for similar markets

---

## Phase G: Performance-Weighted Signal Aggregation

**Why:** Current conflict resolution is all-or-nothing — any disagreement kills all signals. Better: weight by historical accuracy so the proven strategy wins conflicts.

**Depends on:** Phase A (strategy accuracy data)

### Changes
- **`src/polymarket_agent/strategies/aggregator.py`** — Add `strategy_weights: dict[str, float] | None` parameter. When provided: confidence blending uses weighted average; conflict resolution uses weighted vote (higher-weight side wins, only losing side suppressed)
- **`src/polymarket_agent/orchestrator.py`** — Add `_compute_strategy_weights()`: queries `get_strategy_accuracy()` per strategy, uses win_rate with 0.3 floor, requires 20+ samples. Returns `None` when insufficient data (equal weight fallback)
- **`src/polymarket_agent/config.py`** — Add `performance_weighted: bool` to `AggregationConfig`

### Verification
- Unit test: weighted blending favors accurate strategy
- Unit test: weighted conflict resolution lets higher-weight side through
- Backward compatible when `strategy_weights=None`

---

## Implementation Order & Dependencies

```
Phase A ──────────────────────────────►  (start here, foundational)
Phase B ──► (parallel with A, no deps)
Phase C ──► (parallel with A, no deps)
Phase D ──► (after A for measurement, no hard dep)
Phase E ──► (requires A for resolution triggers)
Phase F ──► (requires A for outcome data)
Phase G ──► (requires A for accuracy data)
```

**Recommended sequence:** A → B+C (parallel) → D → E → F → G

## New Files (2)
- `src/polymarket_agent/strategies/debate.py`
- `src/polymarket_agent/strategies/reflection.py`

## File Change Summary

| File | Phases |
|------|--------|
| `src/polymarket_agent/db.py` | A, F |
| `src/polymarket_agent/orchestrator.py` | A, B, C, E, F, G |
| `src/polymarket_agent/execution/paper.py` | B, C |
| `src/polymarket_agent/config.py` | B, G |
| `src/polymarket_agent/strategies/ai_analyst.py` | D, F |
| `src/polymarket_agent/position_sizing.py` | E |
| `src/polymarket_agent/strategies/aggregator.py` | G |
| `src/polymarket_agent/backtest/metrics.py` | C |
| `src/polymarket_agent/cli.py` | A |
