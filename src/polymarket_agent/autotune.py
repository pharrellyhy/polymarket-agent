"""LLM-based config auto-tuner for the Polymarket Agent.

Replaces the ``claude -p`` approach with a direct API call to an LLM
(Anthropic or OpenAI-compatible) for parameter tuning recommendations.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)
_SUPPORTED_PROVIDERS: set[str] = {"anthropic", "openai"}

_SYSTEM_PROMPT = """\
You are an auto-tuning agent for a Polymarket trading bot.

Analyze the evaluation output and decide whether config.yaml needs adjustments.

RULES:
1. NEVER change the 'mode' field — the trading loop will reject mode changes.
2. Adjust at most 2-3 parameters per tuning session to avoid instability.
3. Respect the min/max ranges in tunable_parameters.
4. If performance is acceptable (positive return, Sharpe > 0.5, win rate > 45%), make no changes.
5. Only adjust parameters listed in tunable_parameters.

Respond with ONLY valid JSON in this exact format (no markdown fences):
{"changes": [{"path": "dotted.config.path", "value": <number>, "reason": "brief explanation"}]}

If no changes are needed, respond with:
{"changes": []}
"""


def _init_client(
    provider: str,
    api_key_env: str | None,
    base_url: str | None,
) -> tuple[Any, str]:
    """Initialise an LLM client and return (client, resolved_provider)."""
    provider_normalized = provider.strip().lower()
    if provider_normalized not in _SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider {provider!r}; expected one of: anthropic, openai")

    default_env = "ANTHROPIC_API_KEY" if provider_normalized == "anthropic" else "OPENAI_API_KEY"
    env_var = api_key_env or default_env
    api_key = os.environ.get(env_var)
    if not api_key:
        raise RuntimeError(f"{env_var} is not set")

    if provider_normalized == "anthropic":
        import anthropic  # noqa: PLC0415

        return anthropic.Anthropic(api_key=api_key), provider_normalized

    import openai  # type: ignore[import-not-found]  # noqa: PLC0415

    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return openai.OpenAI(**kwargs), "openai"


def _call_llm(client: Any, provider: str, model: str, eval_json: str) -> str:
    """Send the evaluation data to the LLM and return the raw text response."""
    user_msg = f"EVALUATION DATA:\n{eval_json}"

    if provider == "anthropic":
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return str(response.content[0].text).strip()

    response = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    return str(response.choices[0].message.content).strip()


def _parse_changes(raw: str) -> list[dict[str, Any]]:
    """Parse the LLM JSON response into a list of change dicts."""
    # Strip markdown fences if the model wraps them
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines)

    data = json.loads(cleaned)
    if not isinstance(data, dict) or "changes" not in data:
        raise ValueError("Response must be a JSON object with a 'changes' key")
    changes: list[dict[str, Any]] = data["changes"]
    if not isinstance(changes, list):
        raise ValueError("'changes' must be a list")
    return changes


def _validate_change(change: dict[str, Any], tunable: list[dict[str, object]]) -> dict[str, Any] | None:
    """Validate a single change against tunable parameter ranges.

    Returns the validated change dict or ``None`` if invalid.
    """
    path = change.get("path")
    value = change.get("value")
    reason = change.get("reason", "")

    if not isinstance(path, str) or value is None:
        logger.warning("Skipping malformed change: %s", change)
        return None

    # Find matching tunable parameter
    param = next((p for p in tunable if p.get("path") == path), None)
    if param is None:
        logger.warning("Skipping non-tunable parameter: %s", path)
        return None

    # Enforce min/max
    try:
        num_value = float(value)
    except (TypeError, ValueError):
        logger.warning("Skipping non-numeric value for %s: %s", path, value)
        return None

    min_val = float(str(param.get("min", float("-inf"))))
    max_val = float(str(param.get("max", float("inf"))))
    if num_value < min_val or num_value > max_val:
        logger.warning("Value %s for %s out of range [%s, %s] — clamping", num_value, path, min_val, max_val)
        num_value = max(min_val, min(max_val, num_value))

    # Preserve int type if current value is int
    current = param.get("current")
    if isinstance(current, int):
        num_value = float(round(num_value))

    return {"path": path, "value": num_value, "reason": str(reason)}


def _apply_changes(changes: list[dict[str, Any]], config_path: Path) -> None:
    """Apply validated changes to the config YAML file."""
    text = config_path.read_text()
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Config file {config_path} does not contain a YAML mapping")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for change in changes:
        path = change["path"]
        value = change["value"]
        reason = change.get("reason", "")

        keys = path.split(".")
        node = data
        for key in keys[:-1]:
            node = node[key]

        # Use int if value is a whole number and current is int
        final_value: int | float = int(value) if value == int(value) and isinstance(node.get(keys[-1]), int) else value
        node[keys[-1]] = final_value

        logger.info("Applied: %s = %s (%s)", path, final_value, reason)

    # Write back with a comment header
    lines = [f"# Auto-tuned on {now}\n"]
    lines.append(yaml.dump(data, default_flow_style=False, sort_keys=False))
    config_path.write_text("".join(lines))


def run_autotune(
    eval_data: dict[str, Any],
    config_path: Path,
    *,
    provider: str,
    model: str,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> list[dict[str, Any]]:
    """Run the auto-tuning pipeline.

    Args:
        eval_data: Output from the ``evaluate`` CLI command (parsed JSON).
        config_path: Path to config.yaml.
        provider: LLM provider — ``"anthropic"`` or ``"openai"``.
        model: Model identifier (e.g. ``"claude-sonnet-4-6"``, ``"gpt-4o"``).
        base_url: Optional API base URL for OpenAI-compatible endpoints.
        api_key_env: Optional env var name for the API key.

    Returns:
        List of applied change dicts, each with ``path``, ``value``, ``reason``.
    """
    client, resolved_provider = _init_client(provider, api_key_env, base_url)

    eval_json = json.dumps(eval_data, indent=2, default=str)
    raw_response = _call_llm(client, resolved_provider, model, eval_json)
    logger.info("LLM response: %s", raw_response)

    changes = _parse_changes(raw_response)
    if not changes:
        logger.info("No changes recommended")
        return []

    tunable = eval_data.get("tunable_parameters", [])
    if not isinstance(tunable, list):
        tunable = []

    validated: list[dict[str, Any]] = []
    for change in changes:
        result = _validate_change(change, tunable)
        if result is not None:
            validated.append(result)

    if not validated:
        logger.info("No valid changes after validation")
        return []

    _apply_changes(validated, config_path)
    return validated
