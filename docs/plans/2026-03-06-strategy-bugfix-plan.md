# Trading Strategy Bugfix Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix five confirmed bugs that materially reduce live P&L, deprecate the unhedged cross-platform arb, and update docs.

**Scope:** Tasks 1-4 (bugfixes) + Task 6 (deprecate cross_platform_arb) + Task 7 (verification/docs). Task 5 (semantic_basket_arb) from the original overhaul plan is **deferred** — ship fixes first, measure P&L impact, then decide.

**Tech Stack:** Python 3.12+, Pydantic v2, Typer, Ruff, Mypy, Pytest, existing Polymarket/Gamma data providers

---

## Context

Code review of the strategy overhaul plan (`docs/plans/2026-03-06-trading-strategy-overhaul.md`) confirmed five bugs:

1. **Bearish entry signals emit `side="sell"`** but the executor (`paper.py:148`) requires an existing position to sell — every bearish entry silently fails. This means the system cannot express bearish views at all.
2. **`date_curve_trader` and `sports_derivative_trader` return `[]`** when LLM client is None (`date_curve_trader.py:259`, `sports_derivative_trader.py:241`), blocking pure-math structural checks that don't need a model.
3. **`whale_follower` defaults to Yes token** (`whale_follower.py:118`) regardless of actual whale trade outcome — some signals are directionally inverted.
4. **`whale_follower` dedup key `trader:market`** (`whale_follower.py:110`) suppresses distinct trades by the same whale on the same market.
5. **`cross_platform_arb` is an unhedged basis trade** — single-leg execution with 55% fuzzy string matching, not real arbitrage.

## Design Rules

- Keep using `Signal` and the existing orchestrator/executor interfaces.
- Do not add direct subprocess calls outside `PolymarketData._run_cli()`.
- Use TDD for each behavior change.
- Make the smallest verifiable change per task.
- All imports at top of file, no `__future__` imports.

---

### Task 1: Fix Bearish Entry Signals

**Impact: High** — unlocks all bearish trading.

**Files:**
- Modify: `src/polymarket_agent/strategies/signal_trader.py`
- Modify: `src/polymarket_agent/strategies/arbitrageur.py`
- Modify: `src/polymarket_agent/strategies/ai_analyst.py`
- Modify: `src/polymarket_agent/strategies/technical_analyst.py`
- Modify: `src/polymarket_agent/strategies/date_curve_trader.py`
- Modify: `src/polymarket_agent/strategies/sports_derivative_trader.py`
- Test: `tests/test_signal_trader.py`
- Test: `tests/test_arbitrageur.py`
- Test: `tests/test_ai_analyst.py`
- Test: `tests/test_technical_analyst.py`
- Test: `tests/test_date_curve_trader.py`
- Test: `tests/test_sports_derivative_trader.py`
- Test: `tests/test_paper_trader.py`

**DO NOT CHANGE:** `ExitManager` sells (position exits) and `MarketMaker` sells (ask quotes) — these are correct.

**Step 1: Write failing tests**

Add focused tests per strategy asserting that bearish entry signals use `side="buy"` on the complementary token:

```python
# signal_trader: yes_price > 0.5 should buy No token
def test_signal_trader_bearish_buys_no_token():
    market = _make_market("1", yes_price=0.70, volume_24h=10000)
    signals = strategy.analyze([market], data=None)
    assert signals[0].side == "buy"
    assert signals[0].token_id == "0xtok_1_no"
    assert abs(signals[0].target_price - 0.30) < 0.01
```

```python
# paper_trader: confirm bearish-as-buy-no executes successfully
def test_paper_trader_executes_bearish_buy_no():
    signal = Signal(strategy="test", market_id="m1", token_id="no_tok",
                    side="buy", confidence=0.8, target_price=0.30, size=30.0,
                    reason="bearish via long no")
    order = trader.place_order(signal)
    assert order is not None
    assert order.side == "buy"
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_signal_trader.py tests/test_arbitrageur.py tests/test_ai_analyst.py tests/test_technical_analyst.py tests/test_date_curve_trader.py tests/test_sports_derivative_trader.py tests/test_paper_trader.py -v
```

