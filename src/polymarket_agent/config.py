"""Configuration loading and validation."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class RiskConfig(BaseModel):
    """Risk management configuration."""

    max_position_size: float = 100.0
    max_daily_loss: float = 50.0
    max_open_orders: int = 10


class AggregationConfig(BaseModel):
    """Signal aggregation configuration."""

    min_confidence: float = 0.5
    min_strategies: int = 1


class ConditionalOrderConfig(BaseModel):
    """Conditional order (stop-loss / take-profit / trailing stop) configuration."""

    enabled: bool = False
    default_stop_loss_pct: float = 0.10
    default_take_profit_pct: float = 0.20
    trailing_stop_enabled: bool = False
    trailing_stop_pct: float = 0.05


class PositionSizingConfig(BaseModel):
    """Position sizing configuration."""

    method: Literal["fixed", "kelly", "fractional_kelly"] = "fixed"
    kelly_fraction: float = 0.25
    max_bet_pct: float = 0.10


class BacktestConfig(BaseModel):
    """Backtesting configuration."""

    default_spread: float = 0.02
    snapshot_interval: int = 86400


class MonitoringConfig(BaseModel):
    """Monitoring and dashboard configuration."""

    structured_logging: bool = False
    log_file: str | None = None
    alert_webhooks: list[str] = Field(default_factory=list)
    snapshot_interval: int = 300
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8080


class AppConfig(BaseModel):
    """Top-level application configuration."""

    mode: Literal["monitor", "paper", "live", "mcp"] = "paper"
    starting_balance: float = 1000.0
    poll_interval: int = 60
    strategies: dict[str, dict[str, Any]] = Field(default_factory=dict)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    conditional_orders: ConditionalOrderConfig = Field(default_factory=ConditionalOrderConfig)
    position_sizing: PositionSizingConfig = Field(default_factory=PositionSizingConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)


def load_config(path: Path) -> AppConfig:
    """Load config from a YAML file."""
    return AppConfig(**yaml.safe_load(path.read_text()))


def config_mtime(path: Path) -> float:
    """Return the modification time of a config file, or 0.0 if it doesn't exist."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
