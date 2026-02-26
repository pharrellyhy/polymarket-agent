"""CLI entry point for polymarket-agent."""

import logging
import time
from pathlib import Path
from typing import Annotated

import typer

from polymarket_agent.config import AppConfig, load_config
from polymarket_agent.orchestrator import Orchestrator

app = typer.Typer(name="polymarket-agent", help="Polymarket Agent — agent-friendly auto-trading pipeline")

DEFAULT_CONFIG = Path("config.yaml")
DEFAULT_DB = Path("polymarket_agent.db")

ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Path to config.yaml")]
DbOption = Annotated[Path, typer.Option("--db", help="Path to SQLite database")]


def _build_orchestrator(config_path: Path, db_path: Path) -> tuple[AppConfig, Orchestrator]:
    """Load config and create an Orchestrator."""
    cfg = load_config(config_path) if config_path.exists() else AppConfig()
    return cfg, Orchestrator(config=cfg, db_path=db_path)


@app.command()
def run(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
) -> None:
    """Run the continuous trading loop."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg, orch = _build_orchestrator(config, db)
    typer.echo(f"Starting polymarket-agent in {cfg.mode} mode (poll every {cfg.poll_interval}s)")
    try:
        while True:
            result = orch.tick()
            portfolio = orch.get_portfolio()
            typer.echo(
                f"[{cfg.mode}] markets={result['markets_fetched']} "
                f"signals={result['signals_generated']} "
                f"trades={result['trades_executed']} "
                f"balance=${portfolio.balance:.2f}"
            )
            time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command()
def status(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
) -> None:
    """Show current portfolio and recent trades."""
    cfg, orch = _build_orchestrator(config, db)
    portfolio = orch.get_portfolio()
    typer.echo(f"Mode: {cfg.mode}")
    typer.echo(f"Balance: ${portfolio.balance:.2f}")
    typer.echo(f"Total Value: ${portfolio.total_value:.2f}")
    typer.echo(f"Positions: {len(portfolio.positions)}")


@app.command()
def tick(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
) -> None:
    """Run a single tick of the trading loop."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _cfg, orch = _build_orchestrator(config, db)
    result = orch.tick()
    portfolio = orch.get_portfolio()
    typer.echo(
        f"Markets: {result['markets_fetched']}, "
        f"Signals: {result['signals_generated']}, "
        f"Trades: {result['trades_executed']}"
    )
    typer.echo(f"Portfolio: ${portfolio.balance:.2f} cash, ${portfolio.total_value:.2f} total")
