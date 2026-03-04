"""AIAnalyst strategy — uses an LLM to estimate market probabilities.

Supports both Anthropic and OpenAI-compatible providers. Set ``provider``
to ``"openai"`` in the strategy config (or via ``configure()``) to use any
OpenAI-compatible endpoint, including local models served via Ollama/vLLM.

The prompt is enriched with optional technical analysis (from price history),
recent news headlines, volatility anomaly detection, and sentiment analysis.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from polymarket_agent.data.models import Market, VolatilityReport
from polymarket_agent.news.sentiment import (
    KeywordTracker,
    format_keyword_spikes,
    format_sentiment_summary,
    score_sentiment,
)
from polymarket_agent.strategies.base import Signal, Strategy
from polymarket_agent.strategies.debate import run_debate
from polymarket_agent.strategies.indicators import TechnicalContext, analyze_market_technicals
from polymarket_agent.strategies.volatility import compute_volatility_report, format_volatility_summary

if TYPE_CHECKING:
    from polymarket_agent.data.models import KeywordSpike, SentimentScore
    from polymarket_agent.data.provider import DataProvider
    from polymarket_agent.news.models import NewsItem
    from polymarket_agent.news.provider import NewsProvider

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER: str = "anthropic"
_DEFAULT_MODEL: str = "claude-sonnet-4-6"
_DEFAULT_MAX_CALLS_PER_HOUR: int = 20
_DEFAULT_MIN_DIVERGENCE: float = 0.15
_DEFAULT_ORDER_SIZE: float = 25.0
_DEFAULT_MIN_PRICE: float = 0.05
_DEFAULT_NEWS_MAX_RESULTS: int = 5

_MAX_QUESTION_LEN: int = 500
_MAX_DESCRIPTION_LEN: int = 1000
_MAX_NEWS_TITLE_LEN: int = 200
_MAX_NEWS_PUBLISHED_LEN: int = 40

_DEFAULT_API_KEY_ENVS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}
_SUPPORTED_PROVIDERS: set[str] = {"anthropic", "openai"}


def _sanitize_text(text: str, max_len: int) -> str:
    """Truncate and strip control characters from external text."""
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch in ("\n", " "))
    return cleaned[:max_len]


class AIAnalyst(Strategy):
    """Ask an LLM for probability estimates and trade on divergence.

    Sends market question + description to the configured LLM provider,
    parses a probability from the response. If the estimate diverges from
    the market price by more than ``min_divergence``, a buy or sell signal
    is generated.

    Supported providers:
    - ``"anthropic"`` (default) — requires the ``anthropic`` package
    - ``"openai"`` — requires the ``openai`` package; works with any
      OpenAI-compatible endpoint via ``base_url``

    Gracefully degrades when the required API key is not set.
    """

    name: str = "ai_analyst"

    def __init__(self) -> None:
        self._provider: str = _DEFAULT_PROVIDER
        self._model: str = _DEFAULT_MODEL
        self._base_url: str | None = None
        self._api_key_env: str | None = None
        self._max_calls_per_hour: int = _DEFAULT_MAX_CALLS_PER_HOUR
        self._min_divergence: float = _DEFAULT_MIN_DIVERGENCE
        self._order_size: float = _DEFAULT_ORDER_SIZE
        self._min_price: float = _DEFAULT_MIN_PRICE
        self._news_max_results: int = _DEFAULT_NEWS_MAX_RESULTS
        self._extra_params: dict[str, Any] = {}
        self._call_timestamps: list[float] = []
        self._client: Any = None
        self._news_provider: NewsProvider | None = None
        self._sentiment_enabled: bool = False
        self._keyword_spike_enabled: bool = False
        self._volatility_enabled: bool = True
        self._structured_prompt: bool = False
        self._platt_scaling: bool = True
        self._sigmoid_confidence: bool = True
        self._debate_mode: bool = False
        self._reflection_enabled: bool = False
        self._reflection_engine: Any = None  # ReflectionEngine, set externally
        self._keyword_tracker: KeywordTracker = KeywordTracker()
        self._init_client()

    def _resolved_api_key_env(self) -> str:
        """Return the env var name to use for the API key."""
        if self._api_key_env:
            return self._api_key_env
        return _DEFAULT_API_KEY_ENVS.get(self._provider, "OPENAI_API_KEY")

    def _init_client(self) -> None:
        if self._provider == "anthropic":
            self._init_anthropic_client()
        elif self._provider == "openai":
            self._init_openai_client()
        else:
            logger.warning("Unknown AIAnalyst provider %r; falling back to %s", self._provider, _DEFAULT_PROVIDER)
            self._provider = _DEFAULT_PROVIDER
            self._init_anthropic_client()

    def _init_anthropic_client(self) -> None:
        env_var = self._resolved_api_key_env()
        api_key = os.environ.get(env_var)
        if not api_key:
            logger.info("%s not set — AIAnalyst disabled", env_var)
            return
        try:
            import anthropic  # noqa: PLC0415

            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — AIAnalyst disabled")

    def _init_openai_client(self) -> None:
        env_var = self._resolved_api_key_env()
        api_key = os.environ.get(env_var)
        if not api_key:
            logger.info("%s not set — AIAnalyst disabled", env_var)
            return
        try:
            import openai  # noqa: PLC0415

            kwargs: dict[str, Any] = {"api_key": api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
        except ImportError:
            logger.warning("openai package not installed — AIAnalyst disabled")

    def configure(self, config: dict[str, Any]) -> None:
        self._model = config.get("model", _DEFAULT_MODEL)
        self._max_calls_per_hour = int(config.get("max_calls_per_hour", _DEFAULT_MAX_CALLS_PER_HOUR))
        self._min_divergence = float(config.get("min_divergence", _DEFAULT_MIN_DIVERGENCE))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))
        self._min_price = float(config.get("min_price", _DEFAULT_MIN_PRICE))
        self._news_max_results = max(1, int(config.get("news_max_results", self._news_max_results)))
        self._extra_params = dict(config.get("extra_params", self._extra_params))
        self._sentiment_enabled = bool(config.get("sentiment_enabled", self._sentiment_enabled))
        self._keyword_spike_enabled = bool(config.get("keyword_spike_enabled", self._keyword_spike_enabled))
        self._volatility_enabled = bool(config.get("volatility_enabled", self._volatility_enabled))
        self._structured_prompt = bool(config.get("structured_prompt", self._structured_prompt))
        self._platt_scaling = bool(config.get("platt_scaling", self._platt_scaling))
        self._sigmoid_confidence = bool(config.get("sigmoid_confidence", self._sigmoid_confidence))
        self._debate_mode = bool(config.get("debate_mode", self._debate_mode))
        self._reflection_enabled = bool(config.get("reflection_enabled", self._reflection_enabled))

        raw_provider = config.get("provider", self._provider)
        new_provider = str(raw_provider).strip().lower() if raw_provider is not None else self._provider
        if new_provider not in _SUPPORTED_PROVIDERS:
            logger.warning("Unknown AIAnalyst provider %r; using %s", raw_provider, _DEFAULT_PROVIDER)
            new_provider = _DEFAULT_PROVIDER
        new_base_url = config.get("base_url", self._base_url)
        new_api_key_env = config.get("api_key_env", self._api_key_env)

        needs_reinit = (
            new_provider != self._provider or new_base_url != self._base_url or new_api_key_env != self._api_key_env
        )
        self._provider = new_provider
        self._base_url = str(new_base_url) if new_base_url else None
        self._api_key_env = str(new_api_key_env) if new_api_key_env else None

        if needs_reinit:
            self._client = None
            self._init_client()

    def set_news_provider(self, provider: NewsProvider, *, max_results: int | None = None) -> None:
        """Attach a news provider for prompt enrichment."""
        self._news_provider = provider
        if max_results is not None:
            self._news_max_results = max(1, int(max_results))

    def set_reflection_engine(self, engine: Any) -> None:
        """Attach a ReflectionEngine for injecting past lessons into prompts."""
        self._reflection_engine = engine

    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        if self._client is None:
            return []

        signals: list[Signal] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            if not self._can_call():
                break
            if (signal := self._evaluate(market, data, all_markets=markets)) is not None:
                signals.append(signal)
        return signals

    def _can_call(self) -> bool:
        now = time.monotonic()
        cutoff = now - 3600.0
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        return len(self._call_timestamps) < self._max_calls_per_hour

    def _call_llm(self, prompt: str) -> str:
        """Send a prompt to the configured LLM and return the text response."""
        if self._provider == "anthropic":
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            return str(response.content[0].text).strip()

        # OpenAI-compatible provider
        max_tokens = 1024 if self._structured_prompt else 128
        system_content = (
            "You are a prediction market analyst. Follow the user's instructions exactly."
            if self._structured_prompt
            else "Respond with ONLY a single decimal number between 0.0 and 1.0. No other text."
        )
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ],
        }

        if self._extra_params:
            kwargs["extra_body"] = self._extra_params

        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        return content.strip() if content else ""

    def _evaluate_with_debate(
        self,
        question: str,
        description: str,
        current_price: float,
        context_prompt: str,
    ) -> float | None:
        """Run adversarial debate and return the judge's probability estimate.

        Extracts context sections from the already-built prompt and passes them
        to the debate module. Returns None if the debate fails.
        """
        # Extract context sections (everything between BEGIN/END MARKET DATA markers)
        context_sections = ""
        start_idx = context_prompt.find("--- TECHNICAL ANALYSIS ---")
        if start_idx == -1:
            start_idx = context_prompt.find("--- VOLATILITY ANALYSIS ---")
        if start_idx == -1:
            start_idx = context_prompt.find("--- RECENT NEWS ---")
        end_idx = context_prompt.find("--- END MARKET DATA ---")
        if start_idx != -1 and end_idx != -1:
            context_sections = context_prompt[start_idx:end_idx]

        result = run_debate(
            market_question=question,
            market_description=description,
            current_price=current_price,
            call_llm=self._call_llm,
            context_sections=context_sections,
        )
        if result is None:
            return None

        # Count 3 LLM calls for rate limiting
        self._call_timestamps.extend([time.monotonic()] * 2)

        logger.info(
            "Debate: bull=%.3f bear=%.3f judge=%.3f for %s",
            result.bull_probability,
            result.bear_probability,
            result.judge_probability,
            question[:60],
        )
        return result.judge_probability

    @staticmethod
    def _extremize(p: float, alpha: float = math.sqrt(3)) -> float:
        """Apply Platt scaling to correct LLM hedging bias.

        Shifts moderate probabilities toward 0 or 1 using log-odds rescaling.
        Default α=√3 per AIA Forecaster paper: 0.6→0.74, 0.8→0.89.
        """
        if p <= 0.0 or p >= 1.0:
            return p
        log_odds = math.log(p / (1.0 - p))
        return 1.0 / (1.0 + math.exp(-alpha * log_odds))

    # ------------------------------------------------------------------
    # Technical analysis context
    # ------------------------------------------------------------------

    def _fetch_technical_context(self, token_id: str, data: DataProvider) -> TechnicalContext | None:
        """Best-effort fetch of technical indicators for a token."""
        try:
            history = data.get_price_history(token_id, interval="1w", fidelity=60)
            return analyze_market_technicals(history, token_id)
        except Exception:
            logger.debug("Failed to fetch TA context for %s", token_id)
            return None

    @staticmethod
    def _format_technical_summary(ctx: TechnicalContext) -> str:
        """Format technical context into a human-readable prompt section."""
        direction_arrow = {"up": "up", "down": "down", "neutral": "flat"}
        trend_label = direction_arrow.get(ctx.trend_direction, "flat")
        lines = [
            f"Price trend: {trend_label} ({ctx.price_start:.4f} -> {ctx.current_price:.4f}, {ctx.price_change_pct:+.1%})",
            f"EMA crossover: {ctx.ema_crossover} (fast={ctx.ema_fast.value:.4f}, slow={ctx.ema_slow.value:.4f})",
            f"RSI: {ctx.rsi.rsi:.1f} ({'overbought' if ctx.rsi.is_overbought else 'oversold' if ctx.rsi.is_oversold else 'neutral'})",
            f"Volatility: {'compressed (squeeze)' if ctx.squeeze.is_squeezing else 'expanding' if ctx.squeeze.squeeze_releasing else 'normal'}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # News context
    # ------------------------------------------------------------------

    def _fetch_news(self, query: str) -> list[NewsItem]:
        """Best-effort fetch of recent news headlines."""
        if self._news_provider is None:
            return []
        try:
            return self._news_provider.search(query[:100], max_results=self._news_max_results)
        except Exception:
            logger.debug("Failed to fetch news for query: %s", query[:50])
            return []

    @staticmethod
    def _format_news_summary(items: list[NewsItem]) -> str:
        """Format news items into a prompt section."""
        lines: list[str] = []
        for item in items[:5]:
            title = _sanitize_text(item.title, _MAX_NEWS_TITLE_LEN)
            published = _sanitize_text(item.published, _MAX_NEWS_PUBLISHED_LEN)
            date_part = f" ({published})" if published else ""
            lines.append(f"- {title}{date_part}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Volatility analysis context
    # ------------------------------------------------------------------

    def _fetch_volatility_report(self, token_id: str, data: DataProvider) -> VolatilityReport | None:
        """Best-effort fetch of volatility analysis for a token."""
        if not self._volatility_enabled:
            return None
        try:
            history = data.get_price_history(token_id, interval="1w", fidelity=60)
            return compute_volatility_report(history, token_id)
        except Exception:
            logger.debug("Failed to fetch volatility report for %s", token_id)
            return None

    # ------------------------------------------------------------------
    # Sentiment analysis context
    # ------------------------------------------------------------------

    def _score_news_sentiment(self, news_items: list[NewsItem], market_id: str) -> SentimentScore | None:
        """Score sentiment of news headlines using the configured LLM."""
        if not self._sentiment_enabled or not news_items or self._client is None:
            return None
        try:
            return score_sentiment(news_items, market_id, self._call_llm)
        except Exception:
            logger.debug("Failed to score sentiment for market %s", market_id)
            return None

    def _detect_keyword_spikes(self, question: str) -> list[KeywordSpike]:
        """Detect keyword spikes related to the market question."""
        if not self._keyword_spike_enabled:
            return []
        # Extract key terms from the question (words > 4 chars, skip common words)
        stop_words = {"will", "what", "when", "where", "which", "would", "could", "should", "about", "their", "there"}
        keywords = [w.lower() for w in question.split() if len(w) > 4 and w.lower() not in stop_words][:3]
        for keyword in keywords:
            self._keyword_tracker.fetch_and_record(keyword)
        return self._keyword_tracker.detect_spikes(keywords)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _find_sibling_brackets(market: Market, all_markets: list[Market]) -> list[Market]:
        """Find sibling bracket markets that share a common question prefix.

        Bracket events (e.g., "How many tweets... 100-149", "... 150-199")
        share long question prefixes. Returns siblings sorted by question text.
        """
        prefix_len = 30
        if len(market.question) < prefix_len:
            return []
        prefix = market.question[:prefix_len].lower()
        siblings = [
            m
            for m in all_markets
            if m.id != market.id and len(m.question) >= prefix_len and m.question[:prefix_len].lower() == prefix
        ]
        if not siblings:
            return []
        return sorted(siblings, key=lambda m: m.question)

    @staticmethod
    def _format_bracket_distribution(market: Market, siblings: list[Market]) -> str:
        """Format a bracket distribution table for the LLM prompt."""
        all_brackets = sorted([market, *siblings], key=lambda m: m.question)
        lines: list[str] = []
        for m in all_brackets:
            label = m.group_item_title or m.question[-30:]
            price = m.outcome_prices[0] if m.outcome_prices else 0.0
            marker = " <-- THIS MARKET" if m.id == market.id else ""
            lines.append(f"  {label}: {price:.2f} ({price:.0%}){marker}")
        total = sum(m.outcome_prices[0] for m in all_brackets if m.outcome_prices)
        lines.append(f"  Sum of probabilities: {total:.2f}")
        return "\n".join(lines)

    def _evaluate(
        self, market: Market, data: DataProvider, *, all_markets: list[Market] | None = None
    ) -> Signal | None:
        if not market.outcome_prices or not market.clob_token_ids:
            return None

        yes_price = market.outcome_prices[0]
        if yes_price < self._min_price or yes_price > (1.0 - self._min_price):
            return None

        token_id = market.clob_token_ids[0]

        # Build enriched prompt
        question = _sanitize_text(market.question, _MAX_QUESTION_LEN)
        prompt = (
            "You are a prediction market analyst. Estimate the probability (0.0 to 1.0) "
            "that the following question resolves to Yes.\n\n"
            "--- BEGIN MARKET DATA ---\n"
            f"Question: {question}\n"
        )
        if market.description:
            description = _sanitize_text(market.description, _MAX_DESCRIPTION_LEN)
            prompt += f"Description: {description}\n"

        # Bracket distribution context (optional)
        if all_markets:
            siblings = self._find_sibling_brackets(market, all_markets)
            if siblings:
                prompt += (
                    f"\n--- BRACKET DISTRIBUTION ---\n"
                    f"This market is one bracket in a multi-outcome event. "
                    f"All brackets and their current market prices:\n"
                    f"{self._format_bracket_distribution(market, siblings)}\n"
                )

        # Technical analysis section (optional)
        ta_ctx = self._fetch_technical_context(token_id, data)
        if ta_ctx is not None:
            prompt += f"\n--- TECHNICAL ANALYSIS ---\n{self._format_technical_summary(ta_ctx)}\n"

        # Volatility analysis section (optional)
        vol_report = self._fetch_volatility_report(token_id, data)
        if vol_report is not None:
            prompt += f"\n--- VOLATILITY ANALYSIS ---\n{format_volatility_summary(vol_report)}\n"

        # News section (optional)
        news_items = self._fetch_news(market.question)
        if news_items:
            prompt += f"\n--- RECENT NEWS ---\n{self._format_news_summary(news_items)}\n"

        # Sentiment analysis section (optional)
        if news_items:
            sentiment = self._score_news_sentiment(news_items, market.id)
            if sentiment is not None:
                prompt += f"\n--- SENTIMENT ANALYSIS ---\n{format_sentiment_summary(sentiment)}\n"

        # Keyword spike section (optional)
        keyword_spikes = self._detect_keyword_spikes(market.question)
        if keyword_spikes:
            prompt += f"\n--- KEYWORD SPIKES ---\n{format_keyword_spikes(keyword_spikes)}\n"

        # Past lessons from reflection memory (optional)
        if self._reflection_enabled and self._reflection_engine is not None:
            try:
                from polymarket_agent.strategies.reflection import ReflectionEngine  # noqa: PLC0415

                if isinstance(self._reflection_engine, ReflectionEngine):
                    lessons = self._reflection_engine.retrieve_relevant_lessons(market.question)
                    lessons_text = ReflectionEngine.format_lessons_for_prompt(lessons)
                    if lessons_text:
                        prompt += f"\n{lessons_text}\n"
            except Exception:
                logger.debug("Failed to retrieve lessons for prompt enrichment")

        prompt += f"--- END MARKET DATA ---\n\nCurrent market price: {yes_price:.2f}\n"

        if self._structured_prompt:
            prompt += (
                "Think step by step:\n"
                '1. COMPREHENSION: Rephrase what "resolves Yes" means in concrete terms.\n'
                "2. BASE RATE: What is the historical base rate for similar events?\n"
                "3. ARGUMENTS FOR YES: List 2-3 key reasons this resolves Yes.\n"
                "4. ARGUMENTS FOR NO: List 2-3 key reasons this resolves No.\n"
                "5. WEIGHTING: Which arguments are strongest and why?\n"
                "6. INITIAL ESTIMATE: Your first probability estimate (0.0-1.0).\n"
                "7. CALIBRATION CHECK: Are you over- or under-confident? Consider base rates.\n"
                "8. FINAL PROBABILITY: [number between 0.0 and 1.0]\n"
            )
        else:
            prompt += "Respond with ONLY a single decimal number between 0.0 and 1.0."

        try:
            if self._debate_mode:
                estimate = self._evaluate_with_debate(
                    question, market.description or "", yes_price, prompt
                )
                if estimate is None:
                    return None
            else:
                text = self._call_llm(prompt)

                if self._structured_prompt:
                    fp_match = re.search(r"FINAL\s+PROBABILITY\s*:\s*(0(?:\.\d+)?|1(?:\.0+)?)", text)
                    if fp_match:
                        matches = [fp_match.group(1)]
                    else:
                        matches = re.findall(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", text)
                else:
                    matches = re.findall(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", text)

                if not matches:
                    logger.warning("Could not parse probability from AI response: %s", text[:200])
                    return None
                estimate = float(matches[-1])

            if self._platt_scaling:
                estimate = self._extremize(estimate)
        except Exception:
            logger.exception("AI analyst call failed for market %s", market.id)
            return None
        finally:
            self._call_timestamps.append(time.monotonic())

        divergence = estimate - yes_price

        if abs(divergence) < self._min_divergence:
            return None

        # Positive divergence → AI thinks Yes is underpriced → buy Yes token
        # Negative divergence → AI thinks Yes is overpriced → sell Yes token
        side: Literal["buy", "sell"] = "buy" if divergence > 0 else "sell"

        # Sigmoid confidence: small divergences (<5%) → ~0, 15% → 0.5, 25%+ → ~1.0
        if self._sigmoid_confidence:
            confidence = 1.0 / (1.0 + math.exp(-20.0 * (abs(divergence) - 0.15)))
        else:
            confidence = min(abs(divergence) / 0.3, 1.0)

        return Signal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            side=side,
            confidence=round(confidence, 4),
            target_price=yes_price,
            size=self._order_size,
            reason=f"ai_estimate={estimate:.4f}, market={yes_price:.4f}, div={divergence:+.4f}",
        )