**Step 3: Implement**

Convert bearish entry logic in each strategy. The general pattern:

```python
# Before                              # After
side = "sell"                          side = "buy"
token_id = clob_token_ids[0]          token_id = clob_token_ids[1]  # No
target_price = yes_price               target_price = 1.0 - yes_price
```

Specific locations and nuances:

| File | Line(s) | Nuance |
|------|---------|--------|
| `signal_trader.py` | 77-80 | Straightforward: already picks `clob_token_ids[1]`, just change side to `"buy"` and set `target_price = 1.0 - yes_price` |
| `arbitrageur.py` | 67 | Multi-outcome: when `price_sum > 1.0`, buy the cheapest outcome rather than sell the most expensive |
| `ai_analyst.py` | 571 | When `divergence < 0`: `token_id=clob_token_ids[1]`, `side="buy"`, `target_price=1.0-yes_price` |
| `technical_analyst.py` | 128 | Needs market threaded through `_evaluate` → `_generate_signal` so bearish branch can pick `clob_token_ids[1]` |
| `date_curve_trader.py` | 461, 552 | Overpriced date → buy its No token |
| `sports_derivative_trader.py` | 521, 604, 678, 834 | 4 sell sites; line 678 (cascade) uses price direction — handle `"down"` by buying No |

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_signal_trader.py tests/test_arbitrageur.py tests/test_ai_analyst.py tests/test_technical_analyst.py tests/test_date_curve_trader.py tests/test_sports_derivative_trader.py tests/test_paper_trader.py -v
```

**Step 5: Commit**

```bash
git add src/polymarket_agent/strategies/signal_trader.py \
  src/polymarket_agent/strategies/arbitrageur.py \
  src/polymarket_agent/strategies/ai_analyst.py \
  src/polymarket_agent/strategies/technical_analyst.py \
  src/polymarket_agent/strategies/date_curve_trader.py \
  src/polymarket_agent/strategies/sports_derivative_trader.py \
  tests/test_signal_trader.py tests/test_arbitrageur.py \
  tests/test_ai_analyst.py tests/test_technical_analyst.py \
  tests/test_date_curve_trader.py tests/test_sports_derivative_trader.py \
  tests/test_paper_trader.py
git commit -m "fix: normalize bearish entries to buy complementary token"
```

---

### Task 2: Keep Structural Strategies Active Without LLM

**Impact: Medium** — near-arbitrage edges available without LLM.

**Files:**
- Modify: `src/polymarket_agent/strategies/date_curve_trader.py`
- Modify: `src/polymarket_agent/strategies/sports_derivative_trader.py`
- Test: `tests/test_date_curve_trader.py`
- Test: `tests/test_sports_derivative_trader.py`

**Step 1: Write failing tests**

```python
def test_date_curve_trader_term_structure_without_llm():
    trader = DateCurveTrader()
    trader._client = None
    # Two markets forming a monotonicity violation
    markets = [
        _make_market("m1", "US forces enter Iran by March 7?", 0.20),
        _make_market("m2", "US forces enter Iran by March 14?", 0.10),
    ]
    signals = trader.analyze(markets, MagicMock())
    assert len(signals) > 0
    assert all(s.side == "buy" for s in signals)
```

```python
def test_sports_derivative_trader_bracket_sum_without_llm():
    trader = SportsDerivativeTrader()
    trader._client = None
    # Bracket markets with sum > 1.0
    signals = trader.analyze(markets, MagicMock())
    assert any("bracket_sum" in s.reason or "hierarchy" in s.reason for s in signals)
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_date_curve_trader.py tests/test_sports_derivative_trader.py -v
```

**Step 3: Implement**

Refactor `analyze()` to always run structural checks, gate only LLM enrichment:

```python
def analyze(self, markets, data):
    curves = self._detect_curves(markets)       # regex fallback works without LLM
    if not curves:
        return []
    signals = []
    for curve in curves:
        signals.extend(self._check_term_structure(curve))  # pure math, always runs
        if self._client is not None and self._can_call():
            signals.extend(self._analyze_with_news(curve))  # LLM only
    return signals
