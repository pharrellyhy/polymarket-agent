"""Integration test — full pipeline with all strategies."""

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
            "question": "Will it rain tomorrow?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.30","0.70"]',
            "volume": "80000",
            "volume24hr": "15000",
            "liquidity": "10000",
            "active": True,
            "closed": False,
            "clobTokenIds": '["0xtok1","0xtok2"]',
            "description": "Weather prediction market",
        },
        {
            "id": "101",
            "question": "Will BTC hit 100k?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.60","0.35"]',
            "volume": "200000",
            "volume24hr": "50000",
            "liquidity": "30000",
            "active": True,
            "closed": False,
            "clobTokenIds": '["0xtok3","0xtok4"]',
        },
    ]
)

MOCK_BOOK = json.dumps(
    {
        "bids": [{"price": "0.28", "size": "500"}],
        "asks": [{"price": "0.32", "size": "500"}],
    }
)


def _mock_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    cmd = " ".join(args)
    if "clob book" in cmd:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=MOCK_BOOK, stderr="")
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=MOCK_MARKETS, stderr="")


def test_full_pipeline_paper_mode(mocker: object) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            strategies={
                "signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05},
                "arbitrageur": {"enabled": True, "price_sum_tolerance": 0.02},
            },
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        result = orch.tick()
        assert result["markets_fetched"] == 2
        assert result["signals_generated"] >= 1
        portfolio = orch.get_portfolio()
        assert portfolio.balance <= 1000.0 or result["trades_executed"] == 0


def test_full_pipeline_monitor_mode(mocker: object) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)  # type: ignore[union-attr]
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="monitor",
            strategies={
                "signal_trader": {"enabled": True, "volume_threshold": 5000, "price_move_threshold": 0.05},
                "arbitrageur": {"enabled": True},
            },
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        result = orch.tick()
        assert result["trades_executed"] == 0
        assert result["signals_generated"] >= 0
