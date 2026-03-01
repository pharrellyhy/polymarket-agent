"""AIAnalyst strategy — uses an LLM to estimate market probabilities.

Supports both Anthropic and OpenAI-compatible providers. Set ``provider``
to ``"openai"`` in the strategy config (or via ``configure()``) to use any
OpenAI-compatible endpoint, including local models served via Ollama/vLLM.

The prompt is enriched with optional technical analysis (from price history)
and recent news headlines when available.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy
from polymarket_agent.strategies.indicators import TechnicalContext, analyze_market_technicals

if TYPE_CHECKING:
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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 128,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": "Respond with ONLY a single decimal number between 0.0 and 1.0. No other text.",
                },
                {"role": "user", "content": prompt},
            ],
        }

        if self._extra_params:
            kwargs["extra_body"] = self._extra_params

        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        return content.strip() if content else ""

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

        # News section (optional)
        news_items = self._fetch_news(market.question)
        if news_items:
            prompt += f"\n--- RECENT NEWS ---\n{self._format_news_summary(news_items)}\n"

        prompt += (
            "--- END MARKET DATA ---\n"
            f"\nCurrent market price: {yes_price:.2f}\n"
            "Respond with ONLY a single decimal number between 0.0 and 1.0."
        )

        try:
            text = self._call_llm(prompt)
            # Use findall + take last match: thinking models (e.g. Qwen) emit
            # reasoning text before the final numeric answer.
            matches = re.findall(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", text)
            if not matches:
                logger.warning("Could not parse probability from AI response: %s", text[:200])
                return None
            estimate = float(matches[-1])
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
