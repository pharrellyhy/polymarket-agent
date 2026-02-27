# CLAUDE.md

This file provides guidance to Claude Code when working with the Polymarket Agent codebase.

## Behavioral Rules

- **DO NOT** mention Claude as code generator or code co-author in commits, comments, or docs
- **Plan before you code** — before starting any implementation work, write a design plan or implementation plan in `docs/plans/` first. No code changes until a plan document exists and covers the approach

## Project Overview

Polymarket Agent is a Python framework that wraps the Polymarket CLI into an agent-friendly auto-trading pipeline. It provides market data access, pluggable trading strategies, paper/live execution, and MCP server integration for AI agents.

**Tech stack:** Python 3.12+, Pydantic v2, SQLite, Typer, PyYAML, Polymarket CLI

## Quick Start

```bash
# Setup
uv sync

# Run a single trading tick
uv run polymarket-agent tick

# Run the continuous trading loop
uv run polymarket-agent run

# Check portfolio status
uv run polymarket-agent status

# Run tests
uv run pytest tests/ -v
uv run pytest tests/ -v --cov=src/polymarket_agent  # with coverage

# Code quality (ruff replaces Black/flake8; mypy strict mode)
ruff check src/                # lint
ruff format src/               # format
mypy src/                      # type check (disallow_untyped_defs = true)
```

Pre-commit hooks run `ruff` and `isort` automatically.

## Architecture

CLI-wrapper pipeline: Data Layer → Strategy Engine → Execution Layer, coordinated by an Orchestrator.

### Data Layer
Wraps the `polymarket` CLI with `-o json` output. Provides typed Python API via Pydantic models with TTL caching.

### Strategy Engine
Pluggable strategy modules sharing a common `Strategy` ABC. Each strategy receives market data and emits `Signal` objects. Strategies: SignalTrader, MarketMaker, Arbitrageur, AIAnalyst.

### Execution Layer
Two executors with the same `Executor` ABC interface:
- `PaperTrader` — simulates fills against real order book data, logs to SQLite
- `LiveTrader` — uses `py-clob-client` for real order placement (Phase 2)

### Orchestrator
Main loop: fetch data → run strategies → aggregate signals → execute trades → log state.

## Key File Locations

| Purpose | Location |
|---------|----------|
| CLI entry point | `src/polymarket_agent/cli.py` |
| Data models (Pydantic) | `src/polymarket_agent/data/models.py` |
| CLI wrapper client | `src/polymarket_agent/data/client.py` |
| TTL cache | `src/polymarket_agent/data/cache.py` |
| Strategy base + Signal | `src/polymarket_agent/strategies/base.py` |
| Strategy implementations | `src/polymarket_agent/strategies/` |
| Executor base + Portfolio | `src/polymarket_agent/execution/base.py` |
| Paper trader | `src/polymarket_agent/execution/paper.py` |
| Orchestrator | `src/polymarket_agent/orchestrator.py` |
| SQLite database | `src/polymarket_agent/db.py` |
| Configuration | `src/polymarket_agent/config.py`, `config.yaml` |
| Design docs | `docs/plans/` |

## Code Style

- **No `__future__` imports** — this project targets Python 3.12+ exclusively, so `from __future__ import annotations` and other `__future__` imports are unnecessary and should not be used
- **Python 3.12+** compatible
- **Type hints** required on all functions and methods
- **Classes:** PascalCase (e.g., `PaperTrader`)
- **Functions/Variables:** snake_case (e.g., `get_active_markets`)
- **Constants:** UPPERCASE_WITH_UNDERSCORES
- **Line length:** 120 characters
- **Docstrings:** Google-style for public APIs
- Use dataclasses/Pydantic for structured data
- Use specific exception types, not bare `except:`

## Commit Messages

Use conventional commit format: `type(scope): description`

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

```
feat: add market maker strategy
fix: resolve cache expiration race condition
refactor: simplify signal aggregation logic
```

Keep first line under 50 characters. Use present tense.

## Session Handoff

After completing changes, update `HANDOFF.md` with a detailed entry covering:
- **Problem**: what issue or need prompted the change
- **Solution**: what was done and why
- **Edits**: files modified with key edit descriptions (line references, code context)
- **NOT Changed**: important things deliberately left untouched
- **Verification**: commands to validate the changes

Formatting rules:
- Each entry gets an `---` horizontal rule separator
- New entries go at the top (below the header)
- Keep only the **last 10 entries**; delete older entries from the bottom when adding new ones
- Maintain the `Last updated: YYYY-MM-DD` date in the header

## MCP Guidelines

Always use context7 when you need code generation, setup or configuration steps, or library/API documentation. Automatically use the Context7 MCP tools to resolve library id and get library docs without being explicitly asked.

## Important Constraints

- The `polymarket` CLI (installed via Homebrew) is the primary data source — always use `-o json`
- New strategies must implement the `Strategy` ABC and be registered in `STRATEGY_REGISTRY` in `orchestrator.py`
- New executors must implement the `Executor` ABC
- All CLI subprocess calls go through `PolymarketData._run_cli()` — do not call subprocess directly elsewhere
- Configuration loaded via Pydantic from `config.yaml`
- Tests use `pytest` with `pytest-mock` for mocking subprocess calls
- Never commit `.env` files or private keys
