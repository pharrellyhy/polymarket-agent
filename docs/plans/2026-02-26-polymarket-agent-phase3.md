# Polymarket Agent Phase 3 — MCP Server Implementation Plan

Date: 2026-02-26

## Summary

Expose Polymarket data and trading as MCP tools for AI agents. Uses FastMCP with lifespan context to share the Orchestrator across all 8 tools.

## Tasks

### Task 1: Add `mcp` dependency + data layer additions

**Goal:** Add `mcp` to pyproject.toml. Add `search_markets()` and `get_leaderboard()` to `PolymarketData`.

**Files:**
- `pyproject.toml` — add `mcp>=1.0`
- `src/polymarket_agent/data/client.py` — add `search_markets(query)` and `get_leaderboard(period)`
- `src/polymarket_agent/data/models.py` — add `Trader` Pydantic model for leaderboard entries
- `tests/test_client.py` — tests for new methods
- `tests/test_models.py` — test `Trader.from_cli()`

### Task 2: MCP server core — lifespan + read-only tools

**Goal:** Create `mcp_server.py` with lifespan context and 4 read-only tools.

**Tools:** `search_markets`, `get_market_detail`, `get_price_history`, `get_leaderboard`

**Files:**
- `src/polymarket_agent/mcp_server.py` — NEW
- `tests/test_mcp_server.py` — NEW

### Task 3: MCP server — portfolio, signals, trading tools

**Goal:** Add 3 tools that interact with orchestrator state.

**Tools:** `get_portfolio`, `get_signals`, `place_trade`

**Files:**
- `src/polymarket_agent/mcp_server.py` — extend
- `tests/test_mcp_server.py` — extend

### Task 4: MCP server — analyze_market tool

**Goal:** Add AI analyst tool. Gracefully handle missing API key.

**Tools:** `analyze_market`

**Files:**
- `src/polymarket_agent/mcp_server.py` — extend
- `tests/test_mcp_server.py` — extend

### Task 5: CLI integration + config

**Goal:** Add `polymarket-agent mcp` command. Wire up config.

**Files:**
- `src/polymarket_agent/cli.py` — add `mcp` command
- `config.yaml` — document mcp mode

### Task 6: Integration test + final verification

**Goal:** End-to-end test of MCP tools with mocked data. Full test suite, lint, type check.

**Files:**
- `tests/test_mcp_integration.py` — NEW
- Run: pytest, ruff, mypy

## Architecture

```
FastMCP("polymarket-agent") with lifespan:
  ├── AppContext (Orchestrator, PolymarketData, AppConfig)
  ├── search_markets(query, limit)
  ├── get_market_detail(market_id)
  ├── get_price_history(token_id, interval)
  ├── get_leaderboard(period)
  ├── get_portfolio()
  ├── get_signals()
  ├── place_trade(market_id, token_id, side, size, price)
  └── analyze_market(market_id)
```
