"""Tests for the orchestrator."""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from polymarket_agent.config import AppConfig
from polymarket_agent.data.models import Market
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


def _make_market(*, market_id: str = "100", question: str = "Will it rain?", slug: str = "will-it-rain") -> Market:
    return Market.from_cli(
        {
            "id": market_id,
            "question": question,
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.3","0.7"]',
            "volume": "50000",
            "volume24hr": "12000",
            "liquidity": "5000",
            "active": True,
            "closed": False,
            "slug": slug,
            "clobTokenIds": '["0xtok1","0xtok2"]',
        }
    )


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


def test_orchestrator_exit_manager_generates_sells(mocker: object) -> None:
    """ExitManager should generate sell signals for held positions that hit profit target."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            strategies={},  # no entry strategies
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")

        # Manually seed a position in the executor
        from datetime import datetime, timezone

        orch._executor._positions["0xtok1"] = {
            "market_id": "100",
            "shares": 100.0,
            "avg_price": 0.40,
            "current_price": 0.40,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "entry_strategy": "signal_trader",
        }
        orch._executor._balance = 960.0  # 1000 - 40 cost

        # Mock get_price to return a price above profit target (0.40 * 1.15 = 0.46)
        from polymarket_agent.data.models import Spread

        mock_spread = Spread(token_id="0xtok1", bid=0.50, ask=0.52, spread=0.02)
        mocker.patch.object(orch._data, "get_price", return_value=mock_spread)

        result = orch.tick()

        assert result["trades_executed"] >= 1
        portfolio = orch.get_portfolio()
        # Position should be closed
        assert "0xtok1" not in portfolio.positions


def test_focus_filter_ignores_blank_queries(mocker: object) -> None:
    """Blank focus queries should not filter out all markets."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(mode="paper", strategies={}, focus={"enabled": True, "search_queries": ["   "]})
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        markets = [_make_market()]

        filtered = orch._apply_focus_filter(markets)

        assert len(filtered) == 1
        assert filtered[0].id == "100"


def test_focus_filter_normalizes_slug_and_query_whitespace(mocker: object) -> None:
    """Focus selectors should match even when config values include extra spaces."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            strategies={},
            focus={
                "enabled": True,
                "search_queries": ["  rain  "],
                "market_slugs": ["  WILL-IT-RAIN  "],
            },
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        markets = [_make_market(question="Will it rain?", slug="will-it-rain")]

        filtered = orch._apply_focus_filter(markets)

        assert len(filtered) == 1
        assert filtered[0].id == "100"


def test_focus_filter_does_not_limit_explicit_market_ids(mocker: object) -> None:
    """max_brackets should not truncate explicit ID-targeted focus lists."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        market_ids = [str(i) for i in range(1, 7)]
        config = AppConfig(
            mode="paper",
            strategies={},
            focus={"enabled": True, "market_ids": market_ids, "max_brackets": 2},
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")
        markets = [_make_market(market_id=market_id, question=f"Q {market_id}") for market_id in market_ids]

        filtered = orch._apply_focus_filter(markets)

        assert len(filtered) == len(market_ids)
        assert {market.id for market in filtered} == set(market_ids)


def test_fetch_focus_markets_from_api_skips_malformed_rows() -> None:
    """Gamma fallback should skip malformed market rows instead of dropping the whole response."""
    payload = json.dumps(
        [
            {
                "markets": [
                    {"id": "bad-row"},
                    {
                        "id": "good-1",
                        "question": "Will it rain?",
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["0.3","0.7"]',
                        "volume": "1234",
                        "liquidity": "567",
                        "active": True,
                        "closed": False,
                        "clobTokenIds": '["0xtok1","0xtok2"]',
                    },
                ]
            }
        ]
    ).encode()

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

        def read(self) -> bytes:
            return payload

    with patch("urllib.request.urlopen", return_value=_Response()):
        markets = Orchestrator._fetch_focus_markets_from_api(["will-it-rain"])

    assert len(markets) == 1
    assert markets[0].id == "good-1"
