"""CLI entry point for polymarket-agent."""

import json as _json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import typer

from polymarket_agent import __version__
from polymarket_agent.config import AppConfig, config_mtime, load_config
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
    last_mtime = config_mtime(config)
    try:
        while True:
            # Hot-reload config if file changed on disk
            current_mtime = config_mtime(config)
            if current_mtime != last_mtime and current_mtime > 0:
                try:
                    new_cfg = _load_config(config)
                    orch.reload_config(new_cfg)
                    cfg = new_cfg
                    last_mtime = current_mtime
                except Exception:
                    logger.exception("[reload] Failed to reload config, continuing with previous")

            result = orch.tick()
            portfolio = orch.get_portfolio()
            typer.echo(
                f"[{cfg.mode}] markets={result['markets_fetched']} "
                f"signals={result['signals_generated']} "
                f"trades={result['trades_executed']} "
                f"balance=${portfolio.balance:.2f}"
            )
            time.sleep(orch.poll_interval)
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
    from polymarket_agent.data.client import PolymarketData  # noqa: PLC0415

    cfg, orch = _build_orchestrator(config, db)
    try:
        portfolio = orch.get_portfolio()
        typer.echo(f"Mode: {cfg.mode}")
        typer.echo(f"Balance: ${portfolio.balance:.2f}")
        typer.echo(f"Total Value: ${portfolio.total_value:.2f}")
        typer.echo(f"Positions: {len(portfolio.positions)}")

        if portfolio.positions:
            data = PolymarketData()
            typer.echo(f"\n  {'TOKEN':<14} {'SHARES':>8} {'ENTRY':>8} {'CURRENT':>8} {'P&L':>10} {'P&L%':>8}")
            total_unrealized = 0.0
            for token_id, pos in portfolio.positions.items():
                shares = float(str(pos.get("shares", 0)))
                avg_price = float(str(pos.get("avg_price", 0)))
                if shares <= 0:
                    continue
                try:
                    spread = data.get_price(token_id)
                    current = spread.bid
                except Exception:
                    current = avg_price
                cost = shares * avg_price
                value = shares * current
                pnl = value - cost
                pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
                total_unrealized += pnl
                sign = "+" if pnl >= 0 else ""
                typer.echo(
                    f"  {token_id[:12]:<14} "
                    f"{shares:>8.2f} "
                    f"${avg_price:>7.4f} "
                    f"${current:>7.4f} "
                    f"{sign}${pnl:>8.2f} "
                    f"{sign}{pnl_pct:>6.1f}%"
                )
            sign = "+" if total_unrealized >= 0 else ""
            typer.echo(f"\n  Total Unrealized P&L: {sign}${total_unrealized:,.2f}")
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


def _parse_period(period: str) -> datetime:
    """Parse a period string like '24h', '7d', '30m' into a UTC cutoff datetime."""
    unit = period[-1].lower()
    try:
        value = int(period[:-1])
    except ValueError:
        raise typer.BadParameter(f"Invalid period format: {period!r} (expected e.g. '24h', '7d')") from None
    if unit == "h":
        delta = timedelta(hours=value)
    elif unit == "d":
        delta = timedelta(days=value)
    elif unit == "m":
        delta = timedelta(minutes=value)
    else:
        raise typer.BadParameter(f"Unknown period unit: {unit!r} (expected h, d, or m)")
    return datetime.now(timezone.utc) - delta


