# Trading Strategy Overhaul Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the current strategy execution defects, preserve the strongest existing structural edges, and replace the weakest strategy with a stronger semantic basket arbitrage approach.

**Architecture:** Normalize every strategy to emit executable long exposure on the correct outcome token rather than unsupported short-like `sell` entries. Keep structural strategies usable when no LLM client is present by separating pure math checks from optional LLM enrichment. Replace `cross_platform_arb`'s fuzzy single-leg basis trade with a Polymarket-first semantic basket arbitrage strategy that reasons over related markets and constraint violations.

**Tech Stack:** Python 3.12+, Pydantic v2, Typer, Ruff, Mypy, Pytest, existing Polymarket/Gamma data providers, optional OpenAI/Anthropic-compatible clients

---

## Context

The current review found five implementation problems that materially reduce live utility:

1. Bearish strategy signals are often emitted as `sell` entries, but the execution layer only supports selling an existing inventory position.
2. `date_curve_trader` and `sports_derivative_trader` return no signals at all when an LLM client is unavailable, even though their strongest structural checks do not need a model.
3. `whale_follower` drops outcome identity and frequently follows the wrong token.
4. `whale_follower` deduplication is process-lifetime state, so repeated but distinct whale trades are suppressed until restart.
5. `cross_platform_arb` is an unhedged fuzzy-match basis trade, not true arbitrage.

This plan fixes the defects first, then adds the stronger strategy.

## Design Rules

- Keep using `Signal` and the existing orchestrator/executor interfaces.
- Do not add direct subprocess calls outside `PolymarketData._run_cli()`.
- Use TDD for each behavior change.
- Make the smallest verifiable change per task.
- Prefer focused tests before broadening to wider suites.

## Target End State

- Every entry strategy emits an executable buy on the correct token for both bullish and bearish views.
- Structural strategies still produce pure-math signals without an LLM client.
- `whale_follower` follows the actual traded outcome and only deduplicates the same trade event.
- `cross_platform_arb` is removed or disabled in favor of a new `semantic_basket_arb` strategy.
- Config and docs reflect the new strategy surface clearly.

### Task 1: Fix Bearish Entry Semantics

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

**Step 1: Write the failing tests**

Add focused tests asserting that bearish entry signals use the complementary token with `side="buy"` instead of an unsupported entry `sell`.

```python
def test_signal_trader_over_midpoint_buys_no_token() -> None:
    strategy = SignalTrader()
    strategy.configure({"volume_threshold": 1000, "price_move_threshold": 0.05})
    market = _make_market("1", yes_price=0.70, volume_24h=10000)

    signals = strategy.analyze([market], data=None)

    assert len(signals) == 1
    assert signals[0].side == "buy"
    assert signals[0].token_id == "0xtok_1_no"
```

```python
def test_paper_trader_can_execute_bearish_entry_when_emitted_as_buy_no() -> None:
    signal = Signal(
        strategy="test",
        market_id="m1",
        token_id="no_tok",
        side="buy",
        confidence=0.8,
        target_price=0.30,
        size=30.0,
        reason="bearish view expressed as long no",
    )
    trader = PaperTrader(100.0, db)

    order = trader.place_order(signal)

    assert order is not None
    assert order.side == "buy"
```

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest \
  tests/test_signal_trader.py \
  tests/test_arbitrageur.py \
  tests/test_ai_analyst.py \
  tests/test_technical_analyst.py \
  tests/test_date_curve_trader.py \
  tests/test_sports_derivative_trader.py \
  tests/test_paper_trader.py -v
```

Expected: FAIL on the new assertions because bearish strategies still emit `sell` entries.

**Step 3: Write minimal implementation**

Convert bearish entry logic to buy the opposite outcome token.

Representative shape:

```python
if divergence > 0:
    token_id = market.clob_token_ids[0]   # Yes
else:
    token_id = market.clob_token_ids[1]   # No

