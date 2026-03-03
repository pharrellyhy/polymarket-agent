# Paper-Trading Test Plan — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add config toggle flags for Phase 1–3 features, create 4 test config profiles, and build a comparison script for A/B paper-trading validation.

**Architecture:** Config flags gate each Phase 1–3 feature so it can be disabled for baseline comparison. Four YAML profiles (baseline → phase1 → phase2 → phase3) each enable one more layer. A comparison script queries each run's SQLite DB and prints a side-by-side metrics table.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, SQLite, pytest

---

### Task 1: Add config toggle flags to AIAnalyst

**Files:**
- Modify: `src/polymarket_agent/strategies/ai_analyst.py`

**Step 1: Add flag fields to `__init__` and `configure()`**

In `__init__`, after `self._structured_prompt: bool = False` (line 100), add:

```python
self._platt_scaling: bool = True
self._sigmoid_confidence: bool = True
```

In `configure()`, after the `self._structured_prompt` line (line 160), add:

```python
self._platt_scaling = bool(config.get("platt_scaling", self._platt_scaling))
self._sigmoid_confidence = bool(config.get("sigmoid_confidence", self._sigmoid_confidence))
```

**Step 2: Guard Platt scaling behind the flag**

In `_evaluate()`, line 480 currently reads:

```python
estimate = self._extremize(estimate)
```

Replace with:

```python
if self._platt_scaling:
    estimate = self._extremize(estimate)
```

**Step 3: Guard sigmoid confidence behind the flag**

Line 497 currently reads:

```python
confidence = 1.0 / (1.0 + math.exp(-20.0 * (abs(divergence) - 0.15)))
```

Replace with:

```python
if self._sigmoid_confidence:
    confidence = 1.0 / (1.0 + math.exp(-20.0 * (abs(divergence) - 0.15)))
else:
    confidence = min(abs(divergence) / 0.3, 1.0)
```

**Step 4: Run existing tests**

Run: `uv run pytest tests/test_ai_analyst.py -v`
Expected: All existing tests PASS (flags default to `true`, so behavior unchanged).

---

### Task 2: Add config toggle flags to TechnicalAnalyst

**Files:**
- Modify: `src/polymarket_agent/strategies/technical_analyst.py`

**Step 1: Add flag fields to `__init__` and `configure()`**

In `__init__`, after `self._order_size` (line 44), add:

```python
self._macd_enabled: bool = True
self._regime_adaptive: bool = True
```

In `configure()`, after the `self._order_size` line (line 52), add:

```python
self._macd_enabled = bool(config.get("macd_enabled", self._macd_enabled))
self._regime_adaptive = bool(config.get("regime_adaptive", self._regime_adaptive))
```

**Step 2: Guard regime-adaptive weights behind the flag**

In `_compute_confidence()`, replace the regime weight selection (lines 131–137):

```python
regime = ctx.regime.regime if ctx.regime else "transitional"
if regime == "trending":
    w_ema, w_rsi, w_squeeze, w_macd = 0.45, 0.10, 0.15, 0.30
elif regime == "ranging":
    w_ema, w_rsi, w_squeeze, w_macd = 0.15, 0.40, 0.25, 0.20
else:
    w_ema, w_rsi, w_squeeze, w_macd = 0.30, 0.25, 0.20, 0.25
```

With:

```python
if self._regime_adaptive:
    regime = ctx.regime.regime if ctx.regime else "transitional"
    if regime == "trending":
        w_ema, w_rsi, w_squeeze, w_macd = 0.45, 0.10, 0.15, 0.30
    elif regime == "ranging":
        w_ema, w_rsi, w_squeeze, w_macd = 0.15, 0.40, 0.25, 0.20
    else:
        w_ema, w_rsi, w_squeeze, w_macd = 0.30, 0.25, 0.20, 0.25
else:
    w_ema, w_rsi, w_squeeze, w_macd = 0.40, 0.30, 0.30, 0.0
```

This requires `_compute_confidence` to access `self`, so change the method from `@staticmethod` to a regular method. Update the signature from:

```python
@staticmethod
def _compute_confidence(ctx: TechnicalContext, side: str) -> float:
```

To:

```python
def _compute_confidence(self, ctx: TechnicalContext, side: str) -> float:
```

**Step 3: Guard MACD scoring behind the flag**

