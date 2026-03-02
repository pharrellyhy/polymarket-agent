"""CrossPlatformArb strategy — detect arbitrage across prediction markets.

Fetches prices from Kalshi and Metaculus, fuzzy-matches questions to
Polymarket markets, and signals when price divergences exceed fee thresholds.
"""

import difflib
import logging
from typing import Any, Literal

from polymarket_agent.data.external_prices import KalshiClient, MetaculusClient
from polymarket_agent.data.models import CrossPlatformPrice, Market
from polymarket_agent.data.provider import DataProvider
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_MIN_DIVERGENCE = 0.05
_DEFAULT_SIMILARITY_THRESHOLD = 0.55
_DEFAULT_ORDER_SIZE = 25.0
_DEFAULT_POLYMARKET_FEE = 0.02
_DEFAULT_EXTERNAL_FEE = 0.03
_DEFAULT_KALSHI_CACHE_TTL = 300.0
_DEFAULT_METACULUS_CACHE_TTL = 600.0


class CrossPlatformArb(Strategy):
    """Detect cross-platform arbitrage opportunities.

    Compares Polymarket prices against Kalshi and Metaculus prices for
    similar questions. Signals when divergence exceeds combined fees.
    """

    name: str = "cross_platform_arb"

    def __init__(self) -> None:
        self._min_divergence: float = _DEFAULT_MIN_DIVERGENCE
        self._similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD
        self._order_size: float = _DEFAULT_ORDER_SIZE
        self._polymarket_fee: float = _DEFAULT_POLYMARKET_FEE
        self._external_fee: float = _DEFAULT_EXTERNAL_FEE
        self._kalshi: KalshiClient = KalshiClient(cache_ttl=_DEFAULT_KALSHI_CACHE_TTL)
        self._metaculus: MetaculusClient = MetaculusClient(cache_ttl=_DEFAULT_METACULUS_CACHE_TTL)
        self._kalshi_api_key_env: str = "KALSHI_API_KEY"
        self._metaculus_api_key_env: str = "METACULUS_API_KEY"

    def configure(self, config: dict[str, Any]) -> None:
        self._min_divergence = float(config.get("min_divergence", _DEFAULT_MIN_DIVERGENCE))
        self._similarity_threshold = float(config.get("similarity_threshold", _DEFAULT_SIMILARITY_THRESHOLD))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))
        self._polymarket_fee = float(config.get("polymarket_fee", _DEFAULT_POLYMARKET_FEE))
        self._external_fee = float(config.get("external_fee", _DEFAULT_EXTERNAL_FEE))
        self._kalshi_api_key_env = config.get("kalshi_api_key_env", self._kalshi_api_key_env)
        self._metaculus_api_key_env = config.get("metaculus_api_key_env", self._metaculus_api_key_env)
        kalshi_ttl = float(config.get("kalshi_cache_ttl", _DEFAULT_KALSHI_CACHE_TTL))
        metaculus_ttl = float(config.get("metaculus_cache_ttl", _DEFAULT_METACULUS_CACHE_TTL))
        self._kalshi = KalshiClient(cache_ttl=kalshi_ttl, api_key_env=self._kalshi_api_key_env)
        self._metaculus = MetaculusClient(cache_ttl=metaculus_ttl, api_key_env=self._metaculus_api_key_env)

    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        external_prices = self._fetch_external_prices()
        if not external_prices:
            logger.debug("CrossPlatformArb: no external prices available")
            return []

        active_markets = [m for m in markets if m.active and not m.closed and m.outcome_prices]
        return self._find_arbitrage(active_markets, external_prices)

    def _fetch_external_prices(self) -> list[CrossPlatformPrice]:
        """Fetch prices from all external platforms."""
        prices: list[CrossPlatformPrice] = []
        try:
            prices.extend(self._kalshi.get_active_events())
        except Exception:
            logger.debug("Failed to fetch Kalshi prices")
        try:
            prices.extend(self._metaculus.get_active_questions())
        except Exception:
            logger.debug("Failed to fetch Metaculus prices")
        return prices

    def _find_arbitrage(
        self,
        markets: list[Market],
        external_prices: list[CrossPlatformPrice],
    ) -> list[Signal]:
        """Find arbitrage opportunities via fuzzy matching and divergence check."""
        signals: list[Signal] = []
        fee_threshold = self._min_divergence + self._polymarket_fee + self._external_fee

        for market in markets:
            if not market.clob_token_ids:
                continue

            best_match = self._find_best_match(market.question, external_prices)
            if best_match is None:
                continue

            ext_price, similarity = best_match
            polymarket_price = market.outcome_prices[0]
            divergence = ext_price.probability - polymarket_price

            if abs(divergence) < fee_threshold:
                continue

            side: Literal["buy", "sell"] = "buy" if divergence > 0 else "sell"
            confidence = min(abs(divergence) / 0.3, 1.0) * similarity

            signals.append(
                Signal(
                    strategy=self.name,
                    market_id=market.id,
                    token_id=market.clob_token_ids[0],
                    side=side,
                    confidence=round(confidence, 4),
                    target_price=polymarket_price,
                    size=self._order_size,
                    reason=(
                        f"cross_arb: {ext_price.platform}={ext_price.probability:.4f} "
                        f"vs poly={polymarket_price:.4f} "
                        f"(div={divergence:+.4f}, sim={similarity:.2f})"
                    ),
                )
            )
        return signals

    def _find_best_match(
        self,
        question: str,
        external_prices: list[CrossPlatformPrice],
    ) -> tuple[CrossPlatformPrice, float] | None:
        """Find the best fuzzy match for a Polymarket question among external prices."""
        best: tuple[CrossPlatformPrice, float] | None = None
        question_lower = question.lower()

        for ext in external_prices:
            similarity = difflib.SequenceMatcher(None, question_lower, ext.question.lower()).ratio()
            if similarity < self._similarity_threshold:
                continue
            if best is None or similarity > best[1]:
                best = (ext, similarity)

        return best
