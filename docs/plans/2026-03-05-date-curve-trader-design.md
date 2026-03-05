# DateCurveTrader Strategy Design

## Problem

Polymarket has date-based prediction markets like "US forces enter Iran by...?" with
options at increasing dates (March 7, March 14, March 31, etc.). These form cumulative
probability curves where later dates are strict supersets of earlier ones. Current
strategies treat each market independently, missing two key edges:

1. **News-driven curve repricing** — news shifts the entire date curve, but individual
   date options lag in repricing, creating temporary mispricings
2. **Term structure arbitrage** — mathematical constraints (monotonicity) can be violated,
   offering risk-free trades

## Approach: News-Primary + Term Structure Validation

A standalone `DateCurveTrader` strategy that:
- Groups date-based markets into probability curves
- Validates term structure constraints (pure math)
- Uses LLM + news to predict fair curve shape and trade divergences

### Why Not Other Approaches

- **Temporal decay harvesting** (selling near-term options): Dangerous for geopolitical
  events — picking up pennies in front of a steamroller. Small steady gains, catastrophic
  tail-risk losses.
- **Pure term structure arbitrage**: Real but thin edge. Mispricings are rare and small
  on liquid markets. Good as secondary validation, not primary strategy.
- **News-only without curve awareness**: Misses the relational structure between dates.
  The curve context gives the LLM much better information for probability estimation.

## Architecture

### New Data Models (`src/polymarket_agent/data/models.py`)

```python
class DateCurvePoint(BaseModel):
    date: str              # ISO date (YYYY-MM-DD)
    market: Market
    price: float           # current Yes price

class DateCurve(BaseModel):
    base_question: str     # shared event description
    points: list[DateCurvePoint]  # sorted chronologically
```

### Strategy Class (`src/polymarket_agent/strategies/date_curve_trader.py`)

```
DateCurveTrader(Strategy)
  ├── configure(config)           # load params
  ├── analyze(markets, data)      # main entry point
  ├── _detect_curves(markets)     # LLM-based grouping (cached 1hr)
  ├── _check_term_structure(curve) # math: monotonicity → arb signals
  ├── _analyze_curve(curve, news)  # LLM: news → fair curve → divergence signals
  └── _call_llm(prompt)           # shared LLM call (same pattern as AIAnalyst)
```

### Pipeline Flow

```
All markets → Detect date curves (LLM, cached)
                    ↓
              For each curve:
                    ├── Term structure check → Arbitrage signals (high confidence)
                    └── News + LLM analysis → Divergence signals (scaled confidence)
                    ↓
              Standard Signal objects → Aggregator → Execution
```

## Component Details

### 1. Curve Detection

**Primary**: LLM call with all market questions. Prompt asks it to identify "by date X"
patterns and group related markets. Returns JSON with groups + extracted ISO dates.

**Cache**: Results cached with 1-hour TTL since market groupings rarely change.

**Fallback**: Regex matching on patterns like "by [Month Day]", "before [Date]" with
prefix-based grouping (similar to existing `_find_sibling_brackets`).

### 2. Term Structure Validation

Pure math, no LLM needed:

- **Monotonicity**: `price[i] <= price[i+1]` for consecutive dates. Violation = arbitrage.
- **Marginal probabilities**: `P(event in [date_i, date_i+1]) = price[i+1] - price[i]`.
  Negative marginal = guaranteed mispricing.
- **Signal**: Buy underpriced later date, sell overpriced earlier date. Confidence: 0.9.

### 3. News-Driven Curve Analysis

One LLM call **per curve** (not per market — much more efficient):

**Input to LLM**:
- Full curve with all dates and current market prices
- Today's date for temporal context
- Recent news headlines (from existing news providers)
- Sentiment summary (if enabled)

**LLM prompt** asks:
- How does recent news shift the likely timeline?
- Estimate fair probability for each date

**Signal generation**:
- Compare LLM estimate vs market price for each date point
- Where `|divergence| > min_divergence` → emit buy/sell signal
- Confidence: sigmoid scaling (same as AIAnalyst)
- Apply Platt scaling to correct LLM hedging bias

## Configuration

```yaml
date_curve_trader:
  enabled: true
  min_divergence: 0.10        # lower than ai_analyst (curve context = higher conviction)
  order_size: 25.0
  min_price: 0.03             # date markets can have very low near-term prices
  max_calls_per_hour: 10
  cache_ttl_seconds: 3600
  arb_confidence: 0.9
  provider: openai
  model: ...
  base_url: ...
```

## Files to Create/Modify

| File | Change |
|------|--------|
| `src/polymarket_agent/strategies/date_curve_trader.py` | New: strategy class |
| `src/polymarket_agent/data/models.py` | Add DateCurvePoint, DateCurve |
| `src/polymarket_agent/orchestrator.py` | Register in STRATEGY_REGISTRY, wire news |
| `config.yaml` | Add date_curve_trader section |
| `tests/test_date_curve_trader.py` | New: unit tests |

## Reusable Components

- LLM client init pattern from `AIAnalyst` (`_init_client`, `_init_openai_client`, etc.)
- `AIAnalyst._call_llm()` pattern for provider-agnostic LLM calls
- `AIAnalyst._fetch_news()` / `_format_news_summary()` for news context
- `AIAnalyst._extremize()` for Platt scaling
- `AIAnalyst._score_news_sentiment()` for sentiment enrichment
- News provider wiring from `orchestrator.py`

## Verification

```bash
uv run pytest tests/test_date_curve_trader.py -v    # new tests
uv run pytest tests/ -v                              # all tests pass
ruff check src/                                      # lint clean
mypy src/                                            # type clean
uv run polymarket-agent tick                         # runs in tick loop
```