return Signal(
    strategy=self.name,
    market_id=market.id,
    token_id=token_id,
    side="buy",
    confidence=round(confidence, 4),
    target_price=target_price_for_selected_token,
    size=self._order_size,
    reason=reason,
)
```

For strategies that currently use `target_price=yes_price`, set the No-token target price to `1.0 - yes_price` when the market has only Yes/No outcomes.

**Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest \
  tests/test_signal_trader.py \
  tests/test_arbitrageur.py \
  tests/test_ai_analyst.py \
  tests/test_technical_analyst.py \
  tests/test_date_curve_trader.py \
  tests/test_sports_derivative_trader.py \
  tests/test_paper_trader.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add \
  src/polymarket_agent/strategies/signal_trader.py \
  src/polymarket_agent/strategies/arbitrageur.py \
  src/polymarket_agent/strategies/ai_analyst.py \
  src/polymarket_agent/strategies/technical_analyst.py \
  src/polymarket_agent/strategies/date_curve_trader.py \
  src/polymarket_agent/strategies/sports_derivative_trader.py \
  tests/test_signal_trader.py \
  tests/test_arbitrageur.py \
  tests/test_ai_analyst.py \
  tests/test_technical_analyst.py \
  tests/test_date_curve_trader.py \
  tests/test_sports_derivative_trader.py \
  tests/test_paper_trader.py
git commit -m "fix: normalize bearish entries as executable long-no trades"
```

### Task 2: Keep Structural Strategies Active Without an LLM Client

**Files:**
- Modify: `src/polymarket_agent/strategies/date_curve_trader.py`
- Modify: `src/polymarket_agent/strategies/sports_derivative_trader.py`
- Test: `tests/test_date_curve_trader.py`
- Test: `tests/test_sports_derivative_trader.py`

**Step 1: Write the failing tests**

Add tests that prove pure structural checks still run with `_client = None`.

```python
def test_date_curve_trader_runs_term_structure_without_llm_client() -> None:
    trader = DateCurveTrader()
    trader._client = None
    markets = [
        _make_market("m1", "US forces enter Iran by March 7?", 0.20),
        _make_market("m2", "US forces enter Iran by March 14?", 0.10),
    ]

    signals = trader.analyze(markets, MagicMock())

    assert {s.side for s in signals} == {"buy"}
```

```python
def test_sports_derivative_trader_runs_structure_checks_without_llm_client() -> None:
    trader = SportsDerivativeTrader()
    trader._client = None
    markets = [...]

    signals = trader.analyze(markets, MagicMock())

    assert any("hierarchy" in s.reason or "bracket_sum" in s.reason for s in signals)
```

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_date_curve_trader.py tests/test_sports_derivative_trader.py -v
```

Expected: FAIL because `analyze()` exits early when `_client is None`.

**Step 3: Write minimal implementation**

Refactor `analyze()` so it always:

1. Detects curves/graphs using regex fallback when needed
2. Runs structural checks unconditionally
3. Runs LLM enrichment only when `self._client is not None and self._can_call()`

Representative shape:

```python
curves = self._detect_curves(markets)
signals = []
for curve in curves:
    signals.extend(self._check_term_structure(curve))
    if self._client is not None and self._can_call():
        signals.extend(self._analyze_curve_with_news(curve))
return signals
```

**Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/test_date_curve_trader.py tests/test_sports_derivative_trader.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add \
  src/polymarket_agent/strategies/date_curve_trader.py \
  src/polymarket_agent/strategies/sports_derivative_trader.py \
  tests/test_date_curve_trader.py \
  tests/test_sports_derivative_trader.py
git commit -m "fix: keep structural strategy checks active without llm"
```

### Task 3: Make WhaleFollower Follow the Correct Outcome

**Files:**
- Modify: `src/polymarket_agent/strategies/whale_follower.py`
- Modify: `src/polymarket_agent/data/models.py`
- Test: `tests/test_whale_follower.py`

**Step 1: Write the failing tests**

Add tests that use `outcome` or `outcome_index` from the trade payload.

```python
def test_whale_follower_uses_no_token_for_no_outcome_trade() -> None:
    strategy = WhaleFollower()
    strategy.configure({"min_trade_size": 100.0})
    data = MagicMock()
    data.get_leaderboard.return_value = [_make_trader("whale1", 1)]
    data.get_trader_trades.return_value = [{
        "condition_id": "100",
        "slug": "event-100",
        "side": "BUY",
        "outcome": "No",
        "outcome_index": 1,
        "size": "500",
        "price": "0.40",
    }]

    signals = strategy.analyze([_make_market("100", slug="event-100")], data)

    assert signals[0].token_id.endswith("_no")
    assert signals[0].side == "buy"
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_whale_follower.py -v
```

Expected: FAIL because `whale_follower` currently falls back to the Yes token.

**Step 3: Write minimal implementation**

