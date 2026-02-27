"""Tests for MCP server tools.

Each test mocks the PolymarketData CLI wrapper and exercises the MCP tool
functions directly, verifying that they correctly transform data and handle
error cases.
"""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from polymarket_agent.config import AppConfig
from polymarket_agent.data.client import PolymarketData
from polymarket_agent.data.models import (
    Event,
    Market,
    OrderBook,
    OrderBookLevel,
    Position,
    PricePoint,
    Spread,
    Trader,
    Volume,
)
from polymarket_agent.execution.base import Order, Portfolio
from polymarket_agent.mcp_server import (
    AppContext,
    analyze_market,
    cancel_conditional_order,
    create_conditional_order,
    get_conditional_orders,
    get_event,
    get_leaderboard,
    get_market_detail,
    get_portfolio,
    get_portfolio_snapshots,
    get_positions,
    get_price,
    get_price_history,
    get_signal_log,
    get_signals,
    get_spread,
    get_strategy_performance,
    get_volume,
    health_check,
    place_trade,
    refresh_signals,
    search_markets,
)
from polymarket_agent.orders import ConditionalOrder, OrderStatus, OrderType
from polymarket_agent.orchestrator import Orchestrator
from polymarket_agent.strategies.ai_analyst import AIAnalyst
from polymarket_agent.strategies.base import Signal

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

_MARKET_A = Market(
    id="100",
    question="Will it rain tomorrow?",
    outcomes=["Yes", "No"],
    outcome_prices=[0.6, 0.4],
    volume=50000,
    liquidity=5000,
    active=True,
    closed=False,
    clob_token_ids=["0xtok1", "0xtok2"],
    volume_24h=1200,
    description="A weather prediction market",
)

_MARKET_B = Market(
    id="200",
    question="Will BTC hit 100k?",
    outcomes=["Yes", "No"],
    outcome_prices=[0.3, 0.7],
    volume=120000,
    liquidity=15000,
    active=True,
    closed=False,
    clob_token_ids=["0xtok3", "0xtok4"],
    volume_24h=8000,
)

_ORDERBOOK = OrderBook(
    asks=[OrderBookLevel(price=0.65, size=200)],
    bids=[OrderBookLevel(price=0.55, size=100)],
)


def _make_ctx(
    *,
    markets: list[Market] | None = None,
    mode: str = "paper",
    strategies: list[Any] | None = None,
) -> AppContext:
    """Build a minimal AppContext with mocked dependencies."""
    if markets is None:
        markets = [_MARKET_A, _MARKET_B]

    market_by_id = {m.id: m for m in markets}

    data = MagicMock(spec=PolymarketData)
    data.get_active_markets.return_value = markets
    data.get_market.side_effect = market_by_id.get
    data.search_markets.return_value = markets
    data.get_orderbook.return_value = _ORDERBOOK
    data.get_price_history.return_value = [
        PricePoint(timestamp="2026-01-01T00:00:00Z", price=0.5),
        PricePoint(timestamp="2026-01-02T00:00:00Z", price=0.55),
    ]
    data.get_leaderboard.return_value = [
        Trader(rank=1, name="TopTrader", volume=500000, pnl=25000, markets_traded=42),
        Trader(rank=2, name="Runner", volume=300000, pnl=15000, markets_traded=30),
    ]

    config = AppConfig(mode=mode)  # type: ignore[arg-type]

    orch = MagicMock(spec=Orchestrator)
    orch.data = data
    orch.strategies = strategies or []
    orch.get_portfolio.return_value = Portfolio(balance=950.0, positions={"100": {"shares": 10, "avg_price": 0.6}})
    orch.get_recent_trades.return_value = [{"strategy": "signal_trader", "side": "buy", "size": 50.0}]
    orch.get_cached_signals.return_value = []
    orch.get_cached_signals_updated_at.return_value = None

    return AppContext(orchestrator=orch, data=data, config=config)


