"""WhaleFollower strategy — follow top-ranked traders' moves.

Uses the CLI leaderboard to identify top traders, then queries their
recent trades via the CLI. When a top trader makes a trade above the
configured minimum size, a follow signal is emitted.
"""

import logging
from typing import Any, Literal

from polymarket_agent.data.models import Market, WhaleTrade
from polymarket_agent.data.provider import DataProvider
from polymarket_agent.strategies.base import Signal, Strategy

logger = logging.getLogger(__name__)

_DEFAULT_TOP_N = 10
_DEFAULT_MIN_TRADE_SIZE = 500.0
_DEFAULT_ORDER_SIZE = 25.0
_DEFAULT_LEADERBOARD_PERIOD = "month"


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
        self._seen: set[str] = set()  # "trader_name:market_id" dedup keys

    def configure(self, config: dict[str, Any]) -> None:
        self._top_n = int(config.get("top_n", _DEFAULT_TOP_N))
        self._min_trade_size = float(config.get("min_trade_size", _DEFAULT_MIN_TRADE_SIZE))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))
        self._leaderboard_period = config.get("leaderboard_period", _DEFAULT_LEADERBOARD_PERIOD)

    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        traders = data.get_leaderboard(period=self._leaderboard_period)
        top_traders = traders[: self._top_n]

        if not top_traders:
            logger.debug("WhaleFollower: no traders from leaderboard")
            return []

        # Build lookups for active markets by ID and slug
        market_by_id: dict[str, Market] = {m.id: m for m in markets if m.active and not m.closed}
        market_by_slug: dict[str, Market] = {m.slug.lower(): m for m in markets if m.active and not m.closed}

        whale_trades = self._fetch_whale_trades(top_traders, data)
        return self._generate_signals(whale_trades, market_by_id, market_by_slug)

    def _fetch_whale_trades(self, traders: list[Any], data: DataProvider) -> list[WhaleTrade]:
        """Fetch recent trades for each top trader via the CLI."""
        all_trades: list[WhaleTrade] = []
        for trader in traders:
            address = getattr(trader, "address", "") or getattr(trader, "name", "")
            if not address:
                continue
            raw_trades = data.get_trader_trades(address, limit=20)
            for t in raw_trades:
                size = float(t.get("size", 0) or 0)
                if size < self._min_trade_size:
                    continue
                side_raw = str(t.get("side", "buy")).lower()
                all_trades.append(
                    WhaleTrade(
                        trader_name=trader.name,
                        trader_address=address,
                        rank=trader.rank,
                        market_id=str(t.get("condition_id", "")),
                        token_id="",
                        side=side_raw if side_raw in ("buy", "sell") else "buy",
                        size=size,
                        price=float(t.get("price", 0) or 0),
                        timestamp=str(t.get("timestamp", "")),
                        slug=str(t.get("slug", "")),
                        outcome_index=int(t.get("outcome_index", 0) or 0),
                        transaction_hash=str(t.get("transaction_hash", "") or ""),
                    )
                )
        return all_trades

    def _generate_signals(
        self,
        whale_trades: list[WhaleTrade],
        market_by_id: dict[str, Market],
        market_by_slug: dict[str, Market],
    ) -> list[Signal]:
        """Convert whale trades into follow signals, with deduplication."""
        signals: list[Signal] = []
        for trade in whale_trades:
            market = market_by_id.get(trade.market_id)
            if market is None:
                slug = getattr(trade, "slug", "")
                if slug:
                    market = market_by_slug.get(slug.lower())
            if market is None:
                continue

            if not market.clob_token_ids:
                continue

            # Deduplicate by event identity (transaction hash or full trade fingerprint)
            if trade.transaction_hash:
                dedup_key = trade.transaction_hash
            else:
                dedup_key = f"{trade.trader_address}:{trade.market_id}:{trade.timestamp}:{trade.price}:{trade.size}:{trade.side}"
            if dedup_key in self._seen:
                continue
            self._seen.add(dedup_key)

            # Confidence: inversely proportional to rank (rank 1 = 1.0, rank 10 = 0.55)
            confidence = max(0.1, 1.0 - (trade.rank - 1) * 0.05)

            target = self._resolve_follow_target(trade, market)
            if target is None:
                continue
            token_id, target_price = target
            side: Literal["buy", "sell"] = "buy"  # always buy (whale sells → buy opposite token)

            signals.append(
                Signal(
                    strategy=self.name,
                    market_id=trade.market_id,
                    token_id=token_id,
                    side=side,
                    confidence=round(confidence, 4),
                    target_price=target_price,
                    size=self._order_size * confidence,
                    reason=(f"whale_follow: {trade.trader_name} (rank {trade.rank}) {trade.side} ${trade.size:.0f}"),
                )
            )
        return signals

    @staticmethod
    def _resolve_follow_target(trade: WhaleTrade, market: Market) -> tuple[str, float] | None:
        """Map a whale trade onto the executable token/price we can buy."""
        if not market.clob_token_ids:
            return None

        max_index = len(market.clob_token_ids) - 1
        idx = max(0, min(trade.outcome_index, max_index))

        # Binary sell trades are followed by buying the complementary outcome.
        if trade.side == "sell" and len(market.clob_token_ids) == 2:
            idx = 1 - idx

        token_id = market.clob_token_ids[idx]
        target_price = market.outcome_prices[idx] if len(market.outcome_prices) > idx else 0.5
        return token_id, target_price
