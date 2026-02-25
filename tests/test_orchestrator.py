"""Tests for the orchestrator."""

import json
import subprocess
import tempfile
from pathlib import Path

from polymarket_agent.config import AppConfig
from polymarket_agent.orchestrator import Orchestrator

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


def test_orchestrator_single_tick(mocker: object) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            strategies={"signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05}},
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        result = orch.tick()
        assert "markets_fetched" in result
        assert "signals_generated" in result
        assert "trades_executed" in result


def test_orchestrator_monitor_mode_no_trades(mocker: object) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="monitor",
            strategies={"signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05}},
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        result = orch.tick()
        assert result["trades_executed"] == 0


def test_orchestrator_portfolio(mocker: object) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper", starting_balance=500.0)
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        assert orch.get_portfolio().balance == 500.0
