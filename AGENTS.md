# AGENTS.md

Project-specific instructions for agents working in `polymarket-agent`.
These rules override global guidance when they are more specific.

## 1) Scope and Priority

- Scope: this file applies to the repository root and all subdirectories unless a deeper `AGENTS.md` exists.
- Priority: system/developer/user direct instructions first, then this file, then global defaults.
- Goal: make small, verifiable changes with minimal risk to trading behavior and data correctness.

## 2) Project Snapshot

- Project: Polymarket Agent (Python framework wrapping the `polymarket` CLI into an agent-friendly trading pipeline).
- Stack: Python 3.12+, Pydantic v2, SQLite, Typer, PyYAML, Ruff, Mypy, Pytest, `pytest-mock`.
- Architecture direction: CLI-wrapper pipeline (Data Layer -> Strategy Engine -> Execution Layer) coordinated by an orchestrator.
- Current source layout: `src/polymarket_agent/` with `data/`, `strategies/`, `execution/`, `config.py`, and `db.py`.
- Tests: `tests/` (configured with `pythonpath = ["src"]` in `pyproject.toml`).

Key files/locations (current and near-term, per project docs):
- CLI entry point: `src/polymarket_agent/cli.py` (project script: `polymarket-agent`)
- Data models: `src/polymarket_agent/data/models.py`
- CLI wrapper client: `src/polymarket_agent/data/client.py`
- TTL cache: `src/polymarket_agent/data/cache.py`
- Strategy base: `src/polymarket_agent/strategies/base.py`
- Execution layer and persistence: `src/polymarket_agent/execution/`, `src/polymarket_agent/db.py`
- Config: `src/polymarket_agent/config.py`, `config.yaml`
- Plans/design docs: `docs/plans/`

## 3) Non-Negotiable Constraints

- Do not mention Claude (or any model) as code generator/co-author in commits, comments, or docs.
- Do not edit secrets or local credentials (`.env`, private keys) unless explicitly asked.
- Do not perform opportunistic refactors, dependency upgrades, or broad formatting sweeps.
- Use the `polymarket` CLI as the primary market data source and request JSON output (`-o json`) when interacting with it.
- Route Polymarket CLI subprocess calls through `PolymarketData._run_cli()` (do not add direct subprocess calls elsewhere).
- Preserve typed contracts (Pydantic models / strategy and executor interfaces) when adding features.
- Never revert user changes you did not make.

## 4) How to Work

1. Read relevant code paths first; state assumptions if behavior is unclear.
2. Make the smallest change that solves the request.
3. Validate immediately with the narrowest useful check.
4. Stop on failing checks, summarize root cause, then fix incrementally.
5. Show concise diffs and list exactly what was verified.

## 5) Canonical Commands

Run from repo root unless noted.

```bash
# Setup
uv sync

# Run CLI commands
uv run polymarket-agent tick
uv run polymarket-agent run
uv run polymarket-agent status

# Tests
uv run pytest tests/ -v
uv run pytest tests/ -v --cov=src/polymarket_agent

# Lint / format / typecheck (target changed files when possible)
ruff check src/ tests/
ruff format src/ tests/
mypy src/
```

Validation policy:
- Changed Python files: run `ruff check` on changed paths.
- Behavior changes: run nearest relevant `pytest` target(s) first (for example `tests/test_client.py`, `tests/test_cache.py`).
- Signature/type changes: run `mypy` on touched modules or `mypy src/`.
- If no automated check applies, document manual validation performed.
- Stop on first failure; summarize root cause before broadening scope.

## 6) Change-Specific Guardrails

- Data layer / CLI wrapper changes:
  - Keep `polymarket` CLI JSON parsing and typed model conversion consistent.
  - Update related tests in `tests/test_client.py` and `tests/test_models.py` as needed.
- Strategy changes:
  - Implement/extend the `Strategy` base contract in `src/polymarket_agent/strategies/base.py`.
  - Preserve `Signal` semantics and aggregation assumptions.
  - Add focused tests (for example `tests/test_signal_trader.py`, `tests/test_strategy_base.py`).
- Execution / trading changes:
  - Preserve executor interface compatibility between paper and live paths.
  - Keep paper trading persistence/logging behavior deterministic and testable.
- Config changes:
  - Keep Pydantic-backed config loading aligned with `config.yaml`.
  - Update defaults/docs when config shape changes.
- DB changes:
  - Avoid destructive schema rewrites unless explicitly requested.
  - Preserve compatibility with existing SQLite state where possible.

## 7) Documentation and Session State

Update docs when behavior or operator workflow changes:

- `README.md`: run/test instructions, architecture updates, and usage examples.
- `docs/plans/*`: implementation plans and progress for multi-step work.
- `HANDOFF.md`: add/update a session entry when work meaningfully changes behavior or project state.

`HANDOFF.md` entry format (from project guidance):
- Include: Problem, Solution, Edits, NOT Changed, Verification.
- New entries go at the top (below the header) separated by `---`.
- Keep only the last 10 entries.
- Maintain the `Last updated: YYYY-MM-DD` header date.

Keep docs concise and factual; avoid aspirational text not reflected in code.

## 8) External Docs and Uncertainty

- Use Context7 for library/framework API uncertainty before coding.
- Prefer official docs and repo source over memory when APIs are version-sensitive.
- If API uncertainty remains, build a minimal reproducible check locally and report the result.

## 9) Completion Checklist

Before declaring completion:

1. Confirm only intended files changed.
2. Run the smallest relevant lint/type/test checks and capture outcomes.
3. Confirm no new direct Polymarket CLI subprocess calls bypass `PolymarketData._run_cli()` (if applicable).
4. Summarize:
   - files changed
   - checks run (with pass/fail)
   - remaining risks or follow-ups
