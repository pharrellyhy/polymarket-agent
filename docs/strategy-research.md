# Strategy Research: AIAnalyst & TechnicalAnalyst Improvements

*Date: 2026-03-03*

This document synthesizes academic research and practitioner findings relevant to improving the polymarket-agent's AIAnalyst and TechnicalAnalyst strategies. Each section identifies current implementation gaps and proposes evidence-based improvements with citations.

---

## Table of Contents

1. [AIAnalyst Domain — State-of-the-Art LLM Forecasting](#1-aianalyst-domain--state-of-the-art-llm-forecasting)
   - [1.1 Current Implementation Gaps](#11-current-implementation-gaps)
   - [1.2 AIA Forecaster Architecture](#12-aia-forecaster-architecture)
   - [1.3 Halawi et al. Scratchpad Prompting](#13-halawi-et-al-scratchpad-prompting)
   - [1.4 LLM Ensemble Methods (Wisdom of Silicon Crowd)](#14-llm-ensemble-methods-wisdom-of-silicon-crowd)
   - [1.5 Extremization Calibration (Platt Scaling)](#15-extremization-calibration-platt-scaling)
   - [1.6 Structured Reasoning Decomposition](#16-structured-reasoning-decomposition)
   - [1.7 Multi-Model Ensemble Voting](#17-multi-model-ensemble-voting)
2. [TechnicalAnalyst Domain — Better Indicators & Adaptive Methods](#2-technicalanalyst-domain--better-indicators--adaptive-methods)
   - [2.1 Current Implementation Gaps](#21-current-implementation-gaps)
   - [2.2 MACD Integration](#22-macd-integration)
   - [2.3 RSI/MACD Divergence Detection](#23-rsimacd-divergence-detection)
   - [2.4 Adaptive Thresholds via Volatility Regime Detection](#24-adaptive-thresholds-via-volatility-regime-detection)
   - [2.5 Multi-Timeframe Analysis](#25-multi-timeframe-analysis)
   - [2.6 Market Regime Classification](#26-market-regime-classification)
3. [Cross-Cutting Improvements](#3-cross-cutting-improvements)
   - [3.1 Fractional Kelly Position Sizing](#31-fractional-kelly-position-sizing)
   - [3.2 Signal Confidence Calibration](#32-signal-confidence-calibration)
   - [3.3 Trailing Stops](#33-trailing-stops)
   - [3.4 Ensemble Signal Aggregation](#34-ensemble-signal-aggregation)
4. [References](#4-references)

---

## 1. AIAnalyst Domain — State-of-the-Art LLM Forecasting

### 1.1 Current Implementation Gaps

The AIAnalyst (`src/polymarket_agent/strategies/ai_analyst.py`) has several structural limitations relative to the academic state of the art:

| Gap | Current Behavior | Impact |
|-----|------------------|--------|
| **Single-shot prompt** | Asks for a bare probability number with no reasoning structure | Misses the Brier score improvements from scratchpad/chain-of-thought prompting (Halawi et al.) |
| **Linear confidence** | `confidence = min(abs(divergence) / 0.3, 1.0)` — raw divergence linearly mapped to 0–1 | Not a calibrated win probability; distorts Kelly criterion inputs |
| **No calibration** | LLM probability used as-is | LLMs consistently hedge toward 0.5 (AIA Forecaster); Platt scaling with α=√3 corrects this |
| **Single model** | One local Qwen 35B model | 12-model ensembles match human crowd accuracy (Schoenegger et al.); single models are noisier |
| **No retrieval augmentation** | News headlines included but no structured search | Agentic search reduces Brier score from 0.1230 → 0.1140 (AIA Forecaster) |
| **No self-supervised fine-tuning** | Prompt-only approach | Halawi et al. fine-tuned on 6,000 curated samples where the model beat the crowd |

### 1.2 AIA Forecaster Architecture

**Paper:** "AIA Forecaster: Technical Report" [1]

The AIA Forecaster is a multi-agent LLM forecasting system that achieves performance "statistically indistinguishable from expert superforecasters" on ForecastBench (p=0.1522).

**Architecture — Three Components:**

1. **Multi-Agent Search:** M independent forecasting agents (M=10 in production) each conduct adaptive, iterative search with full discretion over queries. Each agent's search path is formalized as:
   ```
   πᵢ: q → E₁ → E₂ → ... → Eₙ → (Rᵢ, pᵢ)
   ```
   where each query Eₖ conditions on prior search results. Agents operate independently and may follow diverging research paths, ensuring diversity of evidence.

2. **Supervisor Agent Reconciliation:** Rather than naive averaging, a supervisor agent:
   - Identifies disagreements among individual forecasts by examining reasoning traces
   - Executes targeted searches (up to N queries) to resolve ambiguities
   - Outputs confidence levels ("high," "medium," "low") for proposed updates
   - High-confidence updates replace simple means; low-confidence updates are discarded

   The paper states: "an agentic supervisor substantially outperforms both alternatives...substantially outperforms the best of k method."

3. **Platt Scaling Calibration:** A fixed extremization coefficient α=√3 ≈ 1.73 corrects the hedging bias inherent in LLM probability estimates (see Section 1.5).

**Benchmark Results (Brier Scores):**

| Benchmark | AIA Forecaster | Superforecasters | Prediction Market |
|-----------|----------------|------------------|-------------------|
| FB-7-21 (498 questions) | **0.1076** | 0.1110 | — |
| FB-8-14 (602 questions) | **0.1099** | 0.1152 | — |
| FB-Market (76 questions) | **0.0753** | 0.0740 | 0.0965 |
| MarketLiquid (1610 questions) | **0.1258** | — | 0.1106 |

**Key finding for our system:** Agentic search with best configuration achieved Brier score 0.1140 vs 0.1230 for no-search baseline. Access to prediction market prices alone closed ~42% of the performance gap between no-search and agentic search.

**Applicability to polymarket-agent:** The full multi-agent search architecture is computationally expensive and likely overkill for our use case. However, two components are directly applicable:
- **Platt scaling** (zero implementation cost — apply post-hoc to any LLM output)
- **Structured prompt with reasoning trace** (moderate cost — modify the existing prompt template)

### 1.3 Halawi et al. Scratchpad Prompting

**Paper:** "Approaching Human-Level Forecasting with Language Models" — NeurIPS 2024 / ICLR 2025 [2]

This paper introduces **scratchpad prompting** — a structured reasoning template that outperforms zero-shot probability estimation. The system achieves Brier score 0.179 vs human crowd 0.149 (gap of 0.03), and **beats the crowd in selective settings** where retrieval is sufficient.

**The Four-Component Scratchpad:**

1. **Question Comprehension:** Prompt the model to rephrase the question in its own words and expand with background knowledge. This ensures the model has correctly parsed resolution criteria.

2. **Argument Generation:** The model "leverage[s] the retrieved information and its pre-training knowledge to produce arguments for why the outcome may or may not occur." Both sides are explicitly elicited.

3. **Weighting Considerations:** Model is "instructed to weigh them by importance and aggregate them accordingly into an initial forecast." This forces explicit prioritization rather than implicit averaging.

4. **Calibration Check:** Model is "asked to check if it is over- or under-confident and consider historical base rates." This step alone addresses one of the biggest failure modes — ignoring base rates.

The authors note: "Accurately predicting the future is a difficult task that often requires computation beyond a single forward pass, and having the model externalize its reasoning allows them to understand the explanation for the forecast and improve it accordingly."

**Retrieval Augmentation Details:**
- Sources: NewsCatcher and Google News APIs
- Two-pronged query generation: (1) direct expansion from question, (2) decomposition into sub-questions covering indirect factors
- Relevance filtering via GPT-3.5-Turbo on title + first 250 words (70% cost savings over full-text)
- Optimal retrieval: k=15 articles; system outperforms crowd "when there are at least 5 relevant articles"

**Ensemble Composition:**
- 6 forecasts total: 3 from GPT-4-1106-Preview with different scratchpad prompts + 3 from fine-tuned GPT-4-0613 (temperature T=0.5)
- Aggregation: trimmed mean (outperformed median, arithmetic mean, geometric mean, and universal self-consistency)

**Self-Supervised Fine-Tuning:**
- Collected 73,632 candidate reasoning-prediction pairs
- Selected 13,253 where the model beat crowd baseline
- Trained on 6,000 most recent samples for 2 epochs
- Selection criterion: "only keep outputs that give a lower Brier score than the crowd's" while capping deviation at ±0.15

**Applicability to polymarket-agent:** The scratchpad prompt structure is the highest-value, lowest-cost improvement available. Our current prompt asks for "ONLY a single decimal number" — replacing this with a structured scratchpad would force the model to reason through base rates, arguments for/against, and self-calibration before committing to a probability.

### 1.4 LLM Ensemble Methods (Wisdom of Silicon Crowd)

**Paper:** "Wisdom of the Silicon Crowd: LLM Ensemble Prediction Capabilities Rival Human Crowd Accuracy" — Science Advances [3]

This study demonstrates that aggregating forecasts from 12 diverse LLMs produces predictions statistically equivalent to human crowd wisdom.

**Models Used (12 Diverse Models):**
- Frontier proprietary: GPT-4, GPT-4 with Bing, Claude 2
- Open-source: Llama-2-70B, Mistral-7B-Instruct, Solar-0-70B, Qwen-7B-Chat
- Other: PaLM 2, Bard, Falcon-180B, Coral (Command), GPT-3.5-Turbo-Instruct
- Models varied by company, parameter count (7B to 1.6T), internet access, and licensing

**Key Results:**
- **Ensemble Brier score: 0.20** vs human crowd **0.19** — no significant difference (p=0.850)
- GPT-4 led individual models at 0.15; weakest model (Coral) at 0.38
- 9 of 12 individual models underperformed the aggregate numerically
- Models showed overconfidence and acquiescence bias (favoring outcomes above 50% despite only 45% positive resolution rate)
- Round-number preference: 38 forecasts of exactly 50%, zero for 49% or 51%

**Practical Ensemble Finding:** LLM predictions (GPT-4, Claude 2) improve when exposed to the median human prediction, increasing accuracy by 17–28%. Simply averaging human and machine forecasts yields more accurate results than either alone.

**Cost:** Approximately $1 per forecast for the full 12-model ensemble, vs thousands of dollars for human forecaster tournaments.

**Limitation:** Study used only 31 questions — limited sample. Monitor for model refusals on controversial topics that reduce diversity.

**Applicability to polymarket-agent:** A full 12-model ensemble is expensive but even a 3-model ensemble (e.g., the existing Qwen local model + Claude via API + one other) with median aggregation would reduce variance. The key insight is that model diversity (different architectures, training data, sizes) matters more than model quality.

### 1.5 Extremization Calibration (Platt Scaling)

LLMs exhibit a consistent "hedging" tendency — they avoid extreme probabilities even when evidence warrants them. They are "fundamentally miscalibrated for probabilistic prediction under uncertainty" [1].

**The Fix — Platt Scaling with α=√3:**

The AIA Forecaster applies log-odds extremization with a fixed coefficient d=√3 ≈ 1.73:

```
log(p̂ / (1 - p̂)) = (d/n) × Σᵢ₌₁ⁿ log(pᵢ / (1 - pᵢ))
```

where p̂ is the calibrated probability and pᵢ are individual model forecasts. The final probability recovers via sigmoid. The paper establishes: "Platt scaling is Generalized Log Odds Extremization" (Appendix G.2).

**Effect:** Pushes low probabilities toward 0 and high probabilities toward 1. The largest Brier score improvements come from the 0.6–0.8 and 0.2–0.4 forecast bins — exactly where LLM hedging is most pronounced.

**Concrete example:**
- LLM outputs p=0.65 → calibrated: ~0.74
- LLM outputs p=0.80 → calibrated: ~0.89
- LLM outputs p=0.50 → calibrated: 0.50 (unchanged — symmetric)

**Implementation:**
```python
import math

def extremize(p: float, alpha: float = math.sqrt(3)) -> float:
    """Apply Platt scaling / log-odds extremization."""
    if p <= 0.0 or p >= 1.0:
        return p
    log_odds = math.log(p / (1 - p))
    calibrated_log_odds = alpha * log_odds
    return 1.0 / (1.0 + math.exp(-calibrated_log_odds))
```

**Supporting evidence:**
- Claude Sonnet models are "extremely underconfident" and predict 50% probability when the actual likelihood is 80% [5]
- Outcome-based RL with verifiable rewards achieves ECE (Expected Calibration Error) of ~0.042 [11]
- Current performance trajectory: LLMs improve by ~0.016 difficulty-adjusted Brier points annually; projected to match superforecasters on ForecastBench by November 2026 (95% CI: Dec 2025 – Jan 2028) [5]

**Applicability to polymarket-agent:** This is the single highest-ROI change. It requires adding ~5 lines of code after the LLM probability parse in `_evaluate()`. No prompt changes, no additional API calls, no infrastructure.

### 1.6 Structured Reasoning Decomposition

Multiple papers converge on the same structured reasoning pattern for LLM forecasting:

**The Pattern (synthesized from Halawi et al., AIA Forecaster, ksadov):**

1. **Question rephrasing** — force the model to articulate what "resolves Yes" means in concrete terms
2. **Evidence gathering** — present retrieved news, technical data, and market context
3. **Argument generation** — explicitly elicit arguments for Yes AND for No
4. **Weighting** — force the model to rank arguments by importance
5. **Initial estimate** — produce a first probability
6. **Calibration check** — ask: "Consider historical base rates for similar events. Are you over- or under-confident?"
7. **Final estimate** — allow revision after calibration reflection

**Chain-of-Thought (CoT) Considerations:**
- CoT benefits are particularly pronounced in large models (>100B parameters) [8]
- Smaller models "wrote illogical chains of thought, which led to worse accuracy than standard prompting"
- The current Qwen 35B model is borderline — CoT may or may not help; testing is required
- If using a frontier model (Claude, GPT-4), CoT reliably improves forecasting accuracy

**Contrast with current implementation:** Our prompt currently ends with "Respond with ONLY a single decimal number between 0.0 and 1.0." This explicitly suppresses reasoning. The fix is to restructure the prompt to require scratchpad reasoning, then parse the final probability from the structured output.

### 1.7 Multi-Model Ensemble Voting

As an alternative to single-model estimation, ensemble voting uses multiple models or multiple prompt variations to produce a distribution of estimates, then aggregates them.

**Approaches ranked by complexity:**

1. **Temperature sampling** (cheapest): Run the same model N times with temperature >0, take the median. This captures the model's own uncertainty distribution.

2. **Prompt variation** (moderate): Use 3 different scratchpad prompts with the same model, take trimmed mean. Halawi et al. found this effective with just 3 prompts.

3. **Multi-model median** (expensive): Query 3+ different model architectures, take median. Schoenegger et al. showed median aggregation of 12 models matches human crowd accuracy.

4. **Weighted ensemble** (requires history): Weight each model/prompt by its historical accuracy on resolved markets. Requires tracking per-model Brier scores over time.

**Practical recommendation for polymarket-agent:** Start with approach (1) — run the existing model 3 times with temperature=0.3, take the median. This costs 3× in API calls but requires zero infrastructure changes. The `max_calls_per_hour` limit (currently 5000) is generous enough to accommodate 3× calls.

---

## 2. TechnicalAnalyst Domain — Better Indicators & Adaptive Methods

### 2.1 Current Implementation Gaps

The TechnicalAnalyst (`src/polymarket_agent/strategies/technical_analyst.py`) and its indicator library (`indicators.py`) have the following gaps:

| Gap | Current Behavior | Research-Based Alternative |
|-----|------------------|---------------------------|
| **No MACD** | Only EMA crossover (8/21) for trend | MACD (12/26/9) provides trend + momentum in one indicator |
| **No adaptive thresholds** | RSI overbought/oversold fixed at 70/30 | ATR-based or ADX-based threshold adjustment for volatility regimes |
| **No regime detection** | Same strategy in trending and ranging markets | ADX-based trend strength → strategy selection (trend-following vs mean-reversion) |
| **No multi-timeframe** | Single timeframe (1w at 60-min bars) | Short + medium + long confluence for signal confirmation |
| **No divergence detection** | Price/indicator agreement assumed | RSI/price and MACD/price divergence are high-conviction reversal signals |
| **Stochastic RSI unused** | Computed in `indicators.py` but ignored in signal logic | StochRSI provides overbought/oversold timing within RSI trends |

### 2.2 MACD Integration

MACD (Moving Average Convergence Divergence) consists of three components:
- **MACD line:** 12-period EMA minus 26-period EMA
- **Signal line:** 9-period EMA of MACD line
- **Histogram:** MACD line minus signal line (represents momentum acceleration/deceleration)

**Why add MACD when we already have EMA crossover?**
The existing EMA 8/21 crossover only captures trend direction. MACD adds:
- **Momentum measurement** via the histogram (rate of trend change)
- **Signal line crossovers** for entry/exit timing
- **Zero-line crossovers** for trend confirmation
- **Divergence signals** (see Section 2.3)

**MACD+VWAP combination:** MACD crossovers occurring above or below VWAP (or a proxy like the 20-period SMA) provide stronger trend signals — bullish when price is above the average and MACD shows bullish crossover [12].

**Prediction market adaptation:** Standard MACD uses 12/26/9 periods calibrated for daily equity data. For prediction markets with 60-minute bars over 1 week (~168 bars), consider shortened periods (e.g., 6/13/5) to maintain responsiveness. The existing EMA 8/21 choice was explicitly made for prediction market lifespans — apply the same reasoning to MACD.

### 2.3 RSI/MACD Divergence Detection

Divergence occurs when price and an indicator move in opposite directions — a reliable signal of impending trend reversal.

**Types:**
- **Bullish divergence:** Price makes lower low, but RSI/MACD makes higher low → selling pressure weakening
- **Bearish divergence:** Price makes higher high, but RSI/MACD makes lower high → buying pressure weakening

**Implementation approach:**
1. Identify swing highs/lows in both price series and indicator series over a lookback window
2. Compare the most recent two swing points in each
3. Flag divergence when the directions disagree

RSI and MACD divergences appearing simultaneously are "particularly strong signal[s]" [12]. Backtest data shows a combined MACD+RSI strategy achieving **73% win rate over 235 trades** with average gain of 0.88% per trade including commissions and slippage [13].

**Integration with existing signal logic:** Divergence should act as a **confirmation** signal rather than a standalone trigger. When the EMA crossover generates a buy signal AND bullish RSI divergence is present, confidence should increase. When divergence contradicts the EMA signal, confidence should decrease or the signal should be vetoed.

### 2.4 Adaptive Thresholds via Volatility Regime Detection

The current TechnicalAnalyst uses fixed RSI thresholds (70/30) regardless of market conditions. Research shows that adaptive thresholds based on volatility significantly improve signal quality [14].

**ATR-Based Approach:**
- Compute Average True Range (ATR) over 14 periods as a volatility measure
- Note: prediction market data lacks OHLC, so ATR must be approximated using inter-bar price differences: `TR ≈ abs(close[i] - close[i-1])`
- Classify volatility: low (ATR < 20th percentile of historical), normal, high (ATR > 80th percentile)
- Adjust RSI thresholds:
  - Low volatility: tighten to 65/35 (smaller moves are meaningful)
  - Normal: keep 70/30
  - High volatility: relax to 75/25 (larger moves needed for significance)

**ADX-Based Approach (for trend strength):**
- ADX (Average Directional Index) measures trend strength regardless of direction
- ADX > 25: strong trend → favor trend-following signals (EMA, MACD)
- ADX < 20: weak trend / ranging → favor mean-reversion signals (RSI extremes, Bollinger bounces)
- Note: ADX requires +DI and -DI components which need OHLC data; approximation with close-only data is possible but less reliable

**Practical recommendation:** Start with ATR-based volatility classification since it works with close-only data. Use it to:
1. Adjust RSI thresholds
2. Scale confidence scores (reduce confidence in high-volatility environments where signals are noisier)
3. Adjust the EMA crossover buffer (currently fixed at 0.5%)

### 2.5 Multi-Timeframe Analysis

The current system fetches 1 week of 60-minute bars — a single timeframe. Multi-timeframe analysis looks for confluence across different time horizons.

**Three-Timeframe Framework:**
- **Short-term (4h):** 60-minute bars, last 4 hours — captures intraday momentum and immediate entry timing
- **Medium-term (1w):** 60-minute bars, last week — the current default; captures the intermediate trend
- **Long-term (1m):** 4-hour or daily bars, last month — captures the macro trend and major support/resistance

**Signal confluence logic:**
- All three timeframes bullish → high confidence buy
- Two of three bullish → moderate confidence buy
- Mixed signals → reduce confidence or no signal
- All three bearish → high confidence sell

**Implementation consideration:** This requires fetching data at multiple `interval` / `fidelity` settings from the price API. Each additional timeframe adds one API call per market evaluation. Given the `_MIN_DATA_POINTS = 21` requirement, ensure each timeframe has sufficient history.

### 2.6 Market Regime Classification

Different market regimes call for different strategies. A trending market rewards trend-following (EMA crossover, MACD), while a ranging market rewards mean-reversion (RSI extremes, Bollinger bands).

**Regime Detection Methods:**

1. **ADX-Based (preferred if OHLC available):**
   - ADX > 25: trending regime
   - ADX < 20: ranging regime
   - 20–25: transitional

2. **EMA Slope-Based (works with close-only data):**
   - Calculate slope of the 21-period EMA over the last N bars
   - Steep positive/negative slope: trending
   - Flat slope (|slope| < threshold): ranging

3. **Bollinger Bandwidth-Based (already partially implemented):**
   - Expanding bandwidth: trending regime (breakout)
   - Contracting bandwidth: ranging regime (consolidation)
   - The existing squeeze detection is a partial implementation of this

**Strategy Selection by Regime:**
- **Trending:** Weight EMA/MACD signals higher, reduce RSI mean-reversion weight, enable trailing stops
- **Ranging:** Weight RSI/Bollinger signals higher, reduce EMA/MACD weight, use fixed take-profit/stop-loss
- **Transitional:** Equal weights, require higher confidence threshold before signaling

**Integration with existing confidence scoring:**
```python
# Current: fixed weights
confidence = ema_score * 0.4 + rsi_score * 0.3 + squeeze_score * 0.3

# Proposed: regime-adaptive weights
if regime == "trending":
    confidence = ema_score * 0.5 + rsi_score * 0.15 + squeeze_score * 0.15 + macd_score * 0.2
elif regime == "ranging":
    confidence = ema_score * 0.15 + rsi_score * 0.45 + squeeze_score * 0.25 + macd_score * 0.15
else:
    confidence = ema_score * 0.3 + rsi_score * 0.25 + squeeze_score * 0.2 + macd_score * 0.25
```

---

## 3. Cross-Cutting Improvements

### 3.1 Fractional Kelly Position Sizing

**Current state:** The `PositionSizer` class in `position_sizing.py` already implements both full Kelly and fractional Kelly (quarter-Kelly with fraction=0.25). However, the live config uses `method: fixed`, bypassing Kelly entirely.

**The Kelly Formula (as implemented):**
```python
b = (1.0 / price) - 1.0    # decimal odds from market price
q = 1.0 - confidence        # probability of loss
f = (b * confidence - q) / b  # Kelly fraction
```

**Why it's disabled (and how to fix it):** Kelly requires the `confidence` input to be a calibrated win probability. Currently:
- AIAnalyst confidence = `min(abs(divergence) / 0.3, 1.0)` — a divergence magnitude, not a probability
- TechnicalAnalyst confidence = weighted indicator score (0.4 × EMA + 0.3 × RSI + 0.3 × squeeze) — also not a probability

Neither is a valid Kelly input. Feeding uncalibrated confidence into Kelly produces wildly incorrect position sizes.

**Fix path:**
1. Apply Platt scaling to AIAnalyst output → now it's a calibrated probability
2. Track historical accuracy of signals by confidence bucket → build an empirical mapping from confidence score to actual win rate
3. Use the calibrated win rate as Kelly's `p` input
4. Enable fractional Kelly (quarter-Kelly) as a conservative starting point
5. Cap at `max_bet_pct = 0.10` (already implemented)

**Reference:** The ksadov bot uses Kelly criterion with a similar approach: "just run a regular Python function to calculate" the position size once the probability is estimated [9]. Multiple open-source bots (e.g., polymarket-kalshi-weather-bot) also use Kelly-based sizing.

### 3.2 Signal Confidence Calibration

The current linear confidence mapping (`min(abs(divergence) / 0.3, 1.0)`) should be replaced with a sigmoid function that better models the diminishing returns of extreme divergence.

**Sigmoid mapping:**
```python
import math

def sigmoid_confidence(divergence: float, midpoint: float = 0.15, steepness: float = 20.0) -> float:
    """Map divergence to confidence via sigmoid."""
    return 1.0 / (1.0 + math.exp(-steepness * (abs(divergence) - midpoint)))
```

**Why sigmoid?**
- Small divergences (0–5%) should map to near-zero confidence (noise)
- Medium divergences (10–20%) should map to moderate confidence (signal)
- Large divergences (25%+) should saturate near 1.0 (diminishing marginal information)
- The current linear mapping over-weights small divergences and under-weights medium ones

**Calibration via backtesting:** Once historical signal data accumulates, fit the sigmoid parameters (midpoint, steepness) by minimizing the gap between predicted confidence and observed win rate across confidence buckets.

### 3.3 Trailing Stops

The current exit manager uses fixed take-profit (25%) and stop-loss thresholds. In trending markets, a trailing stop captures more upside by letting winners run while protecting profits.

**Trailing Stop Logic:**
- After entry, track the highest favorable price reached (peak unrealized profit)
- Set the stop at `peak_price - trail_distance`
- `trail_distance` can be ATR-based (e.g., 2 × ATR) or percentage-based (e.g., 10% from peak)
- When price hits the trailing stop, exit the position

**When to use trailing stops vs fixed exits:**
- Trending regime (detected per Section 2.6): use trailing stops
- Ranging regime: keep fixed take-profit/stop-loss
- This prevents the current 24-hour max hold from cutting trending winners short

### 3.4 Ensemble Signal Aggregation

The current aggregator (`aggregator.py`) uses a consensus filter + winner-take-all confidence selection. Improvements:

**Current behavior:**
```python
# Groups by (market_id, token_id, side)
# Requires min_strategies=2 (both AI and TA must agree)
# Takes the HIGHEST confidence signal from the agreeing group
```

**Problems:**
1. No blending — a 0.90 AI confidence and 0.41 TA confidence produces 0.90, ignoring the TA's lukewarm agreement
2. No historical performance weighting — both strategies weighted equally regardless of track record
3. No conflict resolution — if AI says buy and TA says sell on the same market, both can proceed independently

**Proposed improvements:**

1. **Confidence blending:** When strategies agree on direction, blend confidence scores:
   ```python
   blended = sum(w_i * c_i for w_i, c_i in zip(weights, confidences))
   ```
   where weights reflect historical strategy accuracy.

2. **Performance-weighted voting:** Track each strategy's realized Brier score or win rate over the last N resolved signals. Weight that strategy's contribution to the ensemble accordingly.

3. **Conflict resolution:** When strategies disagree on the same market:
   - If both high confidence → no signal (genuine uncertainty)
   - If one high and one low → follow the high-confidence strategy (weighted by historical accuracy)
   - Log conflicts for analysis

---

## 4. References

[1] "AIA Forecaster: Technical Report." arXiv:2511.07678. https://arxiv.org/abs/2511.07678

[2] Halawi, D. et al. "Approaching Human-Level Forecasting with Language Models." NeurIPS 2024 / ICLR 2025. https://arxiv.org/abs/2402.18563

[3] Schoenegger, P. et al. "Wisdom of the Silicon Crowd: LLM Ensemble Prediction Capabilities Rival Human Crowd Accuracy." Science Advances. https://www.science.org/doi/10.1126/sciadv.adp1528 — Also: https://arxiv.org/abs/2402.19379

[4] "AI-Augmented Predictions: LLM Assistants Improve Human Forecasting Accuracy." ACM TIIS. https://arxiv.org/html/2402.07862v2

[5] "ForecastBench: A Dynamic Benchmark of AI Forecasting Capabilities." https://arxiv.org/html/2409.19839v5 — Also: LLM forecasting trajectory analysis at https://forecastingresearch.substack.com/p/ai-llm-forecasting-model-forecastbench-benchmark

[6] Lu, J. et al. "Evaluating LLMs on Real-World Forecasting Against Expert Forecasters." https://arxiv.org/html/2507.04562v3

[7] "Epistemic Calibration via Prediction Markets." https://arxiv.org/html/2512.16030v1

[8] "Prompt Engineering Large Language Models' Forecasting Capabilities." https://arxiv.org/pdf/2506.01578

[9] ksadov. "AI agents for prediction market trading." March 2025. https://www.ksadov.com/posts/2025-03-26-tradebot.html

[10] "Going All-In on LLM Accuracy: Fake Prediction Markets, Real Confidence Signals." https://www.arxiv.org/pdf/2512.05998

[11] "LLMs Can Teach Themselves to Better Predict the Future." https://arxiv.org/pdf/2502.05253

[12] Combined MACD+RSI trading strategies. https://wundertrading.com/journal/en/learn/article/combine-macd-and-rsi

[13] "MACD and RSI Strategy" (73% win rate backtest). https://www.quantifiedstrategies.com/macd-and-rsi-strategy/

[14] "High-Frequency RSI-MACD-EMA Composite Technical Analysis Strategy with Adaptive Stop-Loss." https://medium.com/@FMZQuant/high-frequency-rsi-macd-ema-composite-technical-analysis-strategy-with-adaptive-stop-loss-76cbf3be25c8

[15] "Multi-Indicator Divergence Trading Strategy with Adaptive Take-Profit and Stop-Loss." https://medium.com/@redsword_23261/multi-indicator-divergence-trading-strategy-with-adaptive-take-profit-and-stop-loss-73c214d2123e

[16] "TradingAgents: Multi-Agents LLM Financial Trading Framework." https://arxiv.org/html/2412.20138v3

[17] "Two-Stage Framework for Stock Price Prediction: LLM + PPO." https://www.scirp.org/journal/paperinformation?paperid=142270

[18] "Deep Reinforcement Learning with Behavioral Biases for Trading." Nature Scientific Reports. https://www.nature.com/articles/s41598-026-35902-x
