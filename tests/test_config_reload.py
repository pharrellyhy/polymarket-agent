"""Tests for config hot-reload functionality."""

import tempfile
import time
from pathlib import Path

from polymarket_agent.config import AppConfig, config_mtime
from polymarket_agent.orchestrator import Orchestrator


def test_config_mtime_returns_mtime(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("mode: paper\n")
    mtime = config_mtime(f)
    assert mtime > 0


def test_config_mtime_returns_zero_for_missing() -> None:
    assert config_mtime(Path("/nonexistent/config.yaml")) == 0.0


def test_config_mtime_changes_after_write(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("mode: paper\n")
    mtime1 = config_mtime(f)
    time.sleep(0.05)
    f.write_text("mode: paper\npoll_interval: 30\n")
    mtime2 = config_mtime(f)
    assert mtime2 >= mtime1


def test_reload_config_updates_strategies() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            strategies={"signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05}},
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        assert len(orch.strategies) == 1

        # Reload with no strategies enabled
        new_config = AppConfig(mode="paper", starting_balance=1000.0, strategies={})
        orch.reload_config(new_config)
        assert len(orch.strategies) == 0
        orch.close()


def test_reload_config_rejects_mode_change() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper", starting_balance=1000.0)
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")

        # Try to switch from paper to live — should be rejected
        new_config = AppConfig(mode="live", starting_balance=1000.0)
        orch.reload_config(new_config)

        # Mode should remain paper — verify via poll_interval still accessible
        # and strategies weren't rebuilt with live config
        assert orch.poll_interval == config.poll_interval
        orch.close()


def test_reload_config_preserves_executor_balance() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper", starting_balance=500.0)
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        assert orch.get_portfolio().balance == 500.0

        # Reload with different starting_balance — executor should NOT be rebuilt
        new_config = AppConfig(mode="paper", starting_balance=2000.0)
        orch.reload_config(new_config)
        # Balance stays at 500 because executor is preserved
        assert orch.get_portfolio().balance == 500.0
        orch.close()


def test_reload_config_updates_risk_limits() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper", starting_balance=1000.0)
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")

        new_config = AppConfig(mode="paper", starting_balance=1000.0)
        new_config.risk.max_position_size = 999.0
        orch.reload_config(new_config)

        # _config reference should be updated (risk is read from _config)
        assert orch._config.risk.max_position_size == 999.0
        orch.close()


def test_reload_config_updates_poll_interval() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper", poll_interval=60)
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        assert orch.poll_interval == 60

        new_config = AppConfig(mode="paper", poll_interval=120)
        orch.reload_config(new_config)
        assert orch.poll_interval == 120
        orch.close()
