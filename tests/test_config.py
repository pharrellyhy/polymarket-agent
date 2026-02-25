"""Tests for config loading."""

from pathlib import Path

from polymarket_agent.config import AppConfig, load_config

SAMPLE_YAML = """\
mode: paper
starting_balance: 2000.0
poll_interval: 30

strategies:
  signal_trader:
    enabled: true
    volume_threshold: 5000
    price_move_threshold: 0.03

aggregation:
  min_confidence: 0.6
  min_strategies: 2

risk:
  max_position_size: 200.0
  max_daily_loss: 100.0
  max_open_orders: 5
"""


def test_load_config_from_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(SAMPLE_YAML)
    config = load_config(config_file)
    assert config.mode == "paper"
    assert config.starting_balance == 2000.0
    assert config.poll_interval == 30
    assert config.strategies["signal_trader"]["enabled"] is True
    assert config.aggregation.min_confidence == 0.6
    assert config.aggregation.min_strategies == 2
    assert config.risk.max_position_size == 200.0


def test_default_config() -> None:
    config = AppConfig()
    assert config.mode == "paper"
    assert config.starting_balance == 1000.0
    assert config.risk.max_position_size == 100.0


def test_aggregation_config_defaults() -> None:
    config = AppConfig()
    assert config.aggregation.min_confidence == 0.5
    assert config.aggregation.min_strategies == 1