```

Apply the same pattern to `sports_derivative_trader.py` — remove early `_client is None` return, keep structural checks (`_check_bracket_sums`, `_check_hierarchy`, `_check_cascades`), gate only LLM enrichment.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_date_curve_trader.py tests/test_sports_derivative_trader.py -v
```

**Step 5: Commit**

```bash
git add src/polymarket_agent/strategies/date_curve_trader.py \
  src/polymarket_agent/strategies/sports_derivative_trader.py \
  tests/test_date_curve_trader.py tests/test_sports_derivative_trader.py
git commit -m "fix: run structural checks without LLM client"
```

---

### Task 3: Fix WhaleFollower Wrong Token

**Impact: Medium** — some signals currently directionally inverted.

**Files:**
- Modify: `src/polymarket_agent/data/models.py`
- Modify: `src/polymarket_agent/strategies/whale_follower.py`
- Test: `tests/test_whale_follower.py`

**Step 1: Write failing tests**

```python
def test_whale_follower_uses_no_token_for_no_outcome():
    data.get_trader_trades.return_value = [{
        "condition_id": "100", "slug": "event-100",
        "side": "BUY", "outcome": "No", "outcome_index": 1,
        "size": "500", "price": "0.40",
    }]
    signals = strategy.analyze([_make_market("100", slug="event-100")], data)
    assert signals[0].token_id == "0xtok_100_no"
    assert signals[0].side == "buy"
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_whale_follower.py -v
```

**Step 3: Implement**

Add `outcome_index: int = 0` to `WhaleTrade` in `models.py`.

In `whale_follower.py`:

```python
# _fetch_whale_trades — pass outcome_index through
outcome_index=int(t.get("outcome_index", 0) or 0)

# _generate_signals — use outcome_index for correct token
idx = min(trade.outcome_index, len(market.clob_token_ids) - 1)
token_id = trade.token_id or market.clob_token_ids[idx]
target_price = market.outcome_prices[idx] if len(market.outcome_prices) > idx else 0.5

# Whale sells → buy opposite token (same Task 1 pattern)
if trade.side == "sell":
    buy_idx = 1 - idx if len(market.clob_token_ids) > 1 else idx
    token_id = market.clob_token_ids[buy_idx]
    target_price = market.outcome_prices[buy_idx] if len(market.outcome_prices) > buy_idx else 0.5
side = "buy"  # always buy
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_whale_follower.py -v
```

**Step 5: Commit**

```bash
git add src/polymarket_agent/data/models.py \
  src/polymarket_agent/strategies/whale_follower.py \
  tests/test_whale_follower.py
git commit -m "fix: map whale trades to correct outcome token"
```

---

### Task 4: Fix WhaleFollower Dedup

**Impact: Low-Medium** — more signals from distinct whale trades.

**Files:**
- Modify: `src/polymarket_agent/data/models.py`
- Modify: `src/polymarket_agent/strategies/whale_follower.py`
- Test: `tests/test_whale_follower.py`

**Step 1: Write failing tests**

```python
def test_whale_follower_allows_distinct_trades_same_market():
    trade1 = _cli_trade("100", "event-100", "BUY", 500) | {"transaction_hash": "0x111"}
    trade2 = _cli_trade("100", "event-100", "BUY", 700) | {"transaction_hash": "0x222"}
    data.get_trader_trades.return_value = [trade1, trade2]
    signals = strategy.analyze([_make_market("100", slug="event-100")], data)
    assert len(signals) == 2

def test_whale_follower_deduplicates_exact_same_trade():
    trade = _cli_trade("100", "event-100", "BUY", 500) | {"transaction_hash": "0xaaa"}
    data.get_trader_trades.return_value = [trade, trade]
    signals = strategy.analyze([_make_market("100", slug="event-100")], data)
    assert len(signals) == 1
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_whale_follower.py -v
```

