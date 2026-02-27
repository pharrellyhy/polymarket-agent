"""AIAnalyst strategy — uses an LLM to estimate market probabilities.

Supports both Anthropic and OpenAI-compatible providers. Set ``provider``
to ``"openai"`` in the strategy config (or via ``configure()``) to use any
OpenAI-compatible endpoint, including local models served via Ollama/vLLM.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy

if TYPE_CHECKING:
    from polymarket_agent.data.provider import DataProvider

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER: str = "anthropic"
_DEFAULT_MODEL: str = "claude-sonnet-4-6"
_DEFAULT_MAX_CALLS_PER_HOUR: int = 20
_DEFAULT_MIN_DIVERGENCE: float = 0.15
_DEFAULT_ORDER_SIZE: float = 25.0

_MAX_QUESTION_LEN: int = 500
_MAX_DESCRIPTION_LEN: int = 1000

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
        self._call_timestamps: list[float] = []
        self._client: Any = None
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

    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        if self._client is None:
            return []

        signals: list[Signal] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            if not self._can_call():
                break
            if (signal := self._evaluate(market)) is not None:
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
                max_tokens=16,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            return str(response.content[0].text).strip()

        # OpenAI-compatible provider
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=16,
            temperature=0,
            messages=[
                {"role": "system", "content": "Respond with ONLY a single decimal number between 0.0 and 1.0. No other text."},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content
        return content.strip() if content else ""

    def _evaluate(self, market: Market) -> Signal | None:
        if not market.outcome_prices or not market.clob_token_ids:
            return None

        yes_price = market.outcome_prices[0]

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
        prompt += (
            "--- END MARKET DATA ---\n"
            f"\nCurrent market price: {yes_price:.2f}\n"
            "Respond with ONLY a single decimal number between 0.0 and 1.0."
        )

        try:
            text = self._call_llm(prompt)
            match = re.search(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", text)
            if not match:
                logger.warning("Could not parse probability from AI response: %s", text)
                return None
            estimate = float(match.group(1))
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
        token_id = market.clob_token_ids[0]

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