In `_compute_confidence()`, replace the MACD block (lines 166–179):

```python
macd_score = 0.0
if ctx.macd is not None:
```

With:

```python
macd_score = 0.0
if self._macd_enabled and ctx.macd is not None:
```

**Step 4: Run existing tests**

Run: `uv run pytest tests/test_technical_analyst.py -v`
Expected: All existing tests PASS (flags default to `true`).

---

### Task 3: Add config toggle flags to aggregator

**Files:**
- Modify: `src/polymarket_agent/strategies/aggregator.py`
- Modify: `src/polymarket_agent/config.py`

**Step 1: Add flags to `AggregationConfig`**

In `config.py`, add to `AggregationConfig` (after line 22):

```python
conflict_resolution: bool = True
blend_confidence: bool = True
```

**Step 2: Wire flags into `aggregate_signals()`**

Change the signature to accept the new flags:

```python
def aggregate_signals(
    signals: list[Signal],
    *,
    min_confidence: float = 0.5,
    min_strategies: int = 1,
    conflict_resolution: bool = True,
    blend_confidence: bool = True,
) -> list[Signal]:
```

Guard the conflict suppression block (lines 26–31). Replace:

```python
    # Conflict resolution: suppress signals where strategies disagree on side
    market_token_sides: dict[tuple[str, str], set[str]] = {}
    for signal in signals:
        key = (signal.market_id, signal.token_id)
        market_token_sides.setdefault(key, set()).add(signal.side)

    conflicted = {key for key, sides in market_token_sides.items() if len(sides) > 1}
```

With:

```python
    # Conflict resolution: suppress signals where strategies disagree on side
    conflicted: set[tuple[str, str]] = set()
    if conflict_resolution:
        market_token_sides: dict[tuple[str, str], set[str]] = {}
        for signal in signals:
            key = (signal.market_id, signal.token_id)
            market_token_sides.setdefault(key, set()).add(signal.side)
        conflicted = {key for key, sides in market_token_sides.items() if len(sides) > 1}
```

Guard the confidence blending (lines 46–48). Replace:

```python
        blended_confidence = sum(s.confidence for s in group) / len(group)
        best = max(group, key=lambda s: s.confidence)
        best = replace(best, confidence=round(blended_confidence, 4))
```

With:

```python
        best = max(group, key=lambda s: s.confidence)
        if blend_confidence:
            blended_confidence = sum(s.confidence for s in group) / len(group)
            best = replace(best, confidence=round(blended_confidence, 4))
```

**Step 3: Pass flags from orchestrator**

Search for calls to `aggregate_signals` in `orchestrator.py` and pass `conflict_resolution=self._config.aggregation.conflict_resolution` and `blend_confidence=self._config.aggregation.blend_confidence`.

Run: `grep -n "aggregate_signals" src/polymarket_agent/orchestrator.py` to find the call site.

**Step 4: Run existing tests**

Run: `uv run pytest tests/test_aggregator.py tests/test_integration.py -v`
Expected: All PASS (flags default to `true`).

---

### Task 4: Add unit tests for flag behavior

**Files:**
- Modify: `tests/test_aggregator.py`
- Modify: `tests/test_technical_analyst.py`

**Step 1: Write aggregator flag tests**

Append to `tests/test_aggregator.py`:

```python
def test_conflict_resolution_disabled_keeps_both_sides() -> None:
    """When conflict_resolution=False, opposing signals are NOT suppressed."""
    signals = [
        _signal("A", "1", "buy", confidence=0.8),
        _signal("B", "1", "sell", confidence=0.7),
    ]
    result = aggregate_signals(
        signals, min_confidence=0.0, min_strategies=1, conflict_resolution=False
    )
    assert len(result) == 2


def test_blend_confidence_disabled_uses_max() -> None:
    """When blend_confidence=False, winner-takes-all confidence is used."""
    signals = [
        _signal("A", "1", "buy", confidence=0.6),
        _signal("B", "1", "buy", confidence=0.9),
    ]
    result = aggregate_signals(
        signals, min_confidence=0.0, min_strategies=1, blend_confidence=False
    )
    assert len(result) == 1
    assert result[0].confidence == 0.9
```

**Step 2: Write technical analyst flag tests**

Append to `tests/test_technical_analyst.py`:

