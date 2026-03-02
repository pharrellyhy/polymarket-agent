"""WhaleFollower strategy — follow top-ranked traders' moves.

Uses the CLI leaderboard to identify top traders, then queries the
Gamma API for their recent activity. When a top trader makes a trade
above the configured minimum size, a follow signal is emitted.
"""

import logging
from typing import Any, Literal

from polymarket_agent.data.gamma_client import GammaClient
from polymarket_agent.data.models import Market, WhaleTrade
from polymarket_agent.data.provider import DataProvider
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_TOP_N = 10
_DEFAULT_MIN_TRADE_SIZE = 500.0
_DEFAULT_ORDER_SIZE = 25.0
_DEFAULT_LEADERBOARD_PERIOD = "month"
_DEFAULT_GAMMA_CACHE_TTL = 120.0


class WhaleFollower(Strategy):
    """Follow trades from top-ranked Polymarket traders.

    Emits signals when top-N leaderboard traders make large trades.
    Confidence is inversely proportional to rank (rank 1 = 1.0).
    Deduplicates to avoid repeated signals for the same trader/market.
    """

    name: str = "whale_follower"

    def __init__(self) -> None:
        self._top_n: int = _DEFAULT_TOP_N
        self._min_trade_size: float = _DEFAULT_MIN_TRADE_SIZE
        self._order_size: float = _DEFAULT_ORDER_SIZE
        self._leaderboard_period: str = _DEFAULT_LEADERBOARD_PERIOD
        self._gamma: GammaClient = GammaClient(cache_ttl=_DEFAULT_GAMMA_CACHE_TTL)
        self._seen: set[str] = set()  # "trader_name:market_id" dedup keys

    def configure(self, config: dict[str, Any]) -> None:
        self._top_n = int(config.get("top_n", _DEFAULT_TOP_N))
        self._min_trade_size = float(config.get("min_trade_size", _DEFAULT_MIN_TRADE_SIZE))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))
        self._leaderboard_period = config.get("leaderboard_period", _DEFAULT_LEADERBOARD_PERIOD)
        gamma_ttl = float(config.get("gamma_cache_ttl", _DEFAULT_GAMMA_CACHE_TTL))
        self._gamma = GammaClient(cache_ttl=gamma_ttl)

    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        traders = data.get_leaderboard(period=self._leaderboard_period)
        top_traders = traders[: self._top_n]

        if not top_traders:
            logger.debug("WhaleFollower: no traders from leaderboard")
            return []

        # Build a lookup from market ID to market for active markets
        market_lookup: dict[str, Market] = {m.id: m for m in markets if m.active and not m.closed}

        whale_trades = self._fetch_whale_trades(top_traders)
        return self._generate_signals(whale_trades, market_lookup)

    def _fetch_whale_trades(self, traders: list[Any]) -> list[WhaleTrade]:
        """Fetch recent activity for each top trader from the Gamma API."""
        all_trades: list[WhaleTrade] = []
        for trader in traders:
            address = getattr(trader, "address", "") or getattr(trader, "name", "")
            if not address:
                continue
            activities = self._gamma.get_trader_activity(address)
            trades = self._gamma.parse_whale_trades(
                activities,
                trader_name=trader.name,
                trader_address=address,
                rank=trader.rank,
                min_size=self._min_trade_size,
            )
            all_trades.extend(trades)
        return all_trades

    def _generate_signals(
        self,
        whale_trades: list[WhaleTrade],
        market_lookup: dict[str, Market],
    ) -> list[Signal]:
        """Convert whale trades into follow signals, with deduplication."""
        signals: list[Signal] = []
        for trade in whale_trades:
            market = market_lookup.get(trade.market_id)
            if market is None:
                continue

            if not market.clob_token_ids:
                continue

            dedup_key = f"{trade.trader_name}:{trade.market_id}"
            if dedup_key in self._seen:
                continue
            self._seen.add(dedup_key)

            # Confidence: inversely proportional to rank (rank 1 = 1.0, rank 10 = 0.55)
            confidence = max(0.1, 1.0 - (trade.rank - 1) * 0.05)

            token_id = trade.token_id or market.clob_token_ids[0]
            target_price = market.outcome_prices[0] if market.outcome_prices else 0.5
            side: Literal["buy", "sell"] = "sell" if trade.side == "sell" else "buy"

            signals.append(
                Signal(
                    strategy=self.name,
                    market_id=trade.market_id,
                    token_id=token_id,
                    side=side,
                    confidence=round(confidence, 4),
                    target_price=target_price,
                    size=self._order_size * confidence,
                    reason=(
                        f"whale_follow: {trade.trader_name} (rank {trade.rank}) " f"{trade.side} ${trade.size:.0f}"
                    ),
                )
            )
        return signals
