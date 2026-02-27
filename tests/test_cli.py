"""Tests for CLI entry point."""

from polymarket_agent import __version__
from polymarket_agent.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"polymarket-agent {__version__}" in result.stdout


def test_cli_version_short():
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert "polymarket-agent" in result.stdout


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "polymarket-agent" in result.stdout.lower() or "Polymarket" in result.stdout


def test_cli_status():
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0


def test_cli_run_live_requires_flag(tmp_path):
    """Live mode without --live flag should exit with error."""
    config_path = tmp_path / "live_config.yaml"
    config_path.write_text("mode: live\nstarting_balance: 1000\npoll_interval: 60\n")
    db_path = tmp_path / "test.db"

    result = runner.invoke(app, ["run", "--config", str(config_path), "--db", str(db_path)])
    assert result.exit_code == 1
    assert "requires the --live flag" in result.stdout
    assert "POLYMARKET_PRIVATE_KEY" not in result.stdout


def test_cli_tick_live_requires_flag(tmp_path):
    """Live mode tick should also require explicit --live confirmation."""
    config_path = tmp_path / "live_config.yaml"
    config_path.write_text("mode: live\nstarting_balance: 1000\npoll_interval: 60\n")
    db_path = tmp_path / "test.db"

    result = runner.invoke(app, ["tick", "--config", str(config_path), "--db", str(db_path)])
    assert result.exit_code == 1
    assert "requires the --live flag" in result.stdout
    assert "POLYMARKET_PRIVATE_KEY" not in result.stdout
