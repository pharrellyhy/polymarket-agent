"""AIAnalyst strategy — uses Claude to estimate market probabilities."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_MODEL: str = "claude-sonnet-4-6"
_DEFAULT_MAX_CALLS_PER_HOUR: int = 20
_DEFAULT_MIN_DIVERGENCE: float = 0.15
_DEFAULT_ORDER_SIZE: float = 25.0


class AIAnalyst(Strategy):
    """Ask Claude for probability estimates and trade on divergence.

    Sends market question + description to Claude, parses a probability
    from the response. If the estimate diverges from the market price
    by more than ``min_divergence``, a buy or sell signal is generated.

    Gracefully degrades when ANTHROPIC_API_KEY is not set.
    """

    name: str = "ai_analyst"

    def __init__(self) -> None:
        self._model: str = _DEFAULT_MODEL
        self._max_calls_per_hour: int = _DEFAULT_MAX_CALLS_PER_HOUR
        self._min_divergence: float = _DEFAULT_MIN_DIVERGENCE
        self._order_size: float = _DEFAULT_ORDER_SIZE
        self._call_timestamps: list[float] = []
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.info("ANTHROPIC_API_KEY not set — AIAnalyst disabled")
            return
        try:
            import anthropic

            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — AIAnalyst disabled")

    def configure(self, config: dict[str, Any]) -> None:
        self._model = config.get("model", _DEFAULT_MODEL)
        self._max_calls_per_hour = int(config.get("max_calls_per_hour", _DEFAULT_MAX_CALLS_PER_HOUR))
        self._min_divergence = float(config.get("min_divergence", _DEFAULT_MIN_DIVERGENCE))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))

    def analyze(self, markets: list[Market], data: Any) -> list[Signal]:
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

    def _evaluate(self, market: Market) -> Signal | None:
        if not market.outcome_prices or not market.clob_token_ids:
            return None

        yes_price = market.outcome_prices[0]

        prompt = (
            f"You are a prediction market analyst. Estimate the probability (0.0 to 1.0) "
            f"that the following question resolves to Yes.\n\n"
            f"Question: {market.question}\n"
        )
        if market.description:
            prompt += f"Description: {market.description}\n"
        prompt += (
            f"\nCurrent market price: {yes_price:.2f}\n"
            f"Respond with ONLY a single decimal number between 0.0 and 1.0."
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}],
            )
            text: str = response.content[0].text.strip()
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