**Step 3: Implement**

Add `transaction_hash: str = ""` to `WhaleTrade` in `models.py`.

Change dedup key in `whale_follower.py`:

```python
if trade.transaction_hash:
    dedup_key = trade.transaction_hash
else:
    dedup_key = f"{trade.trader_address}:{trade.market_id}:{trade.timestamp}:{trade.price}:{trade.size}:{trade.side}"
```

**Note:** Existing `test_whale_follower_deduplicates` (line 79-93) expects 1 signal from 2 trades with different sizes. After fix, these are distinct events — update that test.

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_whale_follower.py -v
```

**Step 5: Commit**

```bash
git add src/polymarket_agent/data/models.py \
  src/polymarket_agent/strategies/whale_follower.py \
  tests/test_whale_follower.py
git commit -m "fix: deduplicate whale trades by event identity"
```

---

### Task 6: Deprecate CrossPlatformArb

**Files:**
- Modify: `src/polymarket_agent/strategies/cross_platform_arb.py`
- Modify: `config.yaml`
- Test: `tests/test_cross_platform_arb.py`

**Step 1: Write failing test**

```python
def test_cross_platform_arb_deprecated_returns_empty():
    strategy = CrossPlatformArb()
    strategy.configure({})  # default = not enabled
    signals = strategy.analyze([_make_market()], MagicMock())
    assert signals == []
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_cross_platform_arb.py -v
```

**Step 3: Implement**

Add `self._enabled = False` to `__init__`, read from config in `configure()`:

```python
self._enabled = bool(config.get("enabled", False))
```

At top of `analyze()`:

```python
if not self._enabled:
    logger.warning("cross_platform_arb is deprecated; use date_curve_trader / sports_derivative_trader")
    return []
```

Ensure `config.yaml` has `enabled: false` for cross_platform_arb.

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_cross_platform_arb.py -v
```

**Step 5: Commit**

```bash
git add src/polymarket_agent/strategies/cross_platform_arb.py \
  tests/test_cross_platform_arb.py config.yaml
git commit -m "chore: deprecate cross-platform arb strategy"
```

---

### Task 7: Verification Sweep and Docs

**Step 1: Full verification**

```bash
uv run pytest tests/ -v --tb=short
ruff check src/ tests/
mypy src/polymarket_agent/strategies src/polymarket_agent/orchestrator.py src/polymarket_agent/config.py
```

**Step 2: Update docs**

- `README.md` — strategy table, bearish semantics, LLM requirements, deprecation notice
- `HANDOFF.md` — entry with Problem/Solution/Edits/NOT Changed/Verification

**Step 3: Commit**

```bash
git add README.md HANDOFF.md
git commit -m "docs: update strategy docs for bugfix rollout"
```

---

## Rollout Order

1. Task 1 — Bearish entry fix (biggest P&L impact, affects 6 strategies)
2. Task 2 — Structural strategies without LLM (depends on Task 1 for correct signal sides)
3. Task 3 — WhaleFollower wrong token (includes its own bearish entry fix)
4. Task 4 — WhaleFollower dedup (small, isolated)
5. Task 6 — Deprecate cross_platform_arb (trivial)
6. Task 7 — Verification and docs

## Notes for the Implementer

- Keep the execution model simple: long Yes or long No. Do not add synthetic shorting.
- ExitManager and MarketMaker sell signals are correct — do not convert them.
- `arbitrageur.py` handles multi-outcome markets — buy the cheapest outcome, don't assume binary Yes/No.
- `technical_analyst.py` needs the market object threaded through `_evaluate` → `_generate_signal` for the fix.
- `sports_derivative_trader.py` has 4 distinct sell sites with different patterns — handle each individually.
- Existing whale_follower dedup test needs updating after Task 4 (trades with different sizes are now distinct events).
