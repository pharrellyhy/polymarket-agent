"""Tests for CLI entry point."""

from polymarket_agent.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "polymarket-agent" in result.stdout.lower() or "Polymarket" in result.stdout


def test_cli_status():
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
