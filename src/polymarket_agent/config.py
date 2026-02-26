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


class AppConfig(BaseModel):
    """Top-level application configuration."""

    mode: Literal["monitor", "paper", "live", "mcp"] = "paper"
    starting_balance: float = 1000.0
    poll_interval: int = 60
    strategies: dict[str, dict[str, Any]] = Field(default_factory=dict)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)


def load_config(path: Path) -> AppConfig:
    """Load config from a YAML file."""
    return AppConfig(**yaml.safe_load(path.read_text()))