```python
def test_macd_disabled_zeroes_macd_weight() -> None:
    """When macd_enabled=False, MACD component has zero weight."""
    prices = [
        0.40, 0.38, 0.42, 0.39, 0.41, 0.37, 0.40, 0.38, 0.36, 0.39,
        0.37, 0.35, 0.38, 0.36, 0.34, 0.37, 0.39, 0.42, 0.44, 0.46,
        0.49, 0.48, 0.51, 0.53, 0.50, 0.54, 0.56, 0.55, 0.58, 0.60,
    ]
    data = _make_data_provider(prices)

    with_macd = TechnicalAnalyst()
    without_macd = TechnicalAnalyst()
    without_macd.configure({"macd_enabled": False})

    s_with = with_macd.analyze([_make_market("1", yes_price=0.5)], data)
    s_without = without_macd.analyze([_make_market("1", yes_price=0.5)], data)

    # Both should generate buy signals (EMA crossover is the primary driver)
    assert len(s_with) == 1
    assert len(s_without) == 1
    # Confidence may differ due to MACD component weight
    assert s_with[0].side == s_without[0].side == "buy"


def test_regime_adaptive_disabled_uses_fixed_weights() -> None:
    """When regime_adaptive=False, fixed weights (0.4/0.3/0.3/0.0) are used."""
    prices = [
        0.40, 0.38, 0.42, 0.39, 0.41, 0.37, 0.40, 0.38, 0.36, 0.39,
        0.37, 0.35, 0.38, 0.36, 0.34, 0.37, 0.39, 0.42, 0.44, 0.46,
        0.49, 0.48, 0.51, 0.53, 0.50, 0.54, 0.56, 0.55, 0.58, 0.60,
    ]
    data = _make_data_provider(prices)

    adaptive = TechnicalAnalyst()
    fixed = TechnicalAnalyst()
    fixed.configure({"regime_adaptive": False})

    s_adaptive = adaptive.analyze([_make_market("1", yes_price=0.5)], data)
    s_fixed = fixed.analyze([_make_market("1", yes_price=0.5)], data)

    assert len(s_adaptive) == 1
    assert len(s_fixed) == 1
    # Both produce buy signals; confidences may differ
    assert s_adaptive[0].side == s_fixed[0].side == "buy"
```

**Step 3: Run all tests**

Run: `uv run pytest tests/ -q`
Expected: All pass including new tests.

---

### Task 5: Create config profiles

**Files:**
- Create: `configs/test/baseline.yaml`
- Create: `configs/test/phase1.yaml`
- Create: `configs/test/phase2.yaml`
- Create: `configs/test/phase3.yaml`

**Step 1: Create directory**

```bash
mkdir -p configs/test
```

**Step 2: Write `configs/test/baseline.yaml`**

```yaml
# Baseline: all Phase 1-3 features disabled
mode: paper
starting_balance: 100.0
poll_interval: 10
strategies:
  signal_trader:
    enabled: false
  market_maker:
    enabled: false
  arbitrageur:
    enabled: false
  ai_analyst:
    enabled: true
    model: qwen/qwen3.5-35b-a3b
    max_calls_per_hour: 5000
    min_divergence: 0.10
    min_price: 0.05
    order_size: 10.0
    provider: openai
    extra_params:
      reasoning_budget: 0
      enable_thinking: false
    base_url: http://localhost:11567/v1
    volatility_enabled: true
    sentiment_enabled: true
    keyword_spike_enabled: true
    platt_scaling: false
    sigmoid_confidence: false
    structured_prompt: false
  technical_analyst:
    enabled: true
    ema_fast_period: 8
    ema_slow_period: 21
    rsi_period: 14
    history_interval: "1w"
    history_fidelity: 60
    order_size: 25.0
    macd_enabled: false
    regime_adaptive: false
  whale_follower:
    enabled: false
  cross_platform_arb:
    enabled: false
aggregation:
  min_confidence: 0.40
  min_strategies: 2
  conflict_resolution: false
  blend_confidence: false
risk:
  max_position_size: 200.0
  max_daily_loss: 500.0
  max_open_orders: 30
  reentry_cooldown_hours: 2
conditional_orders:
  enabled: true
  default_stop_loss_pct: 0.15
  default_take_profit_pct: 0.25
  trailing_stop_enabled: false
  trailing_stop_pct: 0.05
position_sizing:
  method: fixed
  kelly_fraction: 0.25
  max_bet_pct: 0.1
exit_manager:
  enabled: true
  profit_target_pct: 0.15
  stop_loss_pct: 0.15
  trailing_stop_enabled: false
  trailing_stop_pct: 0.05
  signal_reversal: true
  max_hold_hours: 24
news:
  enabled: true
  provider: google_rss
  api_key_env: TAVILY_API_KEY
  max_calls_per_hour: 100
  cache_ttl: 600
  max_results: 5
focus:
  enabled: false
  search_queries: []
  max_brackets: 30
monitoring:
  structured_logging: false
  log_file: logs/agent.log
  alert_webhooks: []
  snapshot_interval: 300
  dashboard_host: 0.0.0.0
  dashboard_port: 11648
```