def _patch_ctx(ctx: AppContext) -> Any:
    """Patch _get_ctx() to return our test AppContext."""
    return patch("polymarket_agent.mcp_server._get_ctx", return_value=ctx)


# ------------------------------------------------------------------
# search_markets
# ------------------------------------------------------------------


class TestSearchMarkets:
    def test_returns_matching_markets(self) -> None:
        """search_markets delegates to data.search_markets and formats results."""
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            results = search_markets("rain")
        assert len(results) == 2
        assert results[0]["id"] == "100"
        ctx.data.search_markets.assert_called_once_with("rain", limit=25)

    def test_respects_limit(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            search_markets("test", limit=5)
        ctx.data.search_markets.assert_called_once_with("test", limit=5)


# ------------------------------------------------------------------
# get_market_detail
# ------------------------------------------------------------------


class TestGetMarketDetail:
    def test_returns_detail_with_orderbook(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            result = get_market_detail("100")
        assert result["id"] == "100"
        assert result["description"] == "A weather prediction market"
        assert result["orderbook"]["best_bid"] == 0.55
        assert result["orderbook"]["best_ask"] == 0.65

    def test_not_found(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            result = get_market_detail("999")
        assert "error" in result

    def test_orderbook_failure_handled(self) -> None:
        """If orderbook fetch fails, we get an error sub-dict, not a crash."""
        ctx = _make_ctx()
        ctx.data.get_orderbook.side_effect = RuntimeError("CLI failed")
        with _patch_ctx(ctx):
            result = get_market_detail("100")
        assert result["orderbook"]["error"] == "Failed to fetch orderbook"


# ------------------------------------------------------------------
# get_price_history
# ------------------------------------------------------------------


class TestGetPriceHistory:
    def test_returns_points(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            result = get_price_history("0xtok1")
        assert len(result) == 2
        assert result[0]["price"] == 0.5
        ctx.data.get_price_history.assert_called_once_with("0xtok1", interval="1d")


# ------------------------------------------------------------------
# get_leaderboard
# ------------------------------------------------------------------


class TestGetLeaderboard:
    def test_returns_traders(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            result = get_leaderboard()
        assert len(result) == 2
        assert result[0]["name"] == "TopTrader"
        assert result[0]["rank"] == 1
        ctx.data.get_leaderboard.assert_called_once_with(period="month")

    def test_custom_period(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            get_leaderboard(period="week")
        ctx.data.get_leaderboard.assert_called_once_with(period="week")


# ------------------------------------------------------------------
# get_portfolio
# ------------------------------------------------------------------


class TestGetPortfolio:
    def test_returns_portfolio_state(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            result = get_portfolio()
        assert result["balance"] == 950.0
        assert result["total_value"] > 0
        assert len(result["recent_trades"]) == 1


# ------------------------------------------------------------------
# get_signals
# ------------------------------------------------------------------


class TestGetSignals:
    def test_returns_cached_signals_without_recompute(self) -> None:
        ctx = _make_ctx()
        cached_at = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)
        ctx.orchestrator.get_cached_signals.return_value = [
            Signal(
                strategy="test",
                market_id="100",
                token_id="0xtok1",
                side="buy",
                confidence=0.8,
                target_price=0.6,
                size=25.0,
                reason="test signal",
            )
        ]
        ctx.orchestrator.get_cached_signals_updated_at.return_value = cached_at

        with _patch_ctx(ctx):
            result = get_signals()
        assert result["source"] == "cache"
        assert result["last_updated"] == cached_at.isoformat()
        assert result["freshness_seconds"] is not None
        assert len(result["signals"]) == 1
        assert result["signals"][0]["strategy"] == "test"
        assert result["signals"][0]["confidence"] == 0.8
        ctx.orchestrator.generate_signals.assert_not_called()

    def test_returns_empty_snapshot_when_not_computed_yet(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            result = get_signals()
        assert result["source"] == "cache"
        assert result["last_updated"] is None
        assert result["freshness_seconds"] is None
        assert result["signals"] == []
        ctx.orchestrator.generate_signals.assert_not_called()


# ------------------------------------------------------------------
# refresh_signals
# ------------------------------------------------------------------


class TestRefreshSignals:
    def test_recomputes_signals_explicitly(self) -> None:
        ctx = _make_ctx()
        refreshed_at = datetime(2026, 2, 26, 12, 30, 0, tzinfo=timezone.utc)
        ctx.orchestrator.generate_signals.return_value = [
            Signal(
                strategy="ai_analyst",
                market_id="100",
                token_id="0xtok1",
                side="buy",
                confidence=0.9,
                target_price=0.61,
                size=10.0,
                reason="fresh signal",
            )
        ]
        ctx.orchestrator.get_cached_signals_updated_at.return_value = refreshed_at

        with _patch_ctx(ctx):
            result = refresh_signals()

        assert result["source"] == "refresh"
        assert result["last_updated"] == refreshed_at.isoformat()
        assert len(result["signals"]) == 1
        assert result["signals"][0]["strategy"] == "ai_analyst"
        ctx.orchestrator.generate_signals.assert_called_once()


# ------------------------------------------------------------------
# place_trade
# ------------------------------------------------------------------


class TestPlaceTrade:
    def test_successful_trade(self) -> None:
        ctx = _make_ctx(mode="paper")
        ctx.orchestrator.place_order.return_value = Order(
            market_id="100",
            token_id="0xtok1",
            side="buy",
            price=0.6,
            size=25.0,
            shares=41.67,
        )
        with _patch_ctx(ctx):
            result = place_trade("100", "0xtok1", "buy", 25.0, 0.6)
        assert result["status"] == "filled"
        assert result["shares"] == 41.67

    def test_invalid_side(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            result = place_trade("100", "0xtok1", "hold", 25.0, 0.6)
        assert "error" in result

    def test_monitor_mode_blocks_trade(self) -> None:
        ctx = _make_ctx(mode="monitor")
        with _patch_ctx(ctx):
            result = place_trade("100", "0xtok1", "buy", 25.0, 0.6)
        assert result["error"] == "Trading is disabled in monitor mode"

    def test_rejects_zero_price(self) -> None:
        ctx = _make_ctx(mode="paper")
        with _patch_ctx(ctx):
            result = place_trade("100", "0xtok1", "buy", 25.0, 0.0)
        assert "Price must be" in result["error"]
        ctx.orchestrator.place_order.assert_not_called()

    def test_rejects_non_positive_size(self) -> None:
        ctx = _make_ctx(mode="paper")
        with _patch_ctx(ctx):
            result = place_trade("100", "0xtok1", "buy", 0.0, 0.6)
        assert result["error"] == "Size must be greater than 0"
        ctx.orchestrator.place_order.assert_not_called()

    def test_unfilled_order(self) -> None:
        ctx = _make_ctx(mode="paper")
        ctx.orchestrator.place_order.return_value = None
        with _patch_ctx(ctx):
            result = place_trade("100", "0xtok1", "buy", 25.0, 0.99)
        assert result["error"] == "Order could not be filled"


# ------------------------------------------------------------------
# analyze_market
# ------------------------------------------------------------------


class TestAnalyzeMarket:
    def test_no_analyst_enabled(self) -> None:
        ctx = _make_ctx(strategies=[])
        with _patch_ctx(ctx):
            result = analyze_market("100")
        assert result["error"] == "AIAnalyst strategy is not enabled in config"

    def test_no_api_key(self) -> None:
        analyst = MagicMock(spec=AIAnalyst)
        analyst._client = None

        ctx = _make_ctx(strategies=[analyst])
        with _patch_ctx(ctx):
            result = analyze_market("100")
        assert "ANTHROPIC_API_KEY" in result["error"]

    def test_market_not_found(self) -> None:
        analyst = MagicMock(spec=AIAnalyst)
        analyst._client = MagicMock()

        ctx = _make_ctx(strategies=[analyst])
        with _patch_ctx(ctx):
            result = analyze_market("999")
        assert "not found" in result["error"]

    def test_no_divergence(self) -> None:
        analyst = MagicMock(spec=AIAnalyst)
        analyst._client = MagicMock()
        analyst.analyze.return_value = []

        ctx = _make_ctx(strategies=[analyst])
        with _patch_ctx(ctx):
            result = analyze_market("100")
        assert "No divergence" in result["result"]

    def test_with_signal(self) -> None:
        analyst = MagicMock(spec=AIAnalyst)
        analyst._client = MagicMock()
        analyst.analyze.return_value = [
            Signal(
                strategy="ai_analyst",
                market_id="100",
                token_id="0xtok1",
                side="buy",
                confidence=0.7,
                target_price=0.6,
                size=25.0,
                reason="ai_estimate=0.80, market=0.60, div=+0.20",
            )
        ]

        ctx = _make_ctx(strategies=[analyst])
        with _patch_ctx(ctx):
            result = analyze_market("100")
        assert result["signal"]["strategy"] == "ai_analyst"
        assert result["signal"]["confidence"] == 0.7


# ------------------------------------------------------------------
# get_event
# ------------------------------------------------------------------


class TestGetEvent:
    def test_returns_event(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_event.return_value = Event(
            id="500",
            title="Test Event",
            description="An event",
            active=True,
            closed=False,
            volume=100000,
            volume_24h=5000,
            liquidity=20000,
        )
        with _patch_ctx(ctx):
            result = get_event("500")
        assert result["id"] == "500"
        assert result["title"] == "Test Event"
        assert result["volume"] == 100000
        ctx.data.get_event.assert_called_once_with("500")

    def test_not_found(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_event.return_value = None
        with _patch_ctx(ctx):
            result = get_event("999")
        assert "error" in result

    def test_error_handled(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_event.side_effect = RuntimeError("CLI failed")
        with _patch_ctx(ctx):
            result = get_event("bad_event")
        assert "error" in result


# ------------------------------------------------------------------
# get_price
# ------------------------------------------------------------------


class TestGetPrice:
    def test_returns_bid_ask_spread(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_price.return_value = Spread(token_id="0xtok1", bid=0.55, ask=0.65, spread=0.10)
        with _patch_ctx(ctx):
            result = get_price("0xtok1")
        assert result["bid"] == 0.55
        assert result["ask"] == 0.65
        assert result["spread"] == 0.10
        ctx.data.get_price.assert_called_once_with("0xtok1")

    def test_error_handled(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_price.side_effect = RuntimeError("CLI failed")
        with _patch_ctx(ctx):
            result = get_price("bad_token")
        assert "error" in result


# ------------------------------------------------------------------
# get_spread
# ------------------------------------------------------------------


class TestGetSpread:
    def test_returns_spread(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_spread.return_value = Spread(token_id="0xtok1", spread=0.05)
        with _patch_ctx(ctx):
            result = get_spread("0xtok1")
        assert result["spread"] == 0.05
        ctx.data.get_spread.assert_called_once_with("0xtok1")

    def test_error_handled(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_spread.side_effect = RuntimeError("CLI failed")
        with _patch_ctx(ctx):
            result = get_spread("bad_token")
        assert "error" in result


# ------------------------------------------------------------------
# get_volume
# ------------------------------------------------------------------


class TestGetVolume:
    def test_returns_volume(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_volume.return_value = Volume(event_id="500", total=100000.0)
        with _patch_ctx(ctx):
            result = get_volume("500")
        assert result["event_id"] == "500"
        assert result["total"] == 100000.0
        ctx.data.get_volume.assert_called_once_with("500")

    def test_error_handled(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_volume.side_effect = RuntimeError("CLI failed")
        with _patch_ctx(ctx):
            result = get_volume("bad_event")
        assert "error" in result


# ------------------------------------------------------------------
# get_positions
# ------------------------------------------------------------------


class TestGetPositions:
    def test_returns_positions(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_positions.return_value = [
            Position(market="0xabc", outcome="Yes", shares=50.0, avg_price=0.4, current_price=0.6, pnl=10.0),
            Position(market="0xdef", outcome="No", shares=100.0, avg_price=0.7, current_price=0.65, pnl=-5.0),
        ]
        with _patch_ctx(ctx):
            result = get_positions("0xdeadbeef")
        assert len(result) == 2
        assert result[0]["market"] == "0xabc"
        assert result[0]["shares"] == 50.0
        assert result[1]["pnl"] == -5.0
        ctx.data.get_positions.assert_called_once_with("0xdeadbeef", limit=25)

    def test_empty_positions(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_positions.return_value = []
        with _patch_ctx(ctx):
            result = get_positions("0x0000")
        assert result == []

    def test_error_handled(self) -> None:
        ctx = _make_ctx()
        ctx.data.get_positions.side_effect = RuntimeError("CLI failed")
        with _patch_ctx(ctx):
            result = get_positions("bad_addr")
        assert len(result) == 1
        assert "error" in result[0]


# ------------------------------------------------------------------
# health_check
# ------------------------------------------------------------------


class TestHealthCheck:
    def test_returns_status(self) -> None:
        ctx = _make_ctx()
        with _patch_ctx(ctx):
            result = health_check()
        assert result["status"] == "ok"
        assert result["mode"] == "paper"
        assert isinstance(result["version"], str)
        assert isinstance(result["strategy_count"], int)


# ------------------------------------------------------------------
# Conditional order MCP tools
# ------------------------------------------------------------------


def _make_ctx_with_db(
    *,
    mode: str = "paper",
    orders: list[ConditionalOrder] | None = None,
) -> AppContext:
    """Build AppContext with a mock DB for conditional order testing."""
    ctx = _make_ctx(mode=mode)
    mock_db = MagicMock()
    mock_db.get_active_conditional_orders.return_value = orders or []
    mock_db.create_conditional_order.return_value = 42
    ctx.orchestrator.db = mock_db
    return ctx


class TestGetConditionalOrders:
    def test_returns_empty_list(self) -> None:
        ctx = _make_ctx_with_db()
        with _patch_ctx(ctx):
            result = get_conditional_orders()
        assert result == []

    def test_returns_active_orders(self) -> None:
        order = ConditionalOrder(
            id=1, token_id="0xtok1", market_id="100",
            order_type=OrderType.STOP_LOSS, status=OrderStatus.ACTIVE,
            trigger_price=0.45, size=25.0, parent_strategy="test",
            reason="Auto SL", created_at="2026-01-01T00:00:00",
        )
        ctx = _make_ctx_with_db(orders=[order])
        with _patch_ctx(ctx):
            result = get_conditional_orders()
        assert len(result) == 1
        assert result[0]["order_type"] == "stop_loss"
        assert result[0]["trigger_price"] == 0.45


class TestCancelConditionalOrder:
    def test_cancel_existing(self) -> None:
        order = ConditionalOrder(
            id=5, token_id="0xtok1", market_id="100",
            order_type=OrderType.STOP_LOSS, status=OrderStatus.ACTIVE,
            trigger_price=0.45, size=25.0, parent_strategy="test",
            reason="SL",
        )
        ctx = _make_ctx_with_db(orders=[order])
        with _patch_ctx(ctx):
            result = cancel_conditional_order(5)
        assert result["status"] == "cancelled"
        assert result["order_id"] == 5

    def test_cancel_nonexistent(self) -> None:
        ctx = _make_ctx_with_db()
        with _patch_ctx(ctx):
            result = cancel_conditional_order(999)
        assert "error" in result


class TestCreateConditionalOrder:
    def test_create_stop_loss(self) -> None:
        ctx = _make_ctx_with_db()
        with _patch_ctx(ctx):
            result = create_conditional_order("0xtok1", "100", "stop_loss", 0.45, 25.0)
        assert result["status"] == "created"
        assert result["order_id"] == 42

    def test_create_trailing_stop(self) -> None:
        ctx = _make_ctx_with_db()
        with _patch_ctx(ctx):
            result = create_conditional_order("0xtok1", "100", "trailing_stop", 0.60, 25.0, trail_percent=0.05)
        assert result["status"] == "created"

    def test_invalid_order_type(self) -> None:
        ctx = _make_ctx_with_db()
        with _patch_ctx(ctx):
            result = create_conditional_order("0xtok1", "100", "invalid_type", 0.45, 25.0)
        assert "error" in result

    def test_trailing_stop_requires_trail_percent(self) -> None:
        ctx = _make_ctx_with_db()
        with _patch_ctx(ctx):
            result = create_conditional_order("0xtok1", "100", "trailing_stop", 0.60, 25.0)
        assert "trail_percent" in result["error"]

    def test_rejects_zero_size(self) -> None:
        ctx = _make_ctx_with_db()
        with _patch_ctx(ctx):
            result = create_conditional_order("0xtok1", "100", "stop_loss", 0.45, 0.0)
        assert "error" in result


# ------------------------------------------------------------------
# Monitoring MCP tools
# ------------------------------------------------------------------


class TestGetSignalLog:
    def test_returns_empty(self) -> None:
        ctx = _make_ctx_with_db()
        ctx.orchestrator.db.get_signal_log.return_value = []
        with _patch_ctx(ctx):
            result = get_signal_log()
        assert result == []

    def test_returns_entries(self) -> None:
        ctx = _make_ctx_with_db()
        ctx.orchestrator.db.get_signal_log.return_value = [
            {"id": 1, "strategy": "test", "market_id": "m1", "token_id": "t1",
             "side": "buy", "confidence": 0.8, "size": 25.0, "status": "generated",
             "timestamp": "2026-01-01T00:00:00"},
        ]
        with _patch_ctx(ctx):
            result = get_signal_log()
        assert len(result) == 1
        assert result[0]["strategy"] == "test"


class TestGetPortfolioSnapshots:
    def test_returns_empty(self) -> None:
        ctx = _make_ctx_with_db()
        ctx.orchestrator.db.get_portfolio_snapshots.return_value = []
        with _patch_ctx(ctx):
            result = get_portfolio_snapshots()
        assert result == []

    def test_returns_snapshots(self) -> None:
        ctx = _make_ctx_with_db()
        ctx.orchestrator.db.get_portfolio_snapshots.return_value = [
            {"id": 1, "balance": 1000.0, "total_value": 1050.0,
             "positions_json": "{}", "timestamp": "2026-01-01T00:00:00"},
        ]
        with _patch_ctx(ctx):
            result = get_portfolio_snapshots()
        assert len(result) == 1
        assert result[0]["balance"] == 1000.0


class TestGetStrategyPerformance:
    def test_empty_data(self) -> None:
        ctx = _make_ctx_with_db()
        ctx.orchestrator.db.get_signal_log.return_value = []
        ctx.orchestrator.db.get_trades.return_value = []
        with _patch_ctx(ctx):
            result = get_strategy_performance()
        assert result == {}

    def test_aggregates_signals_and_trades(self) -> None:
        ctx = _make_ctx_with_db()
        ctx.orchestrator.db.get_signal_log.return_value = [
            {"strategy": "signal_trader", "status": "generated"},
            {"strategy": "signal_trader", "status": "executed"},
            {"strategy": "arb", "status": "generated"},
        ]
        ctx.orchestrator.db.get_trades.return_value = [
            {"strategy": "signal_trader", "size": 25.0},
            {"strategy": "signal_trader", "size": 30.0},
        ]
        with _patch_ctx(ctx):
            result = get_strategy_performance()
        assert result["signal_trader"]["signals_generated"] == 2
        assert result["signal_trader"]["signals_executed"] == 1
        assert result["signal_trader"]["trade_volume"] == 55.0
        assert result["arb"]["signals_generated"] == 1
        assert result["arb"]["signals_executed"] == 0