@app.command()
def report(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
    period: Annotated[str | None, typer.Option("--period", "-p", help="Time period filter (e.g. 24h, 7d)")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Show performance report with P&L metrics."""
    from polymarket_agent.backtest.metrics import PortfolioSnapshot, compute_metrics  # noqa: PLC0415
    from polymarket_agent.data.client import PolymarketData  # noqa: PLC0415

    cfg, orch = _build_orchestrator(config, db)
    try:
        since: str | None = None
        period_label = "all time"
        if period:
            cutoff = _parse_period(period)
            since = cutoff.strftime("%Y-%m-%d %H:%M:%S")
            period_label = f"last {period}"

        trades = orch.db.get_trades(since=since)
        snapshot_rows = orch.db.get_portfolio_snapshots(limit=10000, since=since)
        snapshot_rows.reverse()  # chronological order for metrics

        snapshots = [
            PortfolioSnapshot(
                timestamp=str(s.get("timestamp", "")),
                balance=float(str(s.get("balance", 0))),
                total_value=float(str(s.get("total_value", 0))),
            )
            for s in snapshot_rows
        ]

        metrics = compute_metrics(trades, snapshots, cfg.starting_balance)

        # Get latest snapshot for position recovery
        latest = orch.db.get_latest_snapshot()
        positions: dict[str, dict[str, object]] = {}
        if latest:
            try:
                raw = _json.loads(str(latest.get("positions_json", "{}")))
                if isinstance(raw, dict):
                    positions = raw
            except (ValueError, TypeError):
                pass

        # Fetch current prices for open positions
        data = PolymarketData()
        position_rows: list[dict[str, object]] = []
        total_unrealized = 0.0
        for token_id, pos in positions.items():
            shares = float(str(pos.get("shares", 0)))
            avg_price = float(str(pos.get("avg_price", 0)))
            if shares <= 0:
                continue
            try:
                spread = data.get_price(token_id)
                current = spread.bid
            except Exception:
                current = avg_price
            cost = shares * avg_price
            value = shares * current
            pnl = value - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
            total_unrealized += pnl
            position_rows.append(
                {
                    "token_id": token_id[:12],
                    "entry": avg_price,
                    "current": current,
                    "shares": shares,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                }
            )

        # Per-strategy breakdown
        strategy_stats: dict[str, dict[str, float | int]] = {}
        for t in trades:
            strat = str(t.get("strategy", "unknown"))
            if strat not in strategy_stats:
                strategy_stats[strat] = {"count": 0, "net": 0.0}
            strategy_stats[strat]["count"] = int(strategy_stats[strat]["count"]) + 1
            size = float(str(t.get("size", 0)))
            if t.get("side") == "sell":
                strategy_stats[strat]["net"] = float(strategy_stats[strat]["net"]) + size
            else:
                strategy_stats[strat]["net"] = float(strategy_stats[strat]["net"]) - size

        if json_output:
            payload = {
                "period": period_label,
                "total_return": metrics.total_return,
                "sharpe_ratio": metrics.sharpe_ratio,
                "max_drawdown": metrics.max_drawdown,
                "win_rate": metrics.win_rate,
                "profit_factor": metrics.profit_factor,
                "total_trades": metrics.total_trades,
                "positions": position_rows,
                "strategy_breakdown": strategy_stats,
            }
            typer.echo(_json.dumps(payload, indent=2, default=str))
            return

        # Portfolio summary from latest snapshot
        balance = float(str(latest.get("balance", 0))) if latest else cfg.starting_balance
        total_value = float(str(latest.get("total_value", 0))) if latest else cfg.starting_balance
        pos_value = total_value - balance

        typer.echo(f"\n=== Performance Report ({period_label}) ===\n")
        typer.echo(f"Portfolio:  ${total_value:,.2f}  ({metrics.total_return:+.2%})")
        typer.echo(f"Cash:       ${balance:,.2f}")
        typer.echo(f"Positions:  ${pos_value:,.2f} ({len(position_rows)} open)\n")

        typer.echo("Metrics:")
        typer.echo(f"  Total Return:   {metrics.total_return:+.2%}")
        typer.echo(f"  Max Drawdown:   {metrics.max_drawdown:.2%}")
        typer.echo(f"  Sharpe Ratio:   {metrics.sharpe_ratio:.2f}")
        round_trips = sum(1 for t in trades if t.get("side") == "sell")
        typer.echo(f"  Win Rate:       {metrics.win_rate:.1%} ({round_trips} round-trips)")
        typer.echo(f"  Profit Factor:  {metrics.profit_factor:.2f}")
        typer.echo(f"  Total Trades:   {metrics.total_trades}\n")

        if position_rows:
            typer.echo("Open Positions:")
            typer.echo(f"  {'TOKEN':<14} {'ENTRY':>8} {'CURRENT':>8} {'P&L':>10} {'P&L%':>8}")
            for p in position_rows:
                typer.echo(
                    f"  {p['token_id']:<14} "
                    f"${p['entry']:>7.4f} "
                    f"${p['current']:>7.4f} "
                    f"{'+' if float(str(p['pnl'])) >= 0 else ''}{float(str(p['pnl'])):>8.2f} "
                    f"{'+' if float(str(p['pnl_pct'])) >= 0 else ''}{float(str(p['pnl_pct'])):>6.1f}%"
                )
            typer.echo(f"  Total Unrealized: ${total_unrealized:+,.2f}\n")

        if strategy_stats:
            typer.echo("Per-Strategy:")
            for strat, stats in strategy_stats.items():
                count = int(stats["count"])
                net = float(stats["net"])
                trade_word = "trade" if count == 1 else "trades"
                typer.echo(f"  {strat}:  {count} {trade_word}, {'+' if net >= 0 else ''}${net:,.2f}")
            typer.echo()

        # Recent trades
        recent = trades[:10]
        if recent:
            typer.echo("Recent Trades (last 10):")
            typer.echo(f"  {'TIME':<20} {'SIDE':<5} {'MARKET':<14} {'PRICE':>8} {'SIZE':>10} {'STRATEGY'}")
            for t in recent:
                ts = str(t.get("timestamp", ""))
                if len(ts) > 19:
                    ts = ts[11:19]
                elif len(ts) > 10:
                    ts = ts[11:]
                typer.echo(
                    f"  {ts:<20} "
                    f"{str(t.get('side', '')):>4} "
                    f" {str(t.get('market_id', ''))[:12]:<14}"
                    f"${float(str(t.get('price', 0))):>7.4f} "
                    f"${float(str(t.get('size', 0))):>8.2f} "
                    f" {str(t.get('strategy', ''))}"
                )
            typer.echo()
    finally:
        orch.close()


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


# ------------------------------------------------------------------
# Evaluate command helpers
# ------------------------------------------------------------------


def _build_tunable_params(cfg: AppConfig) -> list[dict[str, object]]:
    """Enumerate safe-to-tune parameters with path, current value, min/max, and description."""
    params: list[dict[str, object]] = []

    # Strategy-level parameters
    for name, strat_cfg in cfg.strategies.items():
        if "volume_threshold" in strat_cfg:
            params.append(
                {
                    "path": f"strategies.{name}.volume_threshold",
                    "current": strat_cfg["volume_threshold"],
                    "min": 500,
                    "max": 50000,
                    "description": f"Minimum 24h volume for {name} to consider a market",
                }
            )
        if "price_move_threshold" in strat_cfg:
            params.append(
                {
                    "path": f"strategies.{name}.price_move_threshold",
                    "current": strat_cfg["price_move_threshold"],
                    "min": 0.01,
                    "max": 0.20,
                    "description": f"Minimum price deviation from fair value for {name}",
                }
            )
        if "min_price" in strat_cfg:
            params.append(
                {
                    "path": f"strategies.{name}.min_price",
                    "current": strat_cfg["min_price"],
                    "min": 0.01,
                    "max": 0.50,
                    "description": f"Minimum token price filter for {name}",
                }
            )
        if "spread" in strat_cfg:
            params.append(
                {
                    "path": f"strategies.{name}.spread",
                    "current": strat_cfg["spread"],
                    "min": 0.01,
                    "max": 0.20,
                    "description": f"Bid-ask spread target for {name}",
                }
            )
        if "order_size" in strat_cfg:
            params.append(
                {
                    "path": f"strategies.{name}.order_size",
                    "current": strat_cfg["order_size"],
                    "min": 5,
                    "max": 500,
                    "description": f"Default order size (USDC) for {name}",
                }
            )
        if "price_sum_tolerance" in strat_cfg:
            params.append(
                {
                    "path": f"strategies.{name}.price_sum_tolerance",
                    "current": strat_cfg["price_sum_tolerance"],
                    "min": 0.005,
                    "max": 0.05,
                    "description": f"Price sum deviation threshold for {name} arb detection",
                }
            )

    # Aggregation parameters
    params.append(
        {
            "path": "aggregation.min_confidence",
            "current": cfg.aggregation.min_confidence,
            "min": 0.1,
            "max": 0.9,
            "description": "Minimum confidence score to accept a signal",
        }
    )
    params.append(
        {
            "path": "aggregation.min_strategies",
            "current": cfg.aggregation.min_strategies,
            "min": 1,
            "max": 4,
            "description": "Minimum number of strategies that must agree on a signal",
        }
    )

    # Risk parameters
    params.append(
        {
            "path": "risk.max_position_size",
            "current": cfg.risk.max_position_size,
            "min": 10,
            "max": 1000,
            "description": "Maximum size of a single position (USDC)",
        }
    )
    params.append(
        {
            "path": "risk.max_daily_loss",
            "current": cfg.risk.max_daily_loss,
            "min": 10,
            "max": 2000,
            "description": "Maximum daily loss before halting trades (USDC)",
        }
    )

    # Position sizing parameters
    params.append(
        {
            "path": "position_sizing.kelly_fraction",
            "current": cfg.position_sizing.kelly_fraction,
            "min": 0.05,
            "max": 0.50,
            "description": "Fractional Kelly multiplier (lower = more conservative)",
        }
    )
    params.append(
        {
            "path": "position_sizing.max_bet_pct",
            "current": cfg.position_sizing.max_bet_pct,
            "min": 0.01,
            "max": 0.25,
            "description": "Maximum bet as percentage of portfolio value",
        }
    )

    # Conditional order parameters
    if cfg.conditional_orders.enabled:
        params.append(
            {
                "path": "conditional_orders.default_stop_loss_pct",
                "current": cfg.conditional_orders.default_stop_loss_pct,
                "min": 0.05,
                "max": 0.50,
                "description": "Default stop-loss percentage below entry price",
            }
        )
        params.append(
            {
                "path": "conditional_orders.default_take_profit_pct",
                "current": cfg.conditional_orders.default_take_profit_pct,
                "min": 0.05,
                "max": 1.00,
                "description": "Default take-profit percentage above entry price",
            }
        )

    return params


def _analyze_trades(trades: list[dict[str, object]]) -> dict[str, object]:
    """Compute trade analysis: round-trip counts, buy/sell splits, avg sizes."""
    if not trades:
        return {"total": 0, "buys": 0, "sells": 0, "round_trips": 0, "avg_size": 0.0}

    buys = 0
    sells = 0
    total_size = 0.0
    for trade in trades:
        side = trade.get("side")
        if side == "buy":
            buys += 1
        elif side == "sell":
            sells += 1
        total_size += float(str(trade.get("size", 0)))

    # Approximate round-trips as min(buys, sells)
    round_trips = min(buys, sells)

    return {
        "total": len(trades),
        "buys": buys,
        "sells": sells,
        "round_trips": round_trips,
        "avg_size": round(total_size / len(trades), 2),
    }


def _build_summary(metrics: object) -> str:
    """Build a natural-language summary with diagnostic notes."""
    total_return = getattr(metrics, "total_return", 0.0)
    sharpe = getattr(metrics, "sharpe_ratio", 0.0)
    win_rate = getattr(metrics, "win_rate", 0.0)
    max_dd = getattr(metrics, "max_drawdown", 0.0)
    total_trades = getattr(metrics, "total_trades", 0)

    lines = [f"Total return: {total_return:+.1%}"]
    lines.append(f"Sharpe ratio: {sharpe:.2f}")
    lines.append(f"Win rate: {win_rate:.1%}")
    lines.append(f"Max drawdown: {max_dd:.1%}")
    lines.append(f"Total trades: {total_trades}")

    # Diagnostic notes
    notes: list[str] = []
    if total_trades == 0:
        notes.append("No trades executed — check if strategies are enabled and volume thresholds are reachable")
    if win_rate < 0.45 and total_trades > 5:
        notes.append("Win rate is low — consider raising aggregation.min_confidence or tightening entry criteria")
    if sharpe < 0.5 and total_trades > 5:
        notes.append("Sharpe ratio is low — consider reducing position sizes or raising confidence thresholds")
    if max_dd > 0.15:
        notes.append("Drawdown is high — consider lowering risk.max_position_size or enabling tighter stop-losses")
    if total_return < 0 and total_trades > 5:
        notes.append("Negative return — consider reducing risk.max_daily_loss or pausing aggressive strategies")

    if notes:
        lines.append("\nDiagnostic notes:")
        for note in notes:
            lines.append(f"  - {note}")

    return "\n".join(lines)


@app.command()
def autotune(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
    period: Annotated[str, typer.Option("--period", "-p", help="Evaluation period (e.g. 6h, 24h)")] = "6h",
    provider: Annotated[str, typer.Option("--provider", help="LLM provider: anthropic or openai")] = "anthropic",
    model: Annotated[
        str, typer.Option("--model", help="Model name (e.g. claude-sonnet-4-6, gpt-4o)")
    ] = "claude-sonnet-4-6",
    base_url: Annotated[str | None, typer.Option("--base-url", help="Optional API base URL")] = None,
    api_key_env: Annotated[str | None, typer.Option("--api-key-env", help="Env var name for API key")] = None,
) -> None:
    """Run LLM-based auto-tuning of config parameters."""
    from polymarket_agent.autotune import run_autotune  # noqa: PLC0415
    from polymarket_agent.backtest.metrics import PortfolioSnapshot, compute_metrics  # noqa: PLC0415

    provider = provider.strip().lower()
    if provider not in {"anthropic", "openai"}:
        raise typer.BadParameter("provider must be one of: anthropic, openai", param_hint="--provider")

    cfg, orch = _build_orchestrator(config, db)
    try:
        cutoff = _parse_period(period)
        since = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        trades = orch.db.get_trades(since=since)
        snapshot_rows = orch.db.get_portfolio_snapshots(limit=10000, since=since)
        snapshot_rows.reverse()

        snapshots = [
            PortfolioSnapshot(
                timestamp=str(s.get("timestamp", "")),
                balance=float(str(s.get("balance", 0))),
                total_value=float(str(s.get("total_value", 0))),
            )
            for s in snapshot_rows
        ]

        metrics = compute_metrics(trades, snapshots, cfg.starting_balance)

        strategy_breakdown: dict[str, dict[str, float | int]] = {}
        for t in trades:
            strat = str(t.get("strategy", "unknown"))
            if strat not in strategy_breakdown:
                strategy_breakdown[strat] = {"trades": 0, "net_pnl": 0.0}
            strategy_breakdown[strat]["trades"] = int(strategy_breakdown[strat]["trades"]) + 1
            size = float(str(t.get("size", 0)))
            if t.get("side") == "sell":
                strategy_breakdown[strat]["net_pnl"] = float(strategy_breakdown[strat]["net_pnl"]) + size
            else:
                strategy_breakdown[strat]["net_pnl"] = float(strategy_breakdown[strat]["net_pnl"]) - size

        eval_data = {
            "metrics": {
                "total_return": metrics.total_return,
                "sharpe_ratio": metrics.sharpe_ratio,
                "max_drawdown": metrics.max_drawdown,
                "win_rate": metrics.win_rate,
                "profit_factor": metrics.profit_factor,
                "total_trades": metrics.total_trades,
            },
            "strategy_breakdown": strategy_breakdown,
            "trade_analysis": _analyze_trades(trades),
            "current_config": _json.loads(cfg.model_dump_json()),
            "tunable_parameters": _build_tunable_params(cfg),
            "config_file_path": str(config.resolve()),
            "safety_constraints": {
                "mode_locked": True,
                "explanation": (
                    "Never change the 'mode' field. Only adjust parameters listed in "
                    "tunable_parameters, respecting min/max ranges."
                ),
            },
            "summary": _build_summary(metrics),
        }

        typer.echo(f"Evaluating last {period} of performance...")
        typer.echo(f"Provider: {provider}, Model: {model}")

        applied = run_autotune(
            eval_data,
            config.resolve(),
            provider=provider,
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
        )

        if not applied:
            typer.echo("No changes applied — performance is acceptable or no valid changes suggested.")
        else:
            typer.echo(f"Applied {len(applied)} change(s):")
            for change in applied:
                typer.echo(f"  {change['path']} = {change['value']}  ({change['reason']})")
    finally:
        orch.close()


@app.command()
def evaluate(
    config: ConfigOption = DEFAULT_CONFIG,
    db: DbOption = DEFAULT_DB,
    period: Annotated[str, typer.Option("--period", "-p", help="Time period to evaluate (e.g. 24h, 7d)")] = "24h",
    json_output: Annotated[bool, typer.Option("--json/--no-json", help="Output as JSON (default: true)")] = True,
) -> None:
    """Evaluate trading performance and output structured JSON for auto-tuning."""
    from polymarket_agent.backtest.metrics import PortfolioSnapshot, compute_metrics  # noqa: PLC0415

    cfg, orch = _build_orchestrator(config, db)
    try:
        cutoff = _parse_period(period)
        since = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        trades = orch.db.get_trades(since=since)
        snapshot_rows = orch.db.get_portfolio_snapshots(limit=10000, since=since)
        snapshot_rows.reverse()

        snapshots = [
            PortfolioSnapshot(
                timestamp=str(s.get("timestamp", "")),
                balance=float(str(s.get("balance", 0))),
                total_value=float(str(s.get("total_value", 0))),
            )
            for s in snapshot_rows
        ]

        metrics = compute_metrics(trades, snapshots, cfg.starting_balance)

        # Per-strategy breakdown
        strategy_breakdown: dict[str, dict[str, float | int]] = {}
        for t in trades:
            strat = str(t.get("strategy", "unknown"))
            if strat not in strategy_breakdown:
                strategy_breakdown[strat] = {"trades": 0, "net_pnl": 0.0}
            strategy_breakdown[strat]["trades"] = int(strategy_breakdown[strat]["trades"]) + 1
            size = float(str(t.get("size", 0)))
            if t.get("side") == "sell":
                strategy_breakdown[strat]["net_pnl"] = float(strategy_breakdown[strat]["net_pnl"]) + size
            else:
                strategy_breakdown[strat]["net_pnl"] = float(strategy_breakdown[strat]["net_pnl"]) - size

        trade_analysis = _analyze_trades(trades)
        tunable_params = _build_tunable_params(cfg)
        summary = _build_summary(metrics)

        # Serialize current config to dict
        current_config = _json.loads(cfg.model_dump_json())

        payload = {
            "metrics": {
                "total_return": metrics.total_return,
                "sharpe_ratio": metrics.sharpe_ratio,
                "max_drawdown": metrics.max_drawdown,
                "win_rate": metrics.win_rate,
                "profit_factor": metrics.profit_factor,
                "total_trades": metrics.total_trades,
            },
            "strategy_breakdown": strategy_breakdown,
            "trade_analysis": trade_analysis,
            "current_config": current_config,
            "tunable_parameters": tunable_params,
            "config_file_path": str(config.resolve()),
            "safety_constraints": {
                "mode_locked": True,
                "explanation": (
                    "Never change the 'mode' field. The trading loop will reject mode changes. "
                    "Only adjust parameters listed in tunable_parameters, respecting min/max ranges."
                ),
            },
            "summary": summary,
        }

        if json_output:
            typer.echo(_json.dumps(payload, indent=2, default=str))
        else:
            typer.echo(f"\n=== Evaluation ({period}) ===\n")
            typer.echo(summary)
    finally:
        orch.close()
