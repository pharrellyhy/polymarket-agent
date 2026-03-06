"""DateCurveTrader strategy — trades date-based probability curves.

Detects groups of date-based prediction markets (e.g., "X by March 7",
"X by March 14") that form cumulative probability curves, then:

1. Validates term structure (monotonicity) for arbitrage signals
2. Uses LLM + news to estimate fair curve and trade divergences
"""

import json
import logging
import math
import os
import re
import time
from datetime import date
from typing import Any, Literal

from polymarket_agent.data.models import DateCurve, DateCurvePoint, Market
from polymarket_agent.data.provider import DataProvider
from polymarket_agent.news.models import NewsItem
from polymarket_agent.news.provider import NewsProvider
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER: str = "openai"
_DEFAULT_MODEL: str = "gpt-4o"
_DEFAULT_MAX_CALLS_PER_HOUR: int = 10
_DEFAULT_MIN_DIVERGENCE: float = 0.10
_DEFAULT_ORDER_SIZE: float = 25.0
_DEFAULT_MIN_PRICE: float = 0.03
_DEFAULT_CACHE_TTL: int = 3600
_DEFAULT_ARB_CONFIDENCE: float = 0.9
_DEFAULT_MAX_TOKENS: int = 2048
_DEFAULT_NEWS_MAX_RESULTS: int = 5

_SUPPORTED_PROVIDERS: set[str] = {"anthropic", "openai"}
_DEFAULT_API_KEY_ENVS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# Regex patterns for date extraction fallback
_DATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"by\s+(january|february|march|april|may|june|july|august|september|october|november|december)"
        r"\s+(\d{1,2})(?:,?\s+(\d{4}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"before\s+(january|february|march|april|may|june|july|august|september|october|november|december)"
        r"\s+(\d{1,2})(?:,?\s+(\d{4}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"by\s+(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)"
        r"(?:\s+(\d{4}))?",
        re.IGNORECASE,
    ),
]

_MONTH_MAP: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _extract_date_from_question(question: str) -> str | None:
    """Try to extract an ISO date string from a market question using regex."""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(question)
        if not match:
            continue
        groups = match.groups()
        if groups[0].isdigit():
            # Pattern: "by DD Month [YYYY]"
            day = int(groups[0])
            month_name = groups[1].lower()
            year_str = groups[2]
        else:
            # Pattern: "by Month DD [YYYY]"
            month_name = groups[0].lower()
            day = int(groups[1])
            year_str = groups[2]
        month = _MONTH_MAP.get(month_name)
        if month is None:
            continue
        year = int(year_str) if year_str else date.today().year
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return None


def _extract_base_question(question: str) -> str:
    """Strip the date suffix from a question to get the base event."""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(question)
        if match:
            return question[: match.start()].rstrip(" ?").strip()
    return question


