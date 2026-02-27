"""Performance metrics for backtest results."""

import math
from dataclasses import dataclass


@dataclass
class PortfolioSnapshot:
    """A point-in-time snapshot of portfolio value."""

    timestamp: str
    balance: float
    total_value: float


@dataclass
class BacktestMetrics:
    """Aggregate performance metrics from a backtest run."""

    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    total_trades: int


def compute_metrics(
    trades: list[dict[str, object]],
    snapshots: list[PortfolioSnapshot],
    initial_balance: float,
) -> BacktestMetrics:
    """Compute aggregate performance metrics from trades and portfolio snapshots.

    Args:
        trades: List of trade dicts from the database (must contain 'side', 'price', 'size').
        snapshots: Chronological portfolio snapshots.
        initial_balance: Starting balance to compute total return.
    """
    total_return = _compute_total_return(snapshots, initial_balance)
    sharpe_ratio = _compute_sharpe_ratio(snapshots)
    max_drawdown = _compute_max_drawdown(snapshots)
    win_rate, profit_factor = _compute_trade_stats(trades)

    return BacktestMetrics(
        total_return=total_return,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        profit_factor=profit_factor,
        total_trades=len(trades),
    )


def _compute_total_return(snapshots: list[PortfolioSnapshot], initial_balance: float) -> float:
    """Total return as a fraction (e.g. 0.10 = 10%)."""
    if not snapshots or initial_balance <= 0:
        return 0.0
    final_value = snapshots[-1].total_value
    return (final_value - initial_balance) / initial_balance


def _compute_sharpe_ratio(snapshots: list[PortfolioSnapshot]) -> float:
    """Annualized Sharpe ratio from daily-ish snapshots. Assumes risk-free rate of 0."""
    if len(snapshots) < 2:
        return 0.0
    values = [s.total_value for s in snapshots]
    returns: list[float] = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            returns.append((values[i] - values[i - 1]) / values[i - 1])
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    if len(returns) < 2:
        return 0.0
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(252)


def _compute_max_drawdown(snapshots: list[PortfolioSnapshot]) -> float:
    """Maximum drawdown as a positive fraction (e.g. 0.15 = 15% drawdown)."""
    if not snapshots:
        return 0.0
    peak = snapshots[0].total_value
    max_dd = 0.0
    for s in snapshots:
        if s.total_value > peak:
            peak = s.total_value
        if peak > 0:
            dd = (peak - s.total_value) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def _compute_trade_stats(trades: list[dict[str, object]]) -> tuple[float, float]:
    """Return (win_rate, profit_factor) from trade records.

    A trade is considered a "win" if it is a sell at a price higher than
    the average buy price — but since we don't track per-position P&L in
    the trade log, we approximate: sells are wins if their proceeds exceed
    their size (the trade size is always in USDC). In the simple case of
    paper trading, all sells at a profit are wins.

    Profit factor = gross_profit / gross_loss (or inf if no losses).
    """
    if not trades:
        return 0.0, 0.0

    # Pair buy/sell trades per token to calculate per-round-trip P&L
    buy_prices: dict[str, float] = {}
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0

    for trade in trades:
        side = str(trade.get("side", ""))
        token_id = str(trade.get("token_id", ""))
        price = float(str(trade.get("price", 0)))

        if side == "buy":
            buy_prices[token_id] = price
        elif side == "sell":
            entry_price = buy_prices.get(token_id, price)
            pnl = price - entry_price
            if pnl > 0:
                wins += 1
                gross_profit += pnl
            else:
                losses += 1
                gross_loss += abs(pnl)

    total_round_trips = wins + losses
    win_rate = wins / total_round_trips if total_round_trips > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    return win_rate, profit_factor