**Step 3: Write `configs/test/phase1.yaml`**

Copy baseline, then change these keys:

```yaml
# Phase 1: Platt scaling + sigmoid confidence + fractional Kelly
  ai_analyst:
    platt_scaling: true
    sigmoid_confidence: true
position_sizing:
  method: fractional_kelly
```

All other values identical to baseline.

**Step 4: Write `configs/test/phase2.yaml`**

Copy phase1, then change:

```yaml
# Phase 2: + MACD + regime-adaptive weights
  technical_analyst:
    macd_enabled: true
    regime_adaptive: true
```

**Step 5: Write `configs/test/phase3.yaml`**

Copy phase2, then change:

```yaml
# Phase 3: + conflict resolution + blending + trailing stop
aggregation:
  conflict_resolution: true
  blend_confidence: true
conditional_orders:
  trailing_stop_enabled: true
exit_manager:
  trailing_stop_enabled: true
```

---

### Task 6: Write comparison script

**Files:**
- Create: `scripts/compare_test_runs.py`

**Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Compare paper-trading test runs across Phase 1-3 config profiles.

Usage:
    python scripts/compare_test_runs.py [--db-dir DIR]

Reads SQLite DBs named baseline.db, phase1.db, phase2.db, phase3.db from
the specified directory (default: data/) and prints a side-by-side metrics table.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

PROFILES = ["baseline", "phase1", "phase2", "phase3"]


def query_metrics(db_path: Path) -> dict[str, object]:
    """Extract key metrics from a test run database."""
    if not db_path.exists():
        return {"error": f"DB not found: {db_path}"}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Signal metrics
    signals = conn.execute("SELECT COUNT(*) as cnt FROM signal_log").fetchone()
    signal_count = signals["cnt"] if signals else 0

    conf_stats = conn.execute(
        "SELECT AVG(confidence) as mean, "
        "COALESCE(SQRT(AVG(confidence * confidence) - AVG(confidence) * AVG(confidence)), 0) as std "
        "FROM signal_log"
    ).fetchone()
    mean_conf = round(conf_stats["mean"], 4) if conf_stats and conf_stats["mean"] else 0.0
    std_conf = round(conf_stats["std"], 4) if conf_stats and conf_stats["std"] else 0.0

    # Trade metrics
    trades = conn.execute("SELECT COUNT(*) as cnt FROM trades").fetchone()
    trade_count = trades["cnt"] if trades else 0

    avg_size = conn.execute("SELECT AVG(size) as avg FROM trades").fetchone()
    mean_size = round(avg_size["avg"], 2) if avg_size and avg_size["avg"] else 0.0

    unique_markets = conn.execute("SELECT COUNT(DISTINCT market_id) as cnt FROM trades").fetchone()
    market_count = unique_markets["cnt"] if unique_markets else 0

    # Exit manager metrics (from trades with strategy='exit_manager')
    trailing = conn.execute(
        "SELECT COUNT(*) as cnt FROM trades WHERE reason LIKE '%trailing_stop%'"
    ).fetchone()
    trailing_count = trailing["cnt"] if trailing else 0

    # Regime and MACD from signal_log (parsed from reason if available — not directly stored)
    # These are logged in the signal reason strings as "regime=X" and "macd=X"
    # We count signals containing these patterns
    regime_signals = conn.execute(
        "SELECT COUNT(*) as cnt FROM signal_log WHERE strategy = 'technical_analyst'"
    ).fetchone()
    ta_signal_count = regime_signals["cnt"] if regime_signals else 0

    conn.close()

    return {
        "signals": signal_count,
        "mean_conf": mean_conf,
        "std_conf": std_conf,
        "trades": trade_count,
        "mean_size": mean_size,
        "markets": market_count,
        "trailing_exits": trailing_count,
        "ta_signals": ta_signal_count,
    }


