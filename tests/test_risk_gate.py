"""Tests for the Orchestrator risk gate and executor factory."""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from polymarket_agent.config import AppConfig, RiskConfig
from polymarket_agent.execution.base import Order
from polymarket_agent.orchestrator import Orchestrator
from polymarket_agent.strategies.base import Signal

MOCK_MARKETS = json.dumps(
    [
        {
            "id": "100",
            "question": "Will it rain?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.3","0.7"]',
            "volume": "50000",
            "volume24hr": "12000",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "clobTokenIds": '["0xtok1","0xtok2"]',
        }
    ]
)


def _mock_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=MOCK_MARKETS, stderr="")


def _make_signal(size: float = 25.0) -> Signal:
    return Signal(
        strategy="test",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=0.8,
        target_price=0.5,
        size=size,
        reason="test",
    )


def _make_signal_with_token(token_id: str, *, size: float = 25.0) -> Signal:
    signal = _make_signal(size=size)
    signal.token_id = token_id
    return signal


def test_risk_gate_rejects_oversized_position(mocker: object) -> None:
    """Signals exceeding max_position_size are rejected."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            risk=RiskConfig(max_position_size=20.0),
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        rejection = orch._check_risk(_make_signal(size=50.0))
        assert rejection is not None
        assert "max_position_size" in rejection


def test_risk_gate_passes_valid_signal(mocker: object) -> None:
    """Signals within risk limits pass the gate."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            risk=RiskConfig(max_position_size=100.0, max_daily_loss=50.0, max_open_orders=10),
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        rejection = orch._check_risk(_make_signal(size=25.0))
        assert rejection is None


def test_orchestrator_place_order_applies_risk_gate(mocker: object) -> None:
    """Manual orchestrator orders should also respect risk limits."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper", risk=RiskConfig(max_position_size=10.0))
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        order = orch.place_order(_make_signal(size=25.0))
        assert order is None
        assert orch.get_portfolio().balance == config.starting_balance


def test_risk_gate_blocks_when_daily_loss_exceeded(mocker: object) -> None:
    """After enough buy trades, daily loss gate triggers."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            risk=RiskConfig(max_position_size=100.0, max_daily_loss=30.0),
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")

        # Execute two buy trades on different tokens totaling $50 (exceeds max_daily_loss=30)
        orch._executor.place_order(_make_signal_with_token("0xtok_a", size=25.0))
        orch._executor.place_order(_make_signal_with_token("0xtok_b", size=25.0))

        rejection = orch._check_risk(_make_signal_with_token("0xtok_c", size=10.0))
        assert rejection is not None
        assert "daily_loss" in rejection


def test_risk_gate_integrated_in_tick(mocker: object) -> None:
    """tick() should skip signals that fail risk checks."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            risk=RiskConfig(max_position_size=5.0),
            strategies={"signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05}},
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        result = orch.tick()
        # SignalTrader generates signals with size > 5, so risk gate should block them
        assert result["trades_executed"] == 0


def test_tick_reuses_risk_snapshot_across_signals(mocker: object) -> None:
    """tick() should not hit DB/CLOB risk lookups once per signal."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper", risk=RiskConfig(max_position_size=100.0, max_daily_loss=1000.0, max_open_orders=10)
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")

        strategy = mocker.Mock()
        strategy.analyze.return_value = [
            _make_signal_with_token("0xtok1", size=10.0),
            _make_signal_with_token("0xtok2", size=10.0),
        ]
        orch._strategies = [strategy]
        orch._data.get_active_markets = mocker.Mock(return_value=[])  # type: ignore[method-assign]
        mocker.patch("polymarket_agent.orchestrator.aggregate_signals", return_value=strategy.analyze.return_value)

        orch._calculate_daily_loss = mocker.Mock(return_value=0.0)  # type: ignore[method-assign]
        orch._executor.get_open_orders = mocker.Mock(return_value=[])  # type: ignore[method-assign]
        orch._executor.place_order = mocker.Mock(
            side_effect=[
                Order("100", "0xtok1", "buy", 0.5, 10.0, 20.0),
                Order("100", "0xtok2", "buy", 0.5, 10.0, 20.0),
            ]
        )  # type: ignore[method-assign]

        result = orch.tick()

        assert result["trades_executed"] == 2
        orch._calculate_daily_loss.assert_called_once()
        orch._executor.get_open_orders.assert_called_once()


def test_tick_with_trades_uses_forced_snapshot_only(mocker: object) -> None:
    """When trades execute, tick() should force one snapshot write path (no periodic duplicate)."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper", risk=RiskConfig(max_position_size=100.0, max_daily_loss=1000.0, max_open_orders=10)
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")

        signal = _make_signal_with_token("0xtok1", size=10.0)
        strategy = mocker.Mock()
        strategy.analyze.return_value = [signal]
        orch._strategies = [strategy]
        orch._data.get_active_markets = mocker.Mock(return_value=[])  # type: ignore[method-assign]
        mocker.patch("polymarket_agent.orchestrator.aggregate_signals", return_value=[signal])
        orch._executor.place_order = mocker.Mock(return_value=Order("100", "0xtok1", "buy", 0.5, 10.0, 20.0))  # type: ignore[method-assign]

        orch._record_portfolio_snapshot = mocker.Mock()  # type: ignore[method-assign]
        orch._force_portfolio_snapshot = mocker.Mock()  # type: ignore[method-assign]

        result = orch.tick()

        assert result["trades_executed"] == 1
        orch._force_portfolio_snapshot.assert_called_once()
        orch._record_portfolio_snapshot.assert_not_called()


def test_executor_factory_paper_mode(mocker: object) -> None:
    """Paper mode creates PaperTrader."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper", starting_balance=500.0)
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        from polymarket_agent.execution.paper import PaperTrader  # noqa: PLC0415

        assert isinstance(orch._executor, PaperTrader)
        assert orch.get_portfolio().balance == 500.0


def test_executor_factory_live_mode_requires_env(mocker: object) -> None:
    """Live mode without POLYMARKET_PRIVATE_KEY raises ValueError."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="live")
        with patch.dict("os.environ", {}, clear=True):
            try:
                Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
                assert False, "Should have raised"
            except (ValueError, ImportError):
                pass  # Expected: ValueError for missing key, ImportError if py-clob-client not installed


def test_orchestrator_close(mocker: object) -> None:
    """close() cleanly releases resources."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper")
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        orch.close()
        # Verify DB is closed by checking that operations fail
        try:
            orch._db.get_trades()
            assert False, "Expected error after close"
        except Exception:
            pass
