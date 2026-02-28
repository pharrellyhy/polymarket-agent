"""Configuration loading and validation."""

from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
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


class ExitManagerConfig(BaseModel):
    """Exit manager configuration."""

    enabled: bool = True
    profit_target_pct: float = 0.15
    stop_loss_pct: float = 0.12
    signal_reversal: bool = True
    max_hold_hours: int = 24


class NewsConfig(BaseModel):
    """News provider configuration."""

    enabled: bool = False
    provider: Literal["google_rss", "tavily"] = "google_rss"
    api_key_env: str = "TAVILY_API_KEY"
    max_calls_per_hour: int = 50
    cache_ttl: int = 900
    max_results: int = 5


class FocusConfig(BaseModel):
    """Focus trading on specific markets/events."""

    enabled: bool = False
    search_queries: list[str] = Field(default_factory=list)
    market_ids: list[str] = Field(default_factory=list)
    market_slugs: list[str] = Field(default_factory=list)
    max_brackets: int = 5


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
    exit_manager: ExitManagerConfig = Field(default_factory=ExitManagerConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    focus: FocusConfig = Field(default_factory=FocusConfig)


def load_config(path: Path) -> AppConfig:
    """Load config from a YAML file."""
    load_dotenv(path.parent / ".env", override=False)
    return AppConfig(**yaml.safe_load(path.read_text()))


def config_mtime(path: Path) -> float:
    """Return the modification time of a config file, or 0.0 if it doesn't exist."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
