"""Tests for the evaluate CLI command and its helpers."""

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from polymarket_agent.cli import _analyze_trades, _build_summary, _build_tunable_params, app
from polymarket_agent.config import AppConfig

runner = CliRunner()

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


# ------------------------------------------------------------------
# Helper function tests
# ------------------------------------------------------------------


def test_build_tunable_params_includes_aggregation() -> None:
    cfg = AppConfig(
        strategies={"signal_trader": {"enabled": True, "volume_threshold": 2000, "price_move_threshold": 0.02}},
    )
    params = _build_tunable_params(cfg)
    paths = [p["path"] for p in params]
    assert "aggregation.min_confidence" in paths
    assert "aggregation.min_strategies" in paths


def test_build_tunable_params_includes_strategy_params() -> None:
    cfg = AppConfig(
        strategies={"signal_trader": {"enabled": True, "volume_threshold": 2000, "order_size": 50}},
    )
    params = _build_tunable_params(cfg)
    paths = [p["path"] for p in params]
    assert "strategies.signal_trader.volume_threshold" in paths
    assert "strategies.signal_trader.order_size" in paths


def test_build_tunable_params_respects_minmax() -> None:
    cfg = AppConfig()
    params = _build_tunable_params(cfg)
    for p in params:
        assert "min" in p
        assert "max" in p
        assert p["min"] <= p["max"]


def test_build_tunable_params_conditional_orders() -> None:
    cfg = AppConfig()
    cfg.conditional_orders.enabled = True
    params = _build_tunable_params(cfg)
    paths = [p["path"] for p in params]
    assert "conditional_orders.default_stop_loss_pct" in paths
    assert "conditional_orders.default_take_profit_pct" in paths


def test_build_tunable_params_no_conditional_orders_when_disabled() -> None:
    cfg = AppConfig()
    cfg.conditional_orders.enabled = False
    params = _build_tunable_params(cfg)
    paths = [p["path"] for p in params]
    assert "conditional_orders.default_stop_loss_pct" not in paths


def test_analyze_trades_empty() -> None:
    result = _analyze_trades([])
    assert result["total"] == 0
    assert result["buys"] == 0
    assert result["sells"] == 0
    assert result["round_trips"] == 0


def test_analyze_trades_basic() -> None:
    trades = [
        {"side": "buy", "size": "10"},
        {"side": "buy", "size": "20"},
        {"side": "sell", "size": "15"},
    ]
    result = _analyze_trades(trades)
    assert result["total"] == 3
    assert result["buys"] == 2
    assert result["sells"] == 1
    assert result["round_trips"] == 1
    assert result["avg_size"] == 15.0


def test_build_summary_no_trades() -> None:
    class FakeMetrics:
        total_return = 0.0
        sharpe_ratio = 0.0
        win_rate = 0.0
        max_drawdown = 0.0
        total_trades = 0

    summary = _build_summary(FakeMetrics())
    assert "No trades executed" in summary


def test_build_summary_low_win_rate() -> None:
    class FakeMetrics:
        total_return = 0.02
        sharpe_ratio = 0.8
        win_rate = 0.30
        max_drawdown = 0.05
        total_trades = 10

    summary = _build_summary(FakeMetrics())
    assert "Win rate is low" in summary


def test_build_summary_acceptable_performance() -> None:
    class FakeMetrics:
        total_return = 0.10
        sharpe_ratio = 1.5
        win_rate = 0.60
        max_drawdown = 0.03
        total_trades = 20

    summary = _build_summary(FakeMetrics())
    assert "Diagnostic notes" not in summary


# ------------------------------------------------------------------
# CLI command tests
# ------------------------------------------------------------------


def test_evaluate_command_outputs_json(mocker: object, tmp_path: Path) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)

    config_file = tmp_path / "config.yaml"
    config_file.write_text("mode: paper\nstarting_balance: 1000\nstrategies: {}\n")

    db_file = tmp_path / "test.db"

    result = runner.invoke(app, ["evaluate", "--config", str(config_file), "--db", str(db_file), "--period", "24h"])
    assert result.exit_code == 0

    output = json.loads(result.output)
    assert "metrics" in output
    assert "strategy_breakdown" in output
    assert "trade_analysis" in output
    assert "current_config" in output
    assert "tunable_parameters" in output
    assert "config_file_path" in output
    assert "safety_constraints" in output
    assert "summary" in output
    assert output["safety_constraints"]["mode_locked"] is True


def test_evaluate_command_metrics_structure(mocker: object, tmp_path: Path) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)

    config_file = tmp_path / "config.yaml"
    config_file.write_text("mode: paper\nstarting_balance: 1000\nstrategies: {}\n")

    db_file = tmp_path / "test.db"

    result = runner.invoke(app, ["evaluate", "--config", str(config_file), "--db", str(db_file)])
    assert result.exit_code == 0

    output = json.loads(result.output)
    metrics = output["metrics"]
    assert "total_return" in metrics
    assert "sharpe_ratio" in metrics
    assert "max_drawdown" in metrics
    assert "win_rate" in metrics
    assert "profit_factor" in metrics
    assert "total_trades" in metrics


def test_evaluate_command_text_output(mocker: object, tmp_path: Path) -> None:
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)

    config_file = tmp_path / "config.yaml"
    config_file.write_text("mode: paper\nstarting_balance: 1000\nstrategies: {}\n")

    db_file = tmp_path / "test.db"

    result = runner.invoke(
        app, ["evaluate", "--config", str(config_file), "--db", str(db_file), "--no-json"]
    )
    assert result.exit_code == 0
    assert "Evaluation" in result.output
