"""SportsDerivativeTrader strategy — trades derivative sports markets.

Targets cross-market inefficiencies in sports prediction markets:
- Bracket sum mispricing (multi-outcome probabilities != 1.0)
- Hierarchy inconsistency (championship price > series price)
- Cascade lag (game resolves but derivative markets lag)
- LLM-driven derivative analysis with event graph context
"""

import json
import logging
import math
import os
import re
import time
from datetime import date
from typing import Any, Literal

from polymarket_agent.data.models import Market, SportsEventGraph, SportsMarketNode, categorize_market
from polymarket_agent.data.provider import DataProvider
from polymarket_agent.news.models import NewsItem
from polymarket_agent.news.provider import NewsProvider
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER: str = "openai"
_DEFAULT_MODEL: str = "gpt-4o"
_DEFAULT_MAX_CALLS_PER_HOUR: int = 15
_DEFAULT_MIN_DIVERGENCE: float = 0.08
_DEFAULT_ORDER_SIZE: float = 15.0
_DEFAULT_MIN_PRICE: float = 0.03
_DEFAULT_MIN_VOLUME_24H: float = 200.0
_DEFAULT_CACHE_TTL: int = 1800
_DEFAULT_BRACKET_SUM_TOLERANCE: float = 0.05
_DEFAULT_CASCADE_MIN_MOVE: float = 0.15
_DEFAULT_CASCADE_CONFIDENCE: float = 0.75
_DEFAULT_HIERARCHY_CONFIDENCE: float = 0.85
_DEFAULT_MAX_TOKENS: int = 2048
_DEFAULT_NEWS_MAX_RESULTS: int = 5

_SUPPORTED_PROVIDERS: set[str] = {"anthropic", "openai"}
_DEFAULT_API_KEY_ENVS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# Regex patterns for sports market type classification (fallback)
_GAME_PATTERN: re.Pattern[str] = re.compile(
    r"game\s+\d|vs\.?\s|versus\s", re.IGNORECASE,
)
_SERIES_PATTERN: re.Pattern[str] = re.compile(
    r"win\s+(the\s+)?(\w+\s+)*(series|conference|division|round|wcf|ecf|afc|nfc)", re.IGNORECASE,
)
_CHAMPIONSHIP_PATTERN: re.Pattern[str] = re.compile(
    r"win\s+(the\s+)?(championship|title|nba\s+title|super\s+bowl|world\s+series|stanley\s+cup|world\s+cup)",
    re.IGNORECASE,
)
_PLAYER_PROP_PATTERN: re.Pattern[str] = re.compile(
    r"\bmvp\b|scoring\s+leader|rookie\s+of|dpoy|most\s+valuable|cy\s+young|heisman", re.IGNORECASE,
)


def _classify_market_type_regex(question: str) -> str:
    """Classify a sports market type using regex patterns."""
    if _CHAMPIONSHIP_PATTERN.search(question):
        return "championship"
    if _SERIES_PATTERN.search(question):
        return "series"
    if _PLAYER_PROP_PATTERN.search(question):
        return "player_prop"
    if _GAME_PATTERN.search(question):
        return "game"
    return "championship"  # default for ambiguous sports markets


def _is_resolved_price(price: float) -> bool:
    """Check if a price indicates resolution (near 0 or 1)."""
    return price <= 0.02 or price >= 0.98