Preserve outcome identity from the trade payload into `WhaleTrade`, then map `outcome_index` to `market.clob_token_ids[outcome_index]`.

```python
outcome_index = int(t.get("outcome_index", 0) or 0)
token_id = market.clob_token_ids[outcome_index]
```

Keep a safe fallback to outcome text matching when `outcome_index` is missing.

**Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_whale_follower.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add \
  src/polymarket_agent/strategies/whale_follower.py \
  src/polymarket_agent/data/models.py \
  tests/test_whale_follower.py
git commit -m "fix: map whale trades to the correct outcome token"
```

### Task 4: Replace Process-Lifetime Dedup With Trade-Event Dedup

**Files:**
- Modify: `src/polymarket_agent/strategies/whale_follower.py`
- Test: `tests/test_whale_follower.py`

**Step 1: Write the failing tests**

Add tests proving the same trader can trigger multiple signals on distinct trades, while exact duplicate trade events are suppressed.

```python
def test_whale_follower_allows_distinct_trade_events_same_market() -> None:
    strategy = WhaleFollower()
    strategy.configure({"min_trade_size": 100.0})
    trades = [
        _cli_trade("100", "event-100", "BUY", 500) | {"transaction_hash": "0x1"},
        _cli_trade("100", "event-100", "BUY", 700) | {"transaction_hash": "0x2"},
    ]
    ...
    assert len(signals) == 2
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_whale_follower.py -v
```

Expected: FAIL because `_seen` is keyed only by `trader_name:market_id`.

**Step 3: Write minimal implementation**

Use a true trade-event dedup key:

```python
dedup_key = (
    trade.trader_address,
    trade.market_id,
    trade.timestamp,
    trade.price,
    trade.size,
    trade.side,
)
```

If `transaction_hash` exists, prefer it.

**Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_whale_follower.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/polymarket_agent/strategies/whale_follower.py tests/test_whale_follower.py
git commit -m "fix: deduplicate whale trades by event identity"
```

### Task 5: Introduce Semantic Basket Arbitrage

**Files:**
- Create: `src/polymarket_agent/strategies/semantic_basket_arb.py`
- Modify: `src/polymarket_agent/data/models.py`
- Modify: `src/polymarket_agent/orchestrator.py`
- Modify: `src/polymarket_agent/config.py`
- Modify: `config.yaml`
- Test: `tests/test_semantic_basket_arb.py`
- Test: `tests/test_orchestrator.py`
- Doc: `README.md`

**Step 1: Write the failing tests**

Start with Polymarket-only relation checks before external venue integration.

```python
def test_semantic_basket_arb_detects_parent_child_probability_violation() -> None:
    strategy = SemanticBasketArb()
    markets = [
        _market("series", "Will Lakers win WCF?", yes_price=0.30),
        _market("title", "Will Lakers win NBA title?", yes_price=0.40),
    ]

    signals = strategy.analyze(markets, MagicMock())

    assert len(signals) == 2
    assert {s.market_id for s in signals} == {"series", "title"}
```

```python
def test_semantic_basket_arb_detects_multi_bracket_sum_violation() -> None:
    strategy = SemanticBasketArb()
    markets = [...]
    signals = strategy.analyze(markets, MagicMock())
    assert any("basket_sum" in s.reason for s in signals)
```

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_semantic_basket_arb.py -v
```

Expected: FAIL because the strategy file does not exist yet.

**Step 3: Write minimal implementation**

Create a first version that:

1. Groups related markets by semantic family
2. Supports two relation types:
   - parent/child monotonicity
   - mutually exclusive basket sums
3. Emits executable `buy` signals on the appropriate outcome token
4. Uses regex/string heuristics first
5. Leaves cross-venue matching for a later iteration

Suggested data model:

```python
class SemanticRelation(BaseModel):
    relation_type: Literal["subset", "exclusive_set"]
    family_id: str
    market_ids: list[str]
```

Suggested strategy skeleton:

```python
class SemanticBasketArb(Strategy):
    name = "semantic_basket_arb"

    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        families = self._build_families(markets)
        signals: list[Signal] = []
        for family in families:
            signals.extend(self._check_subset_constraints(family))
            signals.extend(self._check_exclusive_sum_constraints(family))
        return signals