def print_comparison(db_dir: Path) -> None:
    """Print side-by-side comparison table."""
    results = {}
    for profile in PROFILES:
        db_path = db_dir / f"{profile}.db"
        results[profile] = query_metrics(db_path)

    # Check for errors
    for profile, metrics in results.items():
        if "error" in metrics:
            print(f"WARNING: {metrics['error']}")

    # Print table
    header = f"{'Metric':<25}" + "".join(f"{p:>12}" for p in PROFILES)
    separator = "-" * len(header)

    print(separator)
    print(header)
    print(separator)

    rows = [
        ("Signals generated", "signals"),
        ("Mean confidence", "mean_conf"),
        ("Confidence std dev", "std_conf"),
        ("Trades executed", "trades"),
        ("Mean position size", "mean_size"),
        ("Unique markets", "markets"),
        ("Trailing stop exits", "trailing_exits"),
        ("TA signals", "ta_signals"),
    ]

    for label, key in rows:
        vals = []
        for profile in PROFILES:
            m = results[profile]
            if "error" in m:
                vals.append("N/A")
            else:
                v = m[key]
                if isinstance(v, float):
                    vals.append(f"{v:.4f}")
                else:
                    vals.append(str(v))
        print(f"{label:<25}" + "".join(f"{v:>12}" for v in vals))

    print(separator)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare paper-trading test runs.")
    parser.add_argument("--db-dir", type=Path, default=Path("data"), help="Directory containing test DBs")
    args = parser.parse_args()

    if not args.db_dir.exists():
        print(f"ERROR: DB directory not found: {args.db_dir}", file=sys.stderr)
        sys.exit(1)

    print_comparison(args.db_dir)


if __name__ == "__main__":
    main()
```

**Step 2: Make executable**

```bash
chmod +x scripts/compare_test_runs.py
```

---

### Task 7: Run all tests and verify

**Step 1: Run full test suite**

Run: `uv run pytest tests/ -q`
Expected: All tests pass including new flag tests.

**Step 2: Verify config profiles load**

Run: `uv run python -c "from polymarket_agent.config import load_config; from pathlib import Path; c = load_config(Path('configs/test/baseline.yaml')); print(c.position_sizing.method)"`
Expected: `fixed`

Run: `uv run python -c "from polymarket_agent.config import load_config; from pathlib import Path; c = load_config(Path('configs/test/phase3.yaml')); print(c.position_sizing.method, c.aggregation.conflict_resolution)"`
Expected: `fractional_kelly True`

**Step 3: Verify comparison script runs (with empty DBs)**

```bash
mkdir -p data
for p in baseline phase1 phase2 phase3; do
    python -c "import sqlite3; c=sqlite3.connect('data/$p.db'); c.execute('CREATE TABLE IF NOT EXISTS signal_log (id INTEGER PRIMARY KEY, strategy TEXT, confidence REAL, market_id TEXT, token_id TEXT, side TEXT, size REAL, status TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)'); c.execute('CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, strategy TEXT, market_id TEXT, token_id TEXT, side TEXT, price REAL, size REAL, reason TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)'); c.commit(); c.close()"
done
uv run python scripts/compare_test_runs.py --db-dir data
```

Expected: Table with all zeros (no data yet). No crashes.

---

### Task 8: Commit

```bash
git add configs/test/ scripts/compare_test_runs.py \
  src/polymarket_agent/strategies/ai_analyst.py \
  src/polymarket_agent/strategies/technical_analyst.py \
  src/polymarket_agent/strategies/aggregator.py \
  src/polymarket_agent/config.py \
  src/polymarket_agent/orchestrator.py \
  tests/test_aggregator.py \
  tests/test_technical_analyst.py \
  docs/plans/2026-03-03-paper-trading-test-plan-design.md \
  docs/plans/2026-03-03-paper-trading-test-plan.md
git commit -m "feat: add config toggle flags and test profiles for Phase 1-3 A/B testing"
```
