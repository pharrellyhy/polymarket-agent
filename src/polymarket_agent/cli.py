"""CLI entry point for polymarket-agent."""

import logging
import time
from pathlib import Path
from typing import Annotated

import typer

from polymarket_agent import __version__
from polymarket_agent.config import AppConfig, load_config
from polymarket_agent.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"polymarket-agent {__version__}")
        raise typer.Exit()


app = typer.Typer(name="polymarket-agent", help="Polymarket Agent — agent-friendly auto-trading pipeline")


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", "-V", help="Show version and exit", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    """Polymarket Agent — agent-friendly auto-trading pipeline."""


DEFAULT_CONFIG = Path("config.yaml")
DEFAULT_DB = Path("polymarket_agent.db")

ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Path to config.yaml")]
DbOption = Annotated[Path, typer.Option("--db", help="Path to SQLite database")]


def _load_config(config_path: Path) -> AppConfig:
    """Load config from file, warning if the file does not exist."""
    if config_path.exists():
        return load_config(config_path)
    logger.warning("Config file %s not found, using defaults", config_path)
    return AppConfig()


def _build_orchestrator(config_path: Path, db_path: Path) -> tuple[AppConfig, Orchestrator]:
    """Load config and create an Orchestrator."""
    cfg = _load_config(config_path)
    return cfg, Orchestrator(config=cfg, db_path=db_path)


def _setup_logging(cfg: AppConfig) -> None:
    """Configure logging based on monitoring config."""
    if cfg.monitoring.structured_logging:
        from polymarket_agent.monitoring.logging import setup_structured_logging  # noqa: PLC0415

        log_file = Path(cfg.monitoring.log_file) if cfg.monitoring.log_file else None
        setup_structured_logging(log_file=log_file)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@app.command()
def run(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
    live: Annotated[bool, typer.Option("--live", help="Required confirmation flag for live trading mode")] = False,
) -> None:
    """Run the continuous trading loop."""
    cfg = _load_config(config)
    _setup_logging(cfg)

    if cfg.mode == "live" and not live:
        typer.echo("Live trading requires the --live flag: polymarket-agent run --live")
        raise typer.Exit(code=1)

    orch = Orchestrator(config=cfg, db_path=db)
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
    finally:
        orch.close()


@app.command()
def status(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
) -> None:
    """Show current portfolio and recent trades."""
    cfg, orch = _build_orchestrator(config, db)
    try:
        portfolio = orch.get_portfolio()
        typer.echo(f"Mode: {cfg.mode}")
        typer.echo(f"Balance: ${portfolio.balance:.2f}")
        typer.echo(f"Total Value: ${portfolio.total_value:.2f}")
        typer.echo(f"Positions: {len(portfolio.positions)}")
    finally:
        orch.close()


@app.command()
def tick(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
    live: Annotated[bool, typer.Option("--live", help="Required confirmation flag for live trading mode")] = False,
) -> None:
    """Run a single tick of the trading loop."""
    cfg = _load_config(config)
    _setup_logging(cfg)
    if cfg.mode == "live" and not live:
        typer.echo("Live trading requires the --live flag: polymarket-agent tick --live")
        raise typer.Exit(code=1)

    orch = Orchestrator(config=cfg, db_path=db)
    try:
        result = orch.tick()
        portfolio = orch.get_portfolio()
        typer.echo(
            f"Markets: {result['markets_fetched']}, "
            f"Signals: {result['signals_generated']}, "
            f"Trades: {result['trades_executed']}"
        )
        typer.echo(f"Portfolio: ${portfolio.balance:.2f} cash, ${portfolio.total_value:.2f} total")
    finally:
        orch.close()


@app.command()
def backtest(
    data_dir: Annotated[Path, typer.Argument(help="Directory containing CSV data files")],
    config: ConfigOption = DEFAULT_CONFIG,
    start: Annotated[str | None, typer.Option("--start", help="Start timestamp filter (inclusive)")] = None,
    end: Annotated[str | None, typer.Option("--end", help="End timestamp filter (inclusive)")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write JSON results to file")] = None,
    trades: Annotated[bool, typer.Option("--trades", help="Include individual trades in output")] = False,
) -> None:
    """Run a backtest over historical CSV data."""
    import json as _json  # noqa: PLC0415

    from polymarket_agent.backtest.engine import BacktestEngine  # noqa: PLC0415
    from polymarket_agent.backtest.historical import HistoricalDataProvider  # noqa: PLC0415

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not data_dir.is_dir():
        typer.echo(f"Error: {data_dir} is not a directory")
        raise typer.Exit(code=1)

    cfg = _load_config(config)
    provider = HistoricalDataProvider(data_dir, default_spread=cfg.backtest.default_spread)

    if provider.total_steps == 0:
        typer.echo("No data loaded — check CSV files in the data directory")
        raise typer.Exit(code=1)

    # Build strategies from config
    from polymarket_agent.orchestrator import STRATEGY_REGISTRY  # noqa: PLC0415

    strategies = []
    for name, params in cfg.strategies.items():
        if not params.get("enabled", False):
            continue
        cls = STRATEGY_REGISTRY.get(name)
        if cls is None:
            continue
        instance = cls()
        instance.configure(params)
        strategies.append(instance)

    engine = BacktestEngine(config=cfg, strategies=strategies, data_provider=provider)
    result = engine.run(start=start, end=end)

    typer.echo(f"Backtest complete: {provider.total_steps} data points, {len(provider.unique_timestamps)} time steps")
    typer.echo(f"  Total return:  {result.metrics.total_return:+.2%}")
    typer.echo(f"  Sharpe ratio:  {result.metrics.sharpe_ratio:.2f}")
    typer.echo(f"  Max drawdown:  {result.metrics.max_drawdown:.2%}")
    typer.echo(f"  Win rate:      {result.metrics.win_rate:.2%}")
    typer.echo(f"  Profit factor: {result.metrics.profit_factor:.2f}")
    typer.echo(f"  Total trades:  {result.metrics.total_trades}")

    if output:
        payload = result.to_dict()
        if trades:
            payload["trades"] = result.trades
        output.write_text(_json.dumps(payload, indent=2, default=str))
        typer.echo(f"Results written to {output}")


@app.command()
def dashboard(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
    host: Annotated[str | None, typer.Option("--host", help="Dashboard bind address")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Dashboard port")] = None,
) -> None:
    """Start the monitoring dashboard web server."""
    cfg = _load_config(config)
    _setup_logging(cfg)

    # Fall back to config values when CLI flags are not provided
    resolved_host = host if host is not None else cfg.monitoring.dashboard_host
    resolved_port = port if port is not None else cfg.monitoring.dashboard_port

    from polymarket_agent.dashboard.api import create_app  # noqa: PLC0415

    _, orch = _build_orchestrator(config, db)
    try:
        import uvicorn  # noqa: PLC0415

        fastapi_app = create_app(
            db=orch.db,
            get_portfolio=orch.get_portfolio,
            get_recent_trades=orch.get_recent_trades,
        )
        typer.echo(f"Dashboard starting on http://{resolved_host}:{resolved_port}")
        uvicorn.run(fastapi_app, host=resolved_host, port=resolved_port, log_level="info")
    except ImportError:
        typer.echo("Dashboard requires optional dependencies: pip install polymarket-agent[dashboard]")
        raise typer.Exit(code=1)
    finally:
        orch.close()


@app.command()
def mcp(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
) -> None:
    """Run the MCP server (stdio transport) for AI agent integration."""
    from polymarket_agent.mcp_server import configure  # noqa: PLC0415
    from polymarket_agent.mcp_server import mcp as mcp_server  # noqa: PLC0415

    configure(config_path=config, db_path=db)
    mcp_server.run()
