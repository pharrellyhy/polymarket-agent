"""Tests for conditional orders: DB schema, CRUD, and orchestrator integration."""

from pathlib import Path
from unittest.mock import patch

from polymarket_agent.config import AppConfig, ConditionalOrderConfig
from polymarket_agent.data.models import Spread
from polymarket_agent.db import Database
from polymarket_agent.orders import ConditionalOrder, OrderStatus, OrderType


# ------------------------------------------------------------------
# DB CRUD
# ------------------------------------------------------------------


class TestConditionalOrderDB:
    def test_create_and_retrieve(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        order_id = db.create_conditional_order(
            token_id="0xtok1",
            market_id="100",
            order_type=OrderType.STOP_LOSS,
            trigger_price=0.45,
            size=25.0,
            parent_strategy="signal_trader",
            reason="Auto stop-loss",
        )
        assert order_id > 0
        orders = db.get_active_conditional_orders()
        assert len(orders) == 1
        assert orders[0].token_id == "0xtok1"
        assert orders[0].order_type == OrderType.STOP_LOSS
        assert orders[0].trigger_price == 0.45
        assert orders[0].status == OrderStatus.ACTIVE
        db.close()

    def test_update_status_to_triggered(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        order_id = db.create_conditional_order(
            token_id="0xtok1",
            market_id="100",
            order_type=OrderType.TAKE_PROFIT,
            trigger_price=0.80,
            size=25.0,
            parent_strategy="signal_trader",
            reason="Auto take-profit",
        )
        db.update_conditional_order(order_id, status=OrderStatus.TRIGGERED)
        orders = db.get_active_conditional_orders()
        assert len(orders) == 0
        db.close()

    def test_update_status_to_cancelled(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        order_id = db.create_conditional_order(
            token_id="0xtok1",
            market_id="100",
            order_type=OrderType.STOP_LOSS,
            trigger_price=0.40,
            size=10.0,
            parent_strategy="test",
            reason="Cancel test",
        )
        db.update_conditional_order(order_id, status=OrderStatus.CANCELLED)
        orders = db.get_active_conditional_orders()
        assert len(orders) == 0
        db.close()

    def test_update_high_watermark(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        order_id = db.create_conditional_order(
            token_id="0xtok1",
            market_id="100",
            order_type=OrderType.TRAILING_STOP,
            trigger_price=0.0,
            size=25.0,
            high_watermark=0.60,
            trail_percent=0.05,
            parent_strategy="signal_trader",
            reason="Trailing stop",
        )
        db.update_high_watermark(order_id, 0.70)
        orders = db.get_active_conditional_orders()
        assert orders[0].high_watermark == 0.70
        db.close()

    def test_multiple_orders(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "test.db")
        db.create_conditional_order(
            token_id="0xtok1", market_id="100", order_type=OrderType.STOP_LOSS,
            trigger_price=0.40, size=10.0, parent_strategy="s1", reason="SL",
        )
        db.create_conditional_order(
            token_id="0xtok2", market_id="200", order_type=OrderType.TAKE_PROFIT,
            trigger_price=0.90, size=20.0, parent_strategy="s2", reason="TP",
        )
        orders = db.get_active_conditional_orders()
        assert len(orders) == 2
        db.close()


# ------------------------------------------------------------------
# Orchestrator conditional order checking
# ------------------------------------------------------------------


class TestConditionalOrderChecking:
    def _make_orchestrator(self, tmp_path: Path, *, enabled: bool = True):
        cfg = AppConfig(
            mode="paper",
            conditional_orders=ConditionalOrderConfig(enabled=enabled),
        )
        with patch("polymarket_agent.orchestrator.PolymarketData"):
            from polymarket_agent.orchestrator import Orchestrator
            orch = Orchestrator(config=cfg, db_path=tmp_path / "test.db")
        return orch

    def test_stop_loss_triggers_sell(self, tmp_path: Path) -> None:
        orch = self._make_orchestrator(tmp_path)
        orch.db.create_conditional_order(
            token_id="0xtok1", market_id="100", order_type=OrderType.STOP_LOSS,
            trigger_price=0.50, size=25.0, parent_strategy="test", reason="SL test",
        )
        # Need a position for the sell to execute
        orch._executor._positions["0xtok1"] = {"market_id": "100", "shares": 100.0, "avg_price": 0.60, "current_price": 0.45}
        # Mock price below trigger
        orch._data.get_price.return_value = Spread(token_id="0xtok1", bid=0.45, ask=0.50, spread=0.05)
        triggered = orch._check_conditional_orders()
        assert triggered == 1
        assert len(orch.db.get_active_conditional_orders()) == 0
        orch.close()

    def test_stop_loss_does_not_trigger_above(self, tmp_path: Path) -> None:
        orch = self._make_orchestrator(tmp_path)
        orch.db.create_conditional_order(
            token_id="0xtok1", market_id="100", order_type=OrderType.STOP_LOSS,
            trigger_price=0.50, size=25.0, parent_strategy="test", reason="SL test",
        )
        orch._data.get_price.return_value = Spread(token_id="0xtok1", bid=0.55, ask=0.60, spread=0.05)
        triggered = orch._check_conditional_orders()
        assert triggered == 0
        assert len(orch.db.get_active_conditional_orders()) == 1
        orch.close()

    def test_take_profit_triggers_sell(self, tmp_path: Path) -> None:
        orch = self._make_orchestrator(tmp_path)
        orch.db.create_conditional_order(
            token_id="0xtok1", market_id="100", order_type=OrderType.TAKE_PROFIT,
            trigger_price=0.80, size=25.0, parent_strategy="test", reason="TP test",
        )
        orch._data.get_price.return_value = Spread(token_id="0xtok1", bid=0.85, ask=0.90, spread=0.05)

        # Need to have a position to sell
        orch._executor._positions["0xtok1"] = {"market_id": "100", "shares": 50.0, "avg_price": 0.50, "current_price": 0.85}

        triggered = orch._check_conditional_orders()
        assert triggered == 1
        orch.close()

    def test_trailing_stop_updates_watermark(self, tmp_path: Path) -> None:
        orch = self._make_orchestrator(tmp_path)
        order_id = orch.db.create_conditional_order(
            token_id="0xtok1", market_id="100", order_type=OrderType.TRAILING_STOP,
            trigger_price=0.0, size=25.0, high_watermark=0.60, trail_percent=0.10,
            parent_strategy="test", reason="Trail test",
        )
        # Price above watermark => update
        orch._data.get_price.return_value = Spread(token_id="0xtok1", bid=0.70, ask=0.75, spread=0.05)
        triggered = orch._check_conditional_orders()
        assert triggered == 0
        orders = orch.db.get_active_conditional_orders()
        assert orders[0].high_watermark == 0.70
        orch.close()

    def test_trailing_stop_triggers_on_drop(self, tmp_path: Path) -> None:
        orch = self._make_orchestrator(tmp_path)
        orch.db.create_conditional_order(
            token_id="0xtok1", market_id="100", order_type=OrderType.TRAILING_STOP,
            trigger_price=0.0, size=25.0, high_watermark=0.80, trail_percent=0.10,
            parent_strategy="test", reason="Trail test",
        )
        # Threshold = 0.80 * (1 - 0.10) = 0.72, bid=0.70 triggers
        orch._data.get_price.return_value = Spread(token_id="0xtok1", bid=0.70, ask=0.75, spread=0.05)

        orch._executor._positions["0xtok1"] = {"market_id": "100", "shares": 50.0, "avg_price": 0.50, "current_price": 0.70}

        triggered = orch._check_conditional_orders()
        assert triggered == 1
        orch.close()

    def test_price_fetch_failure_skips_order(self, tmp_path: Path) -> None:
        orch = self._make_orchestrator(tmp_path)
        orch.db.create_conditional_order(
            token_id="0xtok1", market_id="100", order_type=OrderType.STOP_LOSS,
            trigger_price=0.50, size=25.0, parent_strategy="test", reason="SL test",
        )
        orch._data.get_price.side_effect = RuntimeError("CLI failed")
        triggered = orch._check_conditional_orders()
        assert triggered == 0
        assert len(orch.db.get_active_conditional_orders()) == 1
        orch.close()


# ------------------------------------------------------------------
# Auto-creation of conditional orders
# ------------------------------------------------------------------


class TestAutoConditionalOrders:
    def test_auto_creates_stop_loss_and_take_profit(self, tmp_path: Path) -> None:
        cfg = AppConfig(
            mode="paper",
            conditional_orders=ConditionalOrderConfig(enabled=True),
        )
        with patch("polymarket_agent.orchestrator.PolymarketData"):
            from polymarket_agent.orchestrator import Orchestrator
            orch = Orchestrator(config=cfg, db_path=tmp_path / "test.db")

        from polymarket_agent.strategies.base import Signal
        signal = Signal(
            strategy="test", market_id="100", token_id="0xtok1",
            side="buy", confidence=0.8, target_price=0.60, size=25.0, reason="test",
        )
        orch._auto_create_conditional_orders(signal)
        orders = orch.db.get_active_conditional_orders()
        assert len(orders) == 2
        types = {o.order_type for o in orders}
        assert types == {OrderType.STOP_LOSS, OrderType.TAKE_PROFIT}
        orch.close()

    def test_auto_includes_trailing_stop_when_enabled(self, tmp_path: Path) -> None:
        cfg = AppConfig(
            mode="paper",
            conditional_orders=ConditionalOrderConfig(enabled=True, trailing_stop_enabled=True),
        )
        with patch("polymarket_agent.orchestrator.PolymarketData"):
            from polymarket_agent.orchestrator import Orchestrator
            orch = Orchestrator(config=cfg, db_path=tmp_path / "test.db")

        from polymarket_agent.strategies.base import Signal
        signal = Signal(
            strategy="test", market_id="100", token_id="0xtok1",
            side="buy", confidence=0.8, target_price=0.60, size=25.0, reason="test",
        )
        orch._auto_create_conditional_orders(signal)
        orders = orch.db.get_active_conditional_orders()
        assert len(orders) == 3
        types = {o.order_type for o in orders}
        assert OrderType.TRAILING_STOP in types
        orch.close()

    def test_no_auto_create_on_sell_signal(self, tmp_path: Path) -> None:
        cfg = AppConfig(
            mode="paper",
            conditional_orders=ConditionalOrderConfig(enabled=True),
        )
        with patch("polymarket_agent.orchestrator.PolymarketData"):
            from polymarket_agent.orchestrator import Orchestrator
            orch = Orchestrator(config=cfg, db_path=tmp_path / "test.db")

        from polymarket_agent.strategies.base import Signal
        signal = Signal(
            strategy="test", market_id="100", token_id="0xtok1",
            side="sell", confidence=0.8, target_price=0.60, size=25.0, reason="test",
        )
        orch._auto_create_conditional_orders(signal)
        orders = orch.db.get_active_conditional_orders()
        assert len(orders) == 0
        orch.close()

    def test_signal_stop_loss_overrides_default(self, tmp_path: Path) -> None:
        cfg = AppConfig(
            mode="paper",
            conditional_orders=ConditionalOrderConfig(enabled=True, default_stop_loss_pct=0.10),
        )
        with patch("polymarket_agent.orchestrator.PolymarketData"):
            from polymarket_agent.orchestrator import Orchestrator
            orch = Orchestrator(config=cfg, db_path=tmp_path / "test.db")

        from polymarket_agent.strategies.base import Signal
        signal = Signal(
            strategy="test", market_id="100", token_id="0xtok1",
            side="buy", confidence=0.8, target_price=0.60, size=25.0, reason="test",
            stop_loss=0.40,
        )
        orch._auto_create_conditional_orders(signal)
        orders = orch.db.get_active_conditional_orders()
        sl = next(o for o in orders if o.order_type == OrderType.STOP_LOSS)
        assert sl.trigger_price == 0.40  # from signal, not default (0.60 * 0.90 = 0.54)
        orch.close()
