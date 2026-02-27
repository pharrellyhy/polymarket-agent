"""Tests for the autotune module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from polymarket_agent.autotune import (
    _apply_changes,
    _init_client,
    _parse_changes,
    _validate_change,
    run_autotune,
)
from polymarket_agent.cli import app
from typer.testing import CliRunner

runner = CliRunner()

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

_TUNABLE_PARAMS: list[dict[str, object]] = [
    {"path": "aggregation.min_confidence", "current": 0.3, "min": 0.1, "max": 0.9},
    {"path": "risk.max_position_size", "current": 200, "min": 10, "max": 1000},
    {"path": "strategies.signal_trader.volume_threshold", "current": 2000, "min": 500, "max": 50000},
]

_SAMPLE_EVAL: dict[str, object] = {
    "metrics": {"total_return": -0.05, "sharpe_ratio": 0.2, "win_rate": 0.4},
    "tunable_parameters": _TUNABLE_PARAMS,
    "config_file_path": "/tmp/config.yaml",
    "summary": "Negative return",
}


def _make_config_file(tmp_path: Path) -> Path:
    """Create a minimal config.yaml for testing."""
    cfg = {
        "mode": "paper",
        "aggregation": {"min_confidence": 0.3, "min_strategies": 1},
        "risk": {"max_position_size": 200, "max_daily_loss": 500},
        "strategies": {"signal_trader": {"enabled": True, "volume_threshold": 2000}},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg, default_flow_style=False))
    return p


def _mock_anthropic_client(response_text: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=response_text)]
    client.messages.create.return_value = response
    return client


def _mock_openai_client(response_text: str) -> MagicMock:
    client = MagicMock()
    message = MagicMock()
    message.content = response_text
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    client.chat.completions.create.return_value = response
    return client


# ------------------------------------------------------------------
# _parse_changes tests
# ------------------------------------------------------------------


def test_parse_valid_json() -> None:
    raw = '{"changes": [{"path": "risk.max_position_size", "value": 150, "reason": "reduce risk"}]}'
    changes = _parse_changes(raw)
    assert len(changes) == 1
    assert changes[0]["path"] == "risk.max_position_size"


def test_parse_empty_changes() -> None:
    raw = '{"changes": []}'
    changes = _parse_changes(raw)
    assert changes == []


def test_parse_strips_markdown_fences() -> None:
    raw = '```json\n{"changes": [{"path": "a.b", "value": 1, "reason": "x"}]}\n```'
    changes = _parse_changes(raw)
    assert len(changes) == 1


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_changes("not json at all")


def test_parse_missing_changes_key_raises() -> None:
    with pytest.raises(ValueError, match="changes"):
        _parse_changes('{"adjustments": []}')


# ------------------------------------------------------------------
# _validate_change tests
# ------------------------------------------------------------------


def test_validate_valid_change() -> None:
    change = {"path": "aggregation.min_confidence", "value": 0.5, "reason": "raise bar"}
    result = _validate_change(change, _TUNABLE_PARAMS)
    assert result is not None
    assert result["value"] == 0.5


def test_validate_clamps_out_of_range() -> None:
    change = {"path": "aggregation.min_confidence", "value": 2.0, "reason": "too high"}
    result = _validate_change(change, _TUNABLE_PARAMS)
    assert result is not None
    assert result["value"] == 0.9  # clamped to max


def test_validate_clamps_below_min() -> None:
    change = {"path": "aggregation.min_confidence", "value": 0.01, "reason": "too low"}
    result = _validate_change(change, _TUNABLE_PARAMS)
    assert result is not None
    assert result["value"] == 0.1  # clamped to min


def test_validate_rejects_non_tunable() -> None:
    change = {"path": "mode", "value": "live", "reason": "switch mode"}
    result = _validate_change(change, _TUNABLE_PARAMS)
    assert result is None


def test_validate_rejects_malformed() -> None:
    result = _validate_change({"bad": "data"}, _TUNABLE_PARAMS)
    assert result is None


def test_validate_preserves_int_type() -> None:
    change = {"path": "risk.max_position_size", "value": 300.0, "reason": "increase"}
    result = _validate_change(change, _TUNABLE_PARAMS)
    assert result is not None
    assert result["value"] == 300.0  # float, but _apply_changes handles int conversion


# ------------------------------------------------------------------
# _apply_changes tests
# ------------------------------------------------------------------


def test_apply_changes_modifies_config(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    changes = [{"path": "aggregation.min_confidence", "value": 0.5, "reason": "test"}]
    _apply_changes(changes, config_path)

    updated = yaml.safe_load(config_path.read_text())
    assert updated["aggregation"]["min_confidence"] == 0.5


def test_apply_changes_int_preservation(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    changes = [{"path": "risk.max_position_size", "value": 300.0, "reason": "increase"}]
    _apply_changes(changes, config_path)

    updated = yaml.safe_load(config_path.read_text())
    assert updated["risk"]["max_position_size"] == 300
    assert isinstance(updated["risk"]["max_position_size"], int)


def test_apply_changes_adds_header_comment(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    changes = [{"path": "aggregation.min_confidence", "value": 0.4, "reason": "test"}]
    _apply_changes(changes, config_path)

    text = config_path.read_text()
    assert text.startswith("# Auto-tuned on ")


# ------------------------------------------------------------------
# run_autotune integration tests
# ------------------------------------------------------------------


def test_run_autotune_applies_changes(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    llm_response = json.dumps(
        {"changes": [{"path": "aggregation.min_confidence", "value": 0.5, "reason": "improve filtering"}]}
    )
    mock_client = _mock_openai_client(llm_response)

    eval_data = {**_SAMPLE_EVAL, "config_file_path": str(config_path)}

    with patch("polymarket_agent.autotune._init_client", return_value=(mock_client, "openai")):
        applied = run_autotune(eval_data, config_path, provider="openai", model="gpt-4o")

    assert len(applied) == 1
    assert applied[0]["path"] == "aggregation.min_confidence"

    updated = yaml.safe_load(config_path.read_text())
    assert updated["aggregation"]["min_confidence"] == 0.5


def test_run_autotune_no_changes(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    llm_response = '{"changes": []}'
    mock_client = _mock_openai_client(llm_response)

    eval_data = {**_SAMPLE_EVAL, "config_file_path": str(config_path)}

    with patch("polymarket_agent.autotune._init_client", return_value=(mock_client, "openai")):
        applied = run_autotune(eval_data, config_path, provider="openai", model="gpt-4o")

    assert applied == []


def test_run_autotune_filters_invalid_changes(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    llm_response = json.dumps(
        {
            "changes": [
                {"path": "mode", "value": "live", "reason": "switch to live"},  # non-tunable
                {"path": "aggregation.min_confidence", "value": 0.4, "reason": "valid"},
            ]
        }
    )
    mock_client = _mock_openai_client(llm_response)

    eval_data = {**_SAMPLE_EVAL, "config_file_path": str(config_path)}

    with patch("polymarket_agent.autotune._init_client", return_value=(mock_client, "openai")):
        applied = run_autotune(eval_data, config_path, provider="openai", model="gpt-4o")

    assert len(applied) == 1
    assert applied[0]["path"] == "aggregation.min_confidence"


def test_run_autotune_anthropic_provider(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    llm_response = json.dumps({"changes": [{"path": "aggregation.min_confidence", "value": 0.6, "reason": "test"}]})
    mock_client = _mock_anthropic_client(llm_response)

    eval_data = {**_SAMPLE_EVAL, "config_file_path": str(config_path)}

    with patch("polymarket_agent.autotune._init_client", return_value=(mock_client, "anthropic")):
        applied = run_autotune(eval_data, config_path, provider="anthropic", model="claude-sonnet-4-6")

    assert len(applied) == 1


def test_run_autotune_invalid_json_response(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    mock_client = _mock_openai_client("I cannot provide JSON")

    eval_data = {**_SAMPLE_EVAL, "config_file_path": str(config_path)}

    with (
        patch("polymarket_agent.autotune._init_client", return_value=(mock_client, "openai")),
        pytest.raises(json.JSONDecodeError),
    ):
        run_autotune(eval_data, config_path, provider="openai", model="gpt-4o")


def test_init_client_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="provider"):
        _init_client(provider="claude", api_key_env=None, base_url=None)


def test_cli_autotune_rejects_unknown_provider(tmp_path: Path) -> None:
    config_path = _make_config_file(tmp_path)
    db_path = tmp_path / "test.db"
    result = runner.invoke(
        app,
        [
            "autotune",
            "--config",
            str(config_path),
            "--db",
            str(db_path),
            "--provider",
            "claude",
        ],
    )
    assert result.exit_code != 0
    assert "provider must be one of" in result.output
