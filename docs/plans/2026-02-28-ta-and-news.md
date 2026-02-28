# Technical Analysis + News Intelligence Enhancement

**Date:** 2026-02-28
**Status:** Implementation

## Problem

The AI Analyst strategy receives only market question, description, and current price when asking the LLM for a probability estimate. This misses two key data sources:

1. **No quantitative context** — `DataProvider.get_price_history()` exists but no strategy uses it
2. **No real-world context** — the LLM has no recent news for each market question

## Solution

### 1. Technical Indicators Module (`strategies/indicators.py`)

Pure-computation module. Input: `list[PricePoint]`. Output: Pydantic models.

- EMA (periods 8/21, shorter than stock 12/26 for prediction market lifespans)
- RSI (14-period) + Stochastic RSI
- Bollinger Band squeeze detection
- ATR approximated from `abs(close[i] - close[i-1])` (no OHLC available)
- Minimum 21 data points required; returns None otherwise

### 2. News Provider Package (`news/`)

- `NewsProvider` protocol with `search(query, max_results) -> list[NewsItem]`
- Google News RSS implementation (free, no API key, uses `feedparser`)
- Tavily implementation (optional upgrade, 1k free/month)
- Cached + rate-limited wrapper using existing `TTLCache`

### 3. TechnicalAnalyst Strategy (`strategies/technical_analyst.py`)

Rule-based strategy implementing `Strategy` ABC:
- BUY: bullish EMA crossover + RSI not overbought + squeeze confirmation
- SELL: bearish EMA crossover + RSI not oversold + squeeze confirmation
- Confidence: EMA divergence (0.4) + RSI extremity (0.3) + squeeze (0.3)

### 4. Enhanced AI Analyst Prompt

Inject both TA summary and news headlines into the LLM prompt:
- Technical: price trend, EMA crossover, RSI, volatility
- News: recent headlines with dates
- Both sections optional — graceful degradation

### 5. Wiring

- Register `TechnicalAnalyst` in `STRATEGY_REGISTRY`
- Add `NewsConfig` to `AppConfig`
- Wire `NewsProvider` to `AIAnalyst` via orchestrator

## Implementation Order

1. `indicators.py` + tests (zero deps)
2. `news/` package + tests (parallel with step 1)
3. `technical_analyst.py` + tests (depends on 1)
4. Modify `ai_analyst.py` + tests (depends on 1 + 2)
5. Modify orchestrator + config (depends on 3 + 4)
6. Full verification

## New Dependencies

- `feedparser` — Google News RSS parsing (required)
- `tavily-python` — optional upgrade for news search