```

**Step 4: Run tests to verify it passes**

Run:

```bash
uv run pytest tests/test_semantic_basket_arb.py tests/test_orchestrator.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add \
  src/polymarket_agent/strategies/semantic_basket_arb.py \
  src/polymarket_agent/data/models.py \
  src/polymarket_agent/orchestrator.py \
  src/polymarket_agent/config.py \
  config.yaml \
  tests/test_semantic_basket_arb.py \
  tests/test_orchestrator.py \
  README.md
git commit -m "feat: add semantic basket arbitrage strategy"
```

### Task 6: Retire or Hard-Disable CrossPlatformArb

**Files:**
- Modify: `src/polymarket_agent/strategies/cross_platform_arb.py`
- Modify: `config.yaml`
- Modify: `README.md`
- Test: `tests/test_cross_platform_arb.py`

**Step 1: Write the failing tests**

Add one of these explicit policy tests:

Option A, deprecate but keep module:

```python
def test_cross_platform_arb_returns_no_signals_when_disabled_by_policy() -> None:
    strategy = CrossPlatformArb()
    strategy.configure({"enabled": False})
    assert strategy.analyze([_make_market()], MagicMock()) == []
```

Option B, convert it into a thin adapter onto `semantic_basket_arb` and test for delegation.

**Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_cross_platform_arb.py -v
```

Expected: FAIL.

**Step 3: Write minimal implementation**

Preferred approach: leave the file for compatibility, but document it as deprecated and make the default config disabled.

```python
logger.warning("cross_platform_arb is deprecated; use semantic_basket_arb")
return []
```

If users still need it, guard it behind an explicit opt-in flag and label it experimental.

**Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_cross_platform_arb.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add \
  src/polymarket_agent/strategies/cross_platform_arb.py \
  tests/test_cross_platform_arb.py \
  config.yaml \
  README.md
git commit -m "chore: deprecate fuzzy cross platform arb"
```

### Task 7: Verification Sweep and Operator Docs

**Files:**
- Modify: `README.md`
- Modify: `HANDOFF.md`

**Step 1: Write doc updates**

Update:

- strategy descriptions
- bearish exposure semantics
- which strategies require LLM access and which do not
- `semantic_basket_arb` config example
- `cross_platform_arb` deprecation notice

Add a concise `HANDOFF.md` entry with:

- Problem
- Solution
- Edits
- NOT Changed
- Verification

**Step 2: Run focused verification**

Run:

```bash
ruff check \
  src/polymarket_agent/strategies \
  src/polymarket_agent/orchestrator.py \
  src/polymarket_agent/config.py \
  tests/test_signal_trader.py \
  tests/test_arbitrageur.py \
  tests/test_ai_analyst.py \
  tests/test_technical_analyst.py \
  tests/test_date_curve_trader.py \
  tests/test_sports_derivative_trader.py \
  tests/test_whale_follower.py \
  tests/test_cross_platform_arb.py \
  tests/test_semantic_basket_arb.py \
  tests/test_paper_trader.py
```

Expected: PASS.

**Step 3: Run impacted tests**

Run:

```bash
uv run pytest \
  tests/test_signal_trader.py \
  tests/test_arbitrageur.py \
  tests/test_ai_analyst.py \
  tests/test_technical_analyst.py \
  tests/test_date_curve_trader.py \
  tests/test_sports_derivative_trader.py \
  tests/test_whale_follower.py \
  tests/test_cross_platform_arb.py \
  tests/test_semantic_basket_arb.py \
  tests/test_paper_trader.py \
  tests/test_orchestrator.py -v
```

Expected: PASS.

**Step 4: Run typecheck on touched modules**

Run:

```bash
mypy src/polymarket_agent/strategies src/polymarket_agent/orchestrator.py src/polymarket_agent/config.py
```

Expected: PASS.

**Step 5: Commit**

```bash
git add README.md HANDOFF.md
git commit -m "docs: update strategy architecture and operator guidance"
```

## Notes for the Implementer

- Keep the execution model simple: long Yes or long No. Do not add synthetic shorting unless explicitly requested.
- Do not broaden `semantic_basket_arb` into external venue execution in the first pass.
- Avoid turning the semantic grouping into a giant LLM dependency. Start with deterministic grouping and only add optional LLM assistance if local heuristics prove too weak.
- Reuse existing models and helper patterns from `date_curve_trader` and `sports_derivative_trader` where possible.

## Suggested Rollout Order

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 7 verification checkpoint
6. Task 5
7. Task 6
8. Task 7 final verification/docs