class DateCurveTrader(Strategy):
    """Trade date-based probability curves using term structure + LLM analysis."""

    name: str = "date_curve_trader"

    def __init__(self) -> None:
        self._provider: str = _DEFAULT_PROVIDER
        self._model: str = _DEFAULT_MODEL
        self._base_url: str | None = None
        self._api_key_env: str | None = None
        self._max_calls_per_hour: int = _DEFAULT_MAX_CALLS_PER_HOUR
        self._min_divergence: float = _DEFAULT_MIN_DIVERGENCE
        self._order_size: float = _DEFAULT_ORDER_SIZE
        self._min_price: float = _DEFAULT_MIN_PRICE
        self._cache_ttl: int = _DEFAULT_CACHE_TTL
        self._arb_confidence: float = _DEFAULT_ARB_CONFIDENCE
        self._max_tokens: int = _DEFAULT_MAX_TOKENS
        self._news_max_results: int = _DEFAULT_NEWS_MAX_RESULTS
        self._extra_params: dict[str, Any] = {}
        self._call_timestamps: list[float] = []
        self._client: Any = None
        self._news_provider: NewsProvider | None = None
        # Curve detection cache: (curves, timestamp)
        self._curve_cache: tuple[list[DateCurve], float] | None = None
        self._init_client()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _resolved_api_key_env(self) -> str:
        if self._api_key_env:
            return self._api_key_env
        return _DEFAULT_API_KEY_ENVS.get(self._provider, "OPENAI_API_KEY")

    def _init_client(self) -> None:
        if self._provider == "anthropic":
            self._init_anthropic_client()
        elif self._provider == "openai":
            self._init_openai_client()
        else:
            logger.warning("Unknown provider %r; falling back to %s", self._provider, _DEFAULT_PROVIDER)
            self._provider = _DEFAULT_PROVIDER
            self._init_openai_client()

    def _init_anthropic_client(self) -> None:
        env_var = self._resolved_api_key_env()
        api_key = os.environ.get(env_var)
        if not api_key:
            logger.info("%s not set — DateCurveTrader disabled", env_var)
            return
        try:
            import anthropic  # noqa: PLC0415

            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — DateCurveTrader disabled")

    def _init_openai_client(self) -> None:
        env_var = self._resolved_api_key_env()
        api_key = os.environ.get(env_var)
        if not api_key:
            logger.info("%s not set — DateCurveTrader disabled", env_var)
            return
        try:
            import openai  # noqa: PLC0415

            kwargs: dict[str, Any] = {"api_key": api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
        except ImportError:
            logger.warning("openai package not installed — DateCurveTrader disabled")

    def configure(self, config: dict[str, Any]) -> None:
        self._model = config.get("model", self._model)
        self._max_calls_per_hour = int(config.get("max_calls_per_hour", self._max_calls_per_hour))
        self._min_divergence = float(config.get("min_divergence", self._min_divergence))
        self._order_size = float(config.get("order_size", self._order_size))
        self._min_price = float(config.get("min_price", self._min_price))
        self._cache_ttl = int(config.get("cache_ttl_seconds", self._cache_ttl))
        self._arb_confidence = float(config.get("arb_confidence", self._arb_confidence))
        self._max_tokens = int(config.get("max_tokens", self._max_tokens))
        self._news_max_results = max(1, int(config.get("news_max_results", self._news_max_results)))
        self._extra_params = dict(config.get("extra_params", self._extra_params))

        raw_provider = config.get("provider", self._provider)
        new_provider = str(raw_provider).strip().lower() if raw_provider is not None else self._provider
        if new_provider not in _SUPPORTED_PROVIDERS:
            logger.warning("Unknown provider %r; using %s", raw_provider, _DEFAULT_PROVIDER)
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
        """Attach a news provider for curve analysis enrichment."""
        self._news_provider = provider
        if max_results is not None:
            self._news_max_results = max(1, int(max_results))

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------

    def _can_call(self) -> bool:
        now = time.monotonic()
        cutoff = now - 3600.0
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        return len(self._call_timestamps) < self._max_calls_per_hour

    def _call_llm(self, prompt: str) -> str:
        if self._provider == "anthropic":
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            return str(response.content[0].text).strip()

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "You are a prediction market analyst. Follow the user's instructions exactly."},
                {"role": "user", "content": prompt},
            ],
        }
        if self._extra_params:
            kwargs["extra_body"] = self._extra_params

        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        return content.strip() if content else ""

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        curves = self._detect_curves(markets)
        if not curves:
            return []

        signals: list[Signal] = []

        for curve in curves:
            # 1. Term structure validation (pure math, no LLM)
            signals.extend(self._check_term_structure(curve))

            # 2. News-driven curve analysis (one LLM call per curve, requires LLM)
            if self._client is not None and self._can_call():
                signals.extend(self._analyze_curve_with_news(curve))

        logger.info("DateCurveTrader: %d curves detected, %d signals generated", len(curves), len(signals))
        return signals

    # ------------------------------------------------------------------
    # Curve detection
    # ------------------------------------------------------------------

    def _detect_curves(self, markets: list[Market]) -> list[DateCurve]:
        """Detect date-based market curves. Uses cached result if within TTL."""
        now = time.monotonic()
        if self._curve_cache is not None:
            cached_curves, cached_at = self._curve_cache
            if (now - cached_at) < self._cache_ttl:
                # Re-price cached curves with current market data
                return self._reprice_cached_curves(cached_curves, markets)

        # Try LLM-based detection first, fall back to regex
        curves: list[DateCurve] = []
        if self._client is not None and self._can_call():
            curves = self._detect_curves_llm(markets)
        if not curves:
            curves = self._detect_curves_regex(markets)

        self._curve_cache = (curves, now)
        return curves

    def _reprice_cached_curves(self, cached: list[DateCurve], markets: list[Market]) -> list[DateCurve]:
        """Update prices in cached curves using current market data."""
        market_prices: dict[str, float] = {}
        for m in markets:
            if m.outcome_prices:
                market_prices[m.id] = m.outcome_prices[0]

        updated: list[DateCurve] = []
        for curve in cached:
            new_points: list[DateCurvePoint] = []
            for point in curve.points:
                price = market_prices.get(point.market.id, point.price)
                # Also update the market object if available
                current_market = next((m for m in markets if m.id == point.market.id), point.market)
                new_points.append(DateCurvePoint(date=point.date, market=current_market, price=price))
            updated.append(DateCurve(base_question=curve.base_question, points=new_points))
        return updated

    def _detect_curves_llm(self, markets: list[Market]) -> list[DateCurve]:
        """Use LLM to identify date-based market groups."""
        active_markets = [m for m in markets if m.active and not m.closed]
        if len(active_markets) < 2:
            return []

        market_list = "\n".join(
            f'{i + 1}. "{m.question}" (id: {m.id})'
            for i, m in enumerate(active_markets[:100])
        )

        prompt = (
            "Given these active market questions, identify groups of date-based prediction markets.\n"
            'Date-based markets ask "Will X happen by [date]?" with multiple date options for the same event.\n\n'
            f"Markets:\n{market_list}\n\n"
            'Return JSON: [{"base_question": "...", "markets": [{"id": "...", "date": "YYYY-MM-DD"}]}]\n'
            "Only include markets that are clearly date-based with at least 2 date options for the same event.\n"
            "Return ONLY the JSON array, no other text."
        )

        try:
            text = self._call_llm(prompt)
            self._call_timestamps.append(time.monotonic())
            return self._parse_curve_detection_response(text, active_markets)
        except Exception:
            logger.exception("LLM curve detection failed")
            return []

    def _parse_curve_detection_response(self, text: str, markets: list[Market]) -> list[DateCurve]:
        """Parse LLM JSON response into DateCurve objects."""
        market_map: dict[str, Market] = {m.id: m for m in markets}

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r"\[.*\]", text, re.DOTALL)
        if not json_match:
            logger.warning("Could not extract JSON from curve detection response")
            return []

        try:
            groups = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in curve detection response")
            return []

        curves: list[DateCurve] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            base_q = group.get("base_question", "")
            group_markets = group.get("markets", [])
            if len(group_markets) < 2:
                continue

            points: list[DateCurvePoint] = []
            for entry in group_markets:
                if not isinstance(entry, dict):
                    continue
                market_id = entry.get("id", "")
                date_str = entry.get("date", "")
                market = market_map.get(market_id)
                if market is None or not date_str:
                    continue
                price = market.outcome_prices[0] if market.outcome_prices else 0.0
                points.append(DateCurvePoint(date=date_str, market=market, price=price))

            if len(points) >= 2:
                points.sort(key=lambda p: p.date)
                curves.append(DateCurve(base_question=base_q, points=points))

        return curves

    def _detect_curves_regex(self, markets: list[Market]) -> list[DateCurve]:
        """Fallback: detect date curves using regex pattern matching."""
        dated_markets: list[tuple[str, str, Market]] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            iso_date = _extract_date_from_question(market.question)
            if iso_date is None:
                continue
            base_q = _extract_base_question(market.question)
            dated_markets.append((base_q, iso_date, market))

        # Group by base question (normalized)
        groups: dict[str, list[tuple[str, Market]]] = {}
        for base_q, iso_date, market in dated_markets:
            key = base_q.lower().strip()
            groups.setdefault(key, []).append((iso_date, market))

        curves: list[DateCurve] = []
        for key, items in groups.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda x: x[0])
            base_question = _extract_base_question(items[0][1].question)
            points = [
                DateCurvePoint(
                    date=iso_date,
                    market=market,
                    price=market.outcome_prices[0] if market.outcome_prices else 0.0,
                )
                for iso_date, market in items
            ]
            curves.append(DateCurve(base_question=base_question, points=points))

        return curves

    # ------------------------------------------------------------------
    # Term structure validation
    # ------------------------------------------------------------------

    def _check_term_structure(self, curve: DateCurve) -> list[Signal]:
        """Check monotonicity and emit arbitrage signals for violations."""
        signals: list[Signal] = []
        for i in range(len(curve.points) - 1):
            earlier = curve.points[i]
            later = curve.points[i + 1]

            # Monotonicity violation: later date should have >= price
            if later.price < earlier.price - 0.005:  # 0.5% tolerance for spread noise
                # Buy the underpriced later date
                if later.market.clob_token_ids and later.price >= self._min_price:
                    signals.append(Signal(
                        strategy=self.name,
                        market_id=later.market.id,
                        token_id=later.market.clob_token_ids[0],
                        side="buy",
                        confidence=self._arb_confidence,
                        target_price=later.price,
                        size=self._order_size,
                        reason=(
                            f"term_structure_arb: {curve.base_question} "
                            f"{later.date}={later.price:.3f} < {earlier.date}={earlier.price:.3f}"
                        ),
                    ))
                # Buy No token on the overpriced earlier date
                if len(earlier.market.clob_token_ids) >= 2 and earlier.price >= self._min_price:
                    signals.append(Signal(
                        strategy=self.name,
                        market_id=earlier.market.id,
                        token_id=earlier.market.clob_token_ids[1],  # No token
                        side="buy",
                        confidence=self._arb_confidence,
                        target_price=1.0 - earlier.price,
                        size=self._order_size,
                        reason=(
                            f"term_structure_arb: {curve.base_question} "
                            f"{earlier.date}={earlier.price:.3f} > {later.date}={later.price:.3f}"
                        ),
                    ))
        return signals

    # ------------------------------------------------------------------
    # News-driven curve analysis
    # ------------------------------------------------------------------

    def _analyze_curve_with_news(self, curve: DateCurve) -> list[Signal]:
        """Analyze a curve with LLM + news to find divergences."""
        news_items = self._fetch_news(curve.base_question)
        news_section = self._format_news_summary(news_items) if news_items else "No recent news available."

        today = date.today().isoformat()
        price_lines = "\n".join(
            f"  {p.date}: {p.price:.3f}" for p in curve.points
        )

        prompt = (
            "You are analyzing a date-based prediction market curve.\n\n"
            f'Event: "{curve.base_question}"\n'
            f"Today's date: {today}\n\n"
            "Current market prices (probability that event happens BY each date):\n"
            f"{price_lines}\n\n"
            "These prices form a cumulative probability curve. Later dates MUST have equal\n"
            "or higher probability than earlier dates.\n\n"
            f"Recent news:\n{news_section}\n\n"
            "Based on the news and your analysis:\n"
            "1. How does recent news shift the likely timeline for this event?\n"
            "2. Which date intervals see the biggest probability change?\n"
            "3. Estimate the fair probability for each date (0.0-1.0).\n\n"
            "Return your estimates as JSON: {\"estimates\": [{\"date\": \"YYYY-MM-DD\", \"probability\": 0.XX}]}\n"
            "Ensure probabilities are monotonically increasing. Return ONLY the JSON."
        )

        try:
            text = self._call_llm(prompt)
            self._call_timestamps.append(time.monotonic())
            return self._parse_curve_analysis(text, curve)
        except Exception:
            logger.exception("LLM curve analysis failed for %s", curve.base_question)
            return []

    def _parse_curve_analysis(self, text: str, curve: DateCurve) -> list[Signal]:
        """Parse LLM curve estimates and generate divergence signals."""
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            logger.warning("Could not extract JSON from curve analysis response")
            return []

        try:
            result = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in curve analysis response")
            return []

        estimates_list = result.get("estimates", [])
        if not estimates_list:
            return []

        # Map date -> LLM estimate
        llm_estimates: dict[str, float] = {}
        for entry in estimates_list:
            if isinstance(entry, dict) and "date" in entry and "probability" in entry:
                prob = float(entry["probability"])
                prob = max(0.0, min(1.0, prob))
                # Apply Platt scaling
                prob = self._extremize(prob)
                llm_estimates[entry["date"]] = prob

        signals: list[Signal] = []
        for point in curve.points:
            llm_price = llm_estimates.get(point.date)
            if llm_price is None:
                continue

            divergence = llm_price - point.price
            if abs(divergence) < self._min_divergence:
                continue
            if point.price < self._min_price:
                continue
            if not point.market.clob_token_ids:
                continue

            confidence = 1.0 / (1.0 + math.exp(-20.0 * (abs(divergence) - 0.15)))

            if divergence > 0:
                side: Literal["buy", "sell"] = "buy"
                token_id = point.market.clob_token_ids[0]  # Yes token
                target_price = point.price
            else:
                if len(point.market.clob_token_ids) < 2:
                    continue
                side = "buy"
                token_id = point.market.clob_token_ids[1]  # No token
                target_price = 1.0 - point.price

            signals.append(Signal(
                strategy=self.name,
                market_id=point.market.id,
                token_id=token_id,
                side=side,
                confidence=round(confidence, 4),
                target_price=target_price,
                size=self._order_size,
                reason=(
                    f"curve_divergence: {curve.base_question} {point.date} "
                    f"llm={llm_price:.3f} market={point.price:.3f} div={divergence:+.3f}"
                ),
            ))

        return signals

    @staticmethod
    def _extremize(p: float, alpha: float = math.sqrt(3)) -> float:
        """Apply Platt scaling to correct LLM hedging bias."""
        if p <= 0.0 or p >= 1.0:
            return p
        log_odds = math.log(p / (1.0 - p))
        return 1.0 / (1.0 + math.exp(-alpha * log_odds))

    # ------------------------------------------------------------------
    # News helpers
    # ------------------------------------------------------------------

    def _fetch_news(self, query: str) -> list[NewsItem]:
        if self._news_provider is None:
            return []
        try:
            return self._news_provider.search(query[:100], max_results=self._news_max_results)
        except Exception:
            logger.debug("Failed to fetch news for query: %s", query[:50])
            return []

    @staticmethod
    def _format_news_summary(items: list[NewsItem]) -> str:
        lines: list[str] = []
        for item in items[:5]:
            title = item.title[:200]
            date_part = f" ({item.published[:40]})" if item.published else ""
            lines.append(f"- {title}{date_part}")
        return "\n".join(lines)
