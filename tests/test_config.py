"""Tests for config loading."""

from pathlib import Path

from polymarket_agent.config import AppConfig, CategoryConfig, FocusConfig, load_config

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
    assert config.aggregation.min_confidence == 0.5
    assert config.aggregation.min_strategies == 1


def test_exit_manager_config_defaults() -> None:
    cfg = AppConfig()
    assert cfg.exit_manager.enabled is True
    assert cfg.exit_manager.profit_target_pct == 0.15
    assert cfg.exit_manager.stop_loss_pct == 0.12
    assert cfg.exit_manager.signal_reversal is True
    assert cfg.exit_manager.max_hold_hours == 24


# ------------------------------------------------------------------
# CategoryConfig and extended FocusConfig tests
# ------------------------------------------------------------------


def test_category_config_defaults() -> None:
    cfg = CategoryConfig()
    assert cfg.preferred == []
    assert cfg.excluded == []


def test_focus_config_new_fields_defaults() -> None:
    cfg = FocusConfig()
    assert cfg.min_volume_24h == 0.0
    assert cfg.prioritize_trending is False
    assert cfg.fetch_limit == 50
    assert cfg.categories.preferred == []
    assert cfg.categories.excluded == []


def test_focus_config_loads_from_yaml(tmp_path: Path) -> None:
    yaml_content = """\
mode: paper
starting_balance: 1000.0
focus:
  enabled: false
  fetch_limit: 100
  min_volume_24h: 500
  prioritize_trending: true
  categories:
    preferred: [politics, crypto]
    excluded: [sports]
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content)
    config = load_config(config_file)
    assert config.focus.fetch_limit == 100
    assert config.focus.min_volume_24h == 500
    assert config.focus.prioritize_trending is True
    assert config.focus.categories.preferred == ["politics", "crypto"]
    assert config.focus.categories.excluded == ["sports"]
