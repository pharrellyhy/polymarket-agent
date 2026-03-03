# Paper-Trading Test Plan — Phase 1–3 Validation

**Date:** 2026-03-03
**Status:** APPROVED

---

## Context

Phases 1–3 from the strategy research are implemented but need validation against real Polymarket data via paper trading. This plan creates config toggle flags for A/B comparison, four config profiles (baseline + one per phase), a test protocol, and a comparison script.

---

## 1. Config Toggle Flags

Features currently always-on need config flags to enable/disable for A/B testing:

| Feature | File | Flag | Default | Effect when `false` |
|---------|------|------|---------|---------------------|
| Platt scaling | `ai_analyst.py` | `platt_scaling` | `true` | Skip `_extremize()`, use raw LLM probability |
| Sigmoid confidence | `ai_analyst.py` | `sigmoid_confidence` | `true` | Revert to `min(abs(div) / 0.3, 1.0)` |
| MACD scoring | `technical_analyst.py` | `macd_enabled` | `true` | Zero out MACD weight, redistribute to EMA/RSI/squeeze |
| Regime-adaptive weights | `technical_analyst.py` | `regime_adaptive` | `true` | Use fixed weights (0.4/0.3/0.3/0.0) |
| Conflict resolution | `aggregator.py` | via aggregation config | `true` | Skip conflict suppression pass |
| Confidence blending | `aggregator.py` | via aggregation config | `true` | Revert to winner-takes-all (`max(confidence)`) |

Trailing stop and scratchpad prompt already have config flags.

---

## 2. Config Profiles

Four YAML files in `configs/test/`, each layering on the previous:

**`baseline.yaml`** — Pre-Phase-1 behavior:
- `position_sizing.method: fixed`
- `ai_analyst.platt_scaling: false`, `sigmoid_confidence: false`
- `technical_analyst.macd_enabled: false`, `regime_adaptive: false`
- `aggregation.conflict_resolution: false`, `blend_confidence: false`
- `trailing_stop_enabled: false`

**`phase1.yaml`** — Adds Platt scaling + sigmoid + fractional Kelly:
- `position_sizing.method: fractional_kelly`
- `ai_analyst.platt_scaling: true`, `sigmoid_confidence: true`

**`phase2.yaml`** — Adds MACD + regime-adaptive weights:
- Same as Phase 1 plus `technical_analyst.macd_enabled: true`, `regime_adaptive: true`

**`phase3.yaml`** — Adds aggregation + exit improvements:
- Same as Phase 2 plus `aggregation.conflict_resolution: true`, `blend_confidence: true`
- `conditional_orders.trailing_stop_enabled: true`, `exit_manager.trailing_stop_enabled: true`

Each profile uses a separate DB path so results don't mix.

---

## 3. Test Protocol

**Run command:**
```bash
uv run polymarket-agent run --config configs/test/<profile>.yaml --db data/<profile>.db
```

**Duration:** 30 ticks per profile (~5 minutes at `poll_interval: 10`).

**Observables per run:**

| Metric | Source | What to look for |
|--------|--------|-----------------|
| Signals generated | DB `signals` table | Count per strategy per tick |
| Confidence distribution | DB `signals` table | Baseline: linear. Phase 1+: sigmoid clustering |
| Trade count | Status output | Kelly sizing changes bet amounts |
| Position sizes | DB `trades` table | Baseline: fixed. Phase 1+: variable |
| Conflict suppression | Logs (`grep "conflicted"`) | Phase 3: AI/TA disagreements suppressed |
| Trailing stop triggers | Logs (`grep "trailing"`) | Phase 3: high-water mark exits |
| Regime labels | Logs (`grep "regime="`) | Phase 2+: trending/ranging/transitional |
| MACD crossovers | Logs (`grep "macd="`) | Phase 2+: bullish/bearish/neutral |

---

## 4. Comparison Script

`scripts/compare_test_runs.py` — queries each profile's SQLite DB and prints a side-by-side table:

- Total signals generated
- Mean confidence / std dev
- Trades executed
- Mean position size
- Unique markets traded
- Conflict suppressions (Phase 3)
- Trailing stop exits (Phase 3)
- Regime distribution (Phase 2+)
- MACD crossover count (Phase 2+)

---

## 5. Success Criteria

| Phase | Pass condition |
|-------|---------------|
| Phase 1 | Confidence std dev > baseline. Position sizes vary (not all identical). |
| Phase 2 | TA reason strings contain `regime=` and `macd=`. Signal count may differ from Phase 1. |
| Phase 3 | ≥1 conflict suppression. Confidence values are averages. Trailing stop fires on drawdown. |
| All | No crashes. No unhandled exceptions. Signal count > 0 per profile. |

**Not judged:** Profitability (requires longer runs and resolved markets).

---

## 6. Implementation Steps

| Step | What | Files |
|------|------|-------|
| 1 | Add config toggle flags | `ai_analyst.py`, `technical_analyst.py`, `aggregator.py`, `config.py` |
| 2 | Wire flags into logic | Same files — guard each feature behind its flag |
| 3 | Create config profiles | `configs/test/baseline.yaml`, `phase1.yaml`, `phase2.yaml`, `phase3.yaml` |
| 4 | Write comparison script | `scripts/compare_test_runs.py` |
| 5 | Add unit tests for flag behavior | `tests/test_ai_analyst.py`, `tests/test_technical_analyst.py`, `tests/test_aggregator.py` |
| 6 | Run all 4 profiles and validate | Manual execution of protocol from Section 3 |

**Estimated scope:** ~120 lines config flag wiring, ~60 lines config YAMLs, ~100 lines comparison script, ~50 lines new tests.