class SportsDerivativeTrader(Strategy):
    """Trade derivative sports markets using event graph analysis."""

    name: str = "sports_derivative_trader"

    def __init__(self) -> None:
        self._provider: str = _DEFAULT_PROVIDER
        self._model: str = _DEFAULT_MODEL
        self._base_url: str | None = None
        self._api_key_env: str | None = None
        self._max_calls_per_hour: int = _DEFAULT_MAX_CALLS_PER_HOUR
        self._min_divergence: float = _DEFAULT_MIN_DIVERGENCE
        self._order_size: float = _DEFAULT_ORDER_SIZE
        self._min_price: float = _DEFAULT_MIN_PRICE
        self._min_volume_24h: float = _DEFAULT_MIN_VOLUME_24H
        self._cache_ttl: int = _DEFAULT_CACHE_TTL
        self._bracket_sum_tolerance: float = _DEFAULT_BRACKET_SUM_TOLERANCE
        self._cascade_min_move: float = _DEFAULT_CASCADE_MIN_MOVE
        self._cascade_confidence: float = _DEFAULT_CASCADE_CONFIDENCE
        self._hierarchy_confidence: float = _DEFAULT_HIERARCHY_CONFIDENCE
        self._max_tokens: int = _DEFAULT_MAX_TOKENS
        self._news_max_results: int = _DEFAULT_NEWS_MAX_RESULTS
        self._extra_params: dict[str, Any] = {}
        self._call_timestamps: list[float] = []
        self._client: Any = None
        self._news_provider: NewsProvider | None = None
        # Event graph cache: (graphs, timestamp)
        self._graph_cache: tuple[list[SportsEventGraph], float] | None = None
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
            logger.info("%s not set — SportsDerivativeTrader disabled", env_var)
            return
        try:
            import anthropic  # noqa: PLC0415

            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — SportsDerivativeTrader disabled")

    def _init_openai_client(self) -> None:
        env_var = self._resolved_api_key_env()
        api_key = os.environ.get(env_var)
        if not api_key:
            logger.info("%s not set — SportsDerivativeTrader disabled", env_var)
            return
        try:
            import openai  # noqa: PLC0415

            kwargs: dict[str, Any] = {"api_key": api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)
        except ImportError:
            logger.warning("openai package not installed — SportsDerivativeTrader disabled")

    def configure(self, config: dict[str, Any]) -> None:
        self._model = config.get("model", self._model)
        self._max_calls_per_hour = int(config.get("max_calls_per_hour", self._max_calls_per_hour))
        self._min_divergence = float(config.get("min_divergence", self._min_divergence))
        self._order_size = float(config.get("order_size", self._order_size))
        self._min_price = float(config.get("min_price", self._min_price))
        self._min_volume_24h = float(config.get("min_volume_24h", self._min_volume_24h))
        self._cache_ttl = int(config.get("cache_ttl_seconds", self._cache_ttl))
        self._bracket_sum_tolerance = float(config.get("bracket_sum_tolerance", self._bracket_sum_tolerance))
        self._cascade_min_move = float(config.get("cascade_min_move", self._cascade_min_move))
        self._cascade_confidence = float(config.get("cascade_confidence", self._cascade_confidence))
        self._hierarchy_confidence = float(config.get("hierarchy_confidence", self._hierarchy_confidence))
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
        """Attach a news provider for derivative analysis enrichment."""
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
                {"role": "system", "content": "You are a sports probability analyst. Follow the user's instructions exactly."},
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
        if self._client is None:
            return []

        sports_markets = self._identify_sports_markets(markets)
        if not sports_markets:
            return []

        graphs = self._get_event_graphs(sports_markets)
        if not graphs:
            return []

        signals: list[Signal] = []

        for graph in graphs:
            # 1. Bracket sum validation (pure math)
            signals.extend(self._check_bracket_sum(graph))

            # 2. Hierarchy consistency (pure math)
            signals.extend(self._check_hierarchy_consistency(graph))

            # 3. Cascade signal detection (price-based)
            signals.extend(self._check_cascade_signals(graph))

            # 4. LLM derivative analysis (one call per graph)
            if self._can_call():
                signals.extend(self._llm_derivative_analysis(graph))

        logger.info(
            "SportsDerivativeTrader: %d sports markets, %d graphs, %d signals",
            len(sports_markets), len(graphs), len(signals),
        )
        return signals

    # ------------------------------------------------------------------
    # Market identification
    # ------------------------------------------------------------------

    def _identify_sports_markets(self, markets: list[Market]) -> list[Market]:
        """Filter to active sports markets with sufficient volume."""
        return [
            m for m in markets
            if m.active
            and not m.closed
            and categorize_market(m) == "sports"
            and m.volume_24h >= self._min_volume_24h
        ]

    # ------------------------------------------------------------------
    # Event graph construction
    # ------------------------------------------------------------------

    def _get_event_graphs(self, sports_markets: list[Market]) -> list[SportsEventGraph]:
        """Build event graphs from sports markets, using cache when available."""
        now = time.monotonic()
        if self._graph_cache is not None:
            cached_graphs, cached_at = self._graph_cache
            if (now - cached_at) < self._cache_ttl:
                return self._reprice_cached_graphs(cached_graphs, sports_markets)

        graphs: list[SportsEventGraph] = []
        if self._can_call():
            graphs = self._build_event_graph_llm(sports_markets)
        if not graphs:
            graphs = self._build_event_graph_regex(sports_markets)

        self._graph_cache = (graphs, now)
        return graphs

    def _reprice_cached_graphs(
        self, cached: list[SportsEventGraph], markets: list[Market],
    ) -> list[SportsEventGraph]:
        """Update prices in cached graphs using current market data."""
        market_map: dict[str, Market] = {m.id: m for m in markets}
        updated: list[SportsEventGraph] = []
        for graph in cached:
            new_nodes: list[SportsMarketNode] = []
            for node in graph.nodes:
                current = market_map.get(node.market.id, node.market)
                is_resolved = bool(
                    current.outcome_prices and _is_resolved_price(current.outcome_prices[0])
                )
                new_nodes.append(SportsMarketNode(
                    market=current,
                    market_type=node.market_type,
                    team_or_player=node.team_or_player,
                    is_resolved=is_resolved,
                ))
            updated.append(SportsEventGraph(
                sport=graph.sport, event_label=graph.event_label, nodes=new_nodes,
            ))
        return updated

    def _build_event_graph_llm(self, sports_markets: list[Market]) -> list[SportsEventGraph]:
        """Use LLM to classify and group sports markets into event graphs."""
        if len(sports_markets) < 2:
            return []

        market_list = "\n".join(
            f'{i + 1}. "{m.question}" (id: {m.id})'
            for i, m in enumerate(sports_markets[:100])
        )

        prompt = (
            "Given these active sports prediction markets, classify each market and group\n"
            "related markets into event hierarchies.\n\n"
            "Market types:\n"
            '- "game": Individual game outcome (e.g., "Lakers vs Celtics Game 3")\n'
            '- "series": Series/playoff matchup winner (e.g., "Lakers win WCF")\n'
            '- "championship": Season/tournament winner (e.g., "Lakers win NBA title")\n'
            '- "player_prop": Player award/stat (e.g., "LeBron wins MVP")\n\n'
            f"Markets:\n{market_list}\n\n"
            'Return JSON: [{"event_label": "...", "sport": "nba|nfl|mlb|nhl|soccer|other",\n'
            '"markets": [{"id": "...", "type": "game|series|championship|player_prop",\n'
            '"team_or_player": "..."}]}]\n'
            "Group related markets together. Return ONLY the JSON array, no other text."
        )

        try:
            text = self._call_llm(prompt)
            self._call_timestamps.append(time.monotonic())
            return self._parse_event_graph_response(text, sports_markets)
        except Exception:
            logger.exception("LLM event graph construction failed")
            return []

    def _parse_event_graph_response(
        self, text: str, markets: list[Market],
    ) -> list[SportsEventGraph]:
        """Parse LLM JSON response into SportsEventGraph objects."""
        market_map: dict[str, Market] = {m.id: m for m in markets}

        json_match = re.search(r"\[.*\]", text, re.DOTALL)
        if not json_match:
            logger.warning("Could not extract JSON from event graph response")
            return []

        try:
            groups = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in event graph response")
            return []

        graphs: list[SportsEventGraph] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            event_label = group.get("event_label", "")
            sport = group.get("sport", "other")
            group_markets = group.get("markets", [])
            if len(group_markets) < 2:
                continue

            nodes: list[SportsMarketNode] = []
            for entry in group_markets:
                if not isinstance(entry, dict):
                    continue
                market_id = entry.get("id", "")
                market = market_map.get(market_id)
                if market is None:
                    continue
                market_type = entry.get("type", "championship")
                if market_type not in ("game", "series", "championship", "player_prop"):
                    market_type = "championship"
                team_or_player = entry.get("team_or_player", "")
                is_resolved = bool(
                    market.outcome_prices and _is_resolved_price(market.outcome_prices[0])
                )
                nodes.append(SportsMarketNode(
                    market=market,
                    market_type=market_type,
                    team_or_player=team_or_player,
                    is_resolved=is_resolved,
                ))

            if len(nodes) >= 2:
                graphs.append(SportsEventGraph(
                    sport=sport, event_label=event_label, nodes=nodes,
                ))

        return graphs

    def _build_event_graph_regex(self, sports_markets: list[Market]) -> list[SportsEventGraph]:
        """Fallback: build event graphs using regex classification and prefix grouping."""
        # Classify each market
        classified: list[tuple[str, str, Market]] = []
        for market in sports_markets:
            mtype = _classify_market_type_regex(market.question)
            # Skip individual game markets — we don't trade those
            if mtype == "game":
                continue
            classified.append((mtype, market.question, market))

        if len(classified) < 2:
            return []

        # Group by shared question prefix (30 chars)
        prefix_len = 30
        groups: dict[str, list[tuple[str, Market]]] = {}
        for mtype, question, market in classified:
            if len(question) < prefix_len:
                key = question.lower().strip()
            else:
                key = question[:prefix_len].lower().strip()
            groups.setdefault(key, []).append((mtype, market))

        graphs: list[SportsEventGraph] = []
        for _key, items in groups.items():
            if len(items) < 2:
                continue
            nodes: list[SportsMarketNode] = []
            for mtype, market in items:
                is_resolved = bool(
                    market.outcome_prices and _is_resolved_price(market.outcome_prices[0])
                )
                nodes.append(SportsMarketNode(
                    market=market,
                    market_type=mtype,
                    team_or_player="",
                    is_resolved=is_resolved,
                ))
            # Detect sport from keywords
            sample_q = items[0][1].question.lower()
            sport = "other"
            for s in ("nba", "nfl", "mlb", "nhl", "soccer"):
                if s in sample_q:
                    sport = s
                    break
            graphs.append(SportsEventGraph(
                sport=sport,
                event_label=items[0][1].question[:60],
                nodes=nodes,
            ))

        return graphs

    # ------------------------------------------------------------------
    # Bracket sum validation (pure math)
    # ------------------------------------------------------------------

    def _check_bracket_sum(self, graph: SportsEventGraph) -> list[Signal]:
        """Check if sibling markets in a multi-outcome event sum to ~1.0."""
        signals: list[Signal] = []

        # Group nodes by market_type to find sibling sets
        type_groups: dict[str, list[SportsMarketNode]] = {}
        for node in graph.nodes:
            if node.is_resolved:
                continue
            type_groups.setdefault(node.market_type, []).append(node)

        for mtype, siblings in type_groups.items():
            if len(siblings) < 3:
                continue

            yes_prices = [
                (node, node.market.outcome_prices[0])
                for node in siblings
                if node.market.outcome_prices
            ]
            if not yes_prices:
                continue

            total = sum(p for _, p in yes_prices)
            deviation = total - 1.0

            if abs(deviation) <= self._bracket_sum_tolerance:
                continue

            confidence = min(abs(deviation) / 0.15, 1.0)

            if deviation > 0:
                # Overpriced sum — sell the most overpriced candidate
                most_overpriced = max(yes_prices, key=lambda x: x[1])
                node, price = most_overpriced
                if price >= self._min_price and node.market.clob_token_ids:
                    signals.append(Signal(
                        strategy=self.name,
                        market_id=node.market.id,
                        token_id=node.market.clob_token_ids[0],
                        side="sell",
                        confidence=round(confidence, 4),
                        target_price=price,
                        size=self._order_size,
                        reason=(
                            f"bracket_sum: {graph.event_label} {mtype} "
                            f"sum={total:.3f} (dev={deviation:+.3f}), "
                            f"selling {node.team_or_player or node.market.question[:40]}"
                        ),
                    ))
            else:
                # Underpriced sum — buy the most underpriced candidate
                most_underpriced = min(yes_prices, key=lambda x: x[1])
                node, price = most_underpriced
                if price >= self._min_price and node.market.clob_token_ids:
                    signals.append(Signal(
                        strategy=self.name,
                        market_id=node.market.id,
                        token_id=node.market.clob_token_ids[0],
                        side="buy",
                        confidence=round(confidence, 4),
                        target_price=price,
                        size=self._order_size,
                        reason=(
                            f"bracket_sum: {graph.event_label} {mtype} "
                            f"sum={total:.3f} (dev={deviation:+.3f}), "
                            f"buying {node.team_or_player or node.market.question[:40]}"
                        ),
                    ))

        return signals

    # ------------------------------------------------------------------
    # Hierarchy consistency (pure math)
    # ------------------------------------------------------------------

    def _check_hierarchy_consistency(self, graph: SportsEventGraph) -> list[Signal]:
        """Validate P(championship) <= P(series) for each team."""
        signals: list[Signal] = []

        # Index nodes by (team, type) for lookup
        team_nodes: dict[str, dict[str, SportsMarketNode]] = {}
        for node in graph.nodes:
            if node.is_resolved or not node.team_or_player:
                continue
            key = node.team_or_player.lower()
            team_nodes.setdefault(key, {})[node.market_type] = node

        for team, type_map in team_nodes.items():
            championship = type_map.get("championship")
            series = type_map.get("series")

            if championship is None or series is None:
                continue
            if not championship.market.outcome_prices or not series.market.outcome_prices:
                continue

            champ_price = championship.market.outcome_prices[0]
            series_price = series.market.outcome_prices[0]

            # Violation: championship price > series price
            if champ_price > series_price + 0.01:  # 1% tolerance
                # Buy the underpriced series market
                if series.market.clob_token_ids and series_price >= self._min_price:
                    signals.append(Signal(
                        strategy=self.name,
                        market_id=series.market.id,
                        token_id=series.market.clob_token_ids[0],
                        side="buy",
                        confidence=self._hierarchy_confidence,
                        target_price=series_price,
                        size=self._order_size,
                        reason=(
                            f"hierarchy: {team} championship={champ_price:.3f} > "
                            f"series={series_price:.3f} — buying series"
                        ),
                    ))
                # Sell the overpriced championship market
                if championship.market.clob_token_ids and champ_price >= self._min_price:
                    signals.append(Signal(
                        strategy=self.name,
                        market_id=championship.market.id,
                        token_id=championship.market.clob_token_ids[0],
                        side="sell",
                        confidence=self._hierarchy_confidence,
                        target_price=champ_price,
                        size=self._order_size,
                        reason=(
                            f"hierarchy: {team} championship={champ_price:.3f} > "
                            f"series={series_price:.3f} — selling championship"
                        ),
                    ))

        return signals

    # ------------------------------------------------------------------
    # Cascade signal detection
    # ------------------------------------------------------------------

    def _check_cascade_signals(self, graph: SportsEventGraph) -> list[Signal]:
        """Detect when game resolution or sharp moves haven't cascaded to derivatives."""
        signals: list[Signal] = []

        # Find resolved or sharply-moved game markets
        cascade_triggers: list[SportsMarketNode] = []
        for node in graph.nodes:
            if node.market_type != "game":
                continue
            if node.is_resolved:
                cascade_triggers.append(node)
            elif abs(node.market.one_day_price_change) >= self._cascade_min_move:
                cascade_triggers.append(node)

        if not cascade_triggers:
            return signals

        # Find derivative markets that share a team with the triggered game
        derivatives = [
            n for n in graph.nodes
            if n.market_type in ("series", "championship") and not n.is_resolved
        ]

        for trigger in cascade_triggers:
            trigger_team = trigger.team_or_player.lower()
            if not trigger_team:
                continue

            for deriv in derivatives:
                deriv_team = deriv.team_or_player.lower()
                if not deriv_team or trigger_team not in deriv_team:
                    continue
                if not deriv.market.outcome_prices or not deriv.market.clob_token_ids:
                    continue

                # Check if derivative has a small 1-day price change relative to trigger
                deriv_move = abs(deriv.market.one_day_price_change)
                trigger_move = abs(trigger.market.one_day_price_change)

                if trigger.is_resolved:
                    # Game resolved — derivative should have moved significantly
                    if deriv_move < 0.03:
                        # Derivative hasn't moved — use LLM to determine direction
                        # For now, flag it but don't trade without direction info
                        logger.info(
                            "Cascade lag detected: %s resolved but %s barely moved (%.3f)",
                            trigger.market.question[:50],
                            deriv.market.question[:50],
                            deriv_move,
                        )
                elif trigger_move > 0 and deriv_move < trigger_move * 0.3:
                    # Sharp game move but derivative barely responded
                    deriv_price = deriv.market.outcome_prices[0]
                    if deriv_price < self._min_price:
                        continue

                    # Direction: if game price went up, derivative should go up too
                    game_direction = "up" if trigger.market.one_day_price_change > 0 else "down"
                    side: Literal["buy", "sell"] = "buy" if game_direction == "up" else "sell"

                    gap = trigger_move - deriv_move
                    confidence = min(
                        self._cascade_confidence * (1.0 / (1.0 + math.exp(-10.0 * (gap - 0.10)))),
                        0.95,
                    )

                    if confidence < 0.3:
                        continue

                    signals.append(Signal(
                        strategy=self.name,
                        market_id=deriv.market.id,
                        token_id=deriv.market.clob_token_ids[0],
                        side=side,
                        confidence=round(confidence, 4),
                        target_price=deriv_price,
                        size=self._order_size,
                        reason=(
                            f"cascade: {trigger.team_or_player} game moved {trigger_move:+.3f} "
                            f"but {deriv.market_type} only {deriv_move:+.3f}"
                        ),
                    ))

        return signals

    # ------------------------------------------------------------------
    # LLM derivative analysis
    # ------------------------------------------------------------------

    def _llm_derivative_analysis(self, graph: SportsEventGraph) -> list[Signal]:
        """Use LLM with full event graph + news to estimate fair derivative prices."""
        # Build context sections by market type
        championship_lines: list[str] = []
        series_lines: list[str] = []
        resolved_lines: list[str] = []
        player_prop_lines: list[str] = []

        for node in graph.nodes:
            price = node.market.outcome_prices[0] if node.market.outcome_prices else 0.0
            label = node.team_or_player or node.market.question[:60]

            if node.is_resolved:
                outcome = "YES" if price >= 0.98 else "NO"
                resolved_lines.append(f"  {label}: {outcome} (resolved)")
            elif node.market_type == "championship":
                championship_lines.append(f"  {label}: {price:.3f}")
            elif node.market_type == "series":
                series_lines.append(f"  {label}: {price:.3f}")
            elif node.market_type == "player_prop":
                player_prop_lines.append(f"  {label}: {price:.3f}")

        # Skip if no unresolved derivative markets
        if not championship_lines and not series_lines and not player_prop_lines:
            return []

        # Fetch news
        news_items = self._fetch_news(graph.event_label)
        news_section = self._format_news_summary(news_items) if news_items else "No recent news available."

        today = date.today().isoformat()

        prompt = (
            "You are a sports probability analyst.\n\n"
            f"Event: \"{graph.event_label}\"\n"
            f"Sport: {graph.sport}\n"
            f"Today's date: {today}\n\n"
        )

        if championship_lines:
            champ_sum = sum(
                n.market.outcome_prices[0] for n in graph.nodes
                if n.market_type == "championship" and not n.is_resolved and n.market.outcome_prices
            )
            prompt += (
                "CHAMPIONSHIP MARKETS (who wins the title):\n"
                + "\n".join(championship_lines) + "\n"
                + f"  Sum: {champ_sum:.2f} (should be ~1.0)\n\n"
            )

        if series_lines:
            prompt += "SERIES MARKETS:\n" + "\n".join(series_lines) + "\n\n"

        if player_prop_lines:
            prompt += "PLAYER PROP MARKETS:\n" + "\n".join(player_prop_lines) + "\n\n"

        if resolved_lines:
            prompt += "RESOLVED MARKETS (known results):\n" + "\n".join(resolved_lines) + "\n\n"

        prompt += (
            "CONSTRAINTS you must satisfy:\n"
            "- Championship probability <= Series probability for each team\n"
            "- All championship probabilities should sum to ~1.0\n\n"
            f"Recent news:\n{news_section}\n\n"
            "Estimate fair probability for each unresolved market.\n"
            'Return JSON: {"estimates": [{"market_id": "...", "probability": 0.XX}]}\n'
            "Ensure championship prob <= series prob for each team. Return ONLY the JSON."
        )

        try:
            text = self._call_llm(prompt)
            self._call_timestamps.append(time.monotonic())
            return self._parse_derivative_analysis(text, graph)
        except Exception:
            logger.exception("LLM derivative analysis failed for %s", graph.event_label)
            return []

    def _parse_derivative_analysis(self, text: str, graph: SportsEventGraph) -> list[Signal]:
        """Parse LLM derivative estimates and generate divergence signals."""
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            logger.warning("Could not extract JSON from derivative analysis response")
            return []

        try:
            result = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in derivative analysis response")
            return []

        estimates_list = result.get("estimates", [])
        if not estimates_list:
            return []

        llm_estimates: dict[str, float] = {}
        for entry in estimates_list:
            if isinstance(entry, dict) and "market_id" in entry and "probability" in entry:
                try:
                    prob = float(entry["probability"])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(prob):
                    continue
                prob = max(0.0, min(1.0, prob))
                prob = self._extremize(prob)
                llm_estimates[entry["market_id"]] = prob

        signals: list[Signal] = []
        for node in graph.nodes:
            if node.is_resolved:
                continue
            llm_price = llm_estimates.get(node.market.id)
            if llm_price is None:
                continue
            if not node.market.outcome_prices or not node.market.clob_token_ids:
                continue

            market_price = node.market.outcome_prices[0]
            divergence = llm_price - market_price

            if abs(divergence) < self._min_divergence:
                continue
            if market_price < self._min_price:
                continue

            side: Literal["buy", "sell"] = "buy" if divergence > 0 else "sell"
            confidence = 1.0 / (1.0 + math.exp(-20.0 * (abs(divergence) - 0.15)))

            signals.append(Signal(
                strategy=self.name,
                market_id=node.market.id,
                token_id=node.market.clob_token_ids[0],
                side=side,
                confidence=round(confidence, 4),
                target_price=market_price,
                size=self._order_size,
                reason=(
                    f"derivative_divergence: {graph.event_label} "
                    f"{node.team_or_player or node.market.question[:30]} "
                    f"llm={llm_price:.3f} market={market_price:.3f} div={divergence:+.3f}"
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
