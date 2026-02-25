"""Tests for config loading."""

import tempfile
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

risk:
  max_position_size: 200.0
  max_daily_loss: 100.0
  max_open_orders: 5
"""


def test_load_config_from_yaml():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_YAML)
        f.flush()
        config = load_config(Path(f.name))
    assert config.mode == "paper"
    assert config.starting_balance == 2000.0
    assert config.poll_interval == 30
    assert config.strategies["signal_trader"]["enabled"] is True
    assert config.risk.max_position_size == 200.0


def test_default_config():
    config = AppConfig()
    assert config.mode == "paper"
    assert config.starting_balance == 1000.0
    assert config.risk.max_position_size == 100.0
