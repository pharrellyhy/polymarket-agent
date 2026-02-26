# Polymarket Agent — Handoff Document

Last updated: 2026-02-26

---

## Session Entry — 2026-02-26 (Data Layer Gap-Fill Follow-Up: Parsing Fixes)

### Problem
- Review of the newly added data-layer gap-fill code found a correctness bug in `Position.from_cli()`: numeric fallback parsing used `or` chains, which can treat valid `0.0` values as missing and incorrectly fall through to alternate fields.
- Example risk: a position with explicit `pnl=0` could incorrectly read `profit` instead.

### Solution
- **Zero-safe numeric fallback helper:** Added `_float_field_first(data, *keys)` to select the first present key while preserving valid `0`/`0.0` values.
- **Position parsing fix:** Updated `Position.from_cli()` to use explicit key-presence fallback (`size`/`shares`, `avgPrice`/`avg_price`, `currentPrice`/`current_price`, `pnl`/`profit`) without falsy-value bugs.
- **Spread parsing improvement:** `Spread.from_cli()` now also parses `bid`/`ask` when present (supports common key variants like `bid`, `bestBid`, `best_bid`) instead of only `spread`.
- **Regression coverage:** Added a focused client test verifying zero-valued position fields are preserved and do not fall through to alternate keys.
- **Test helper cleanup:** Replaced the fragile string-replacement mock for `"markets get"` with a proper single-object JSON fixture in `tests/test_client.py`.

### Edits
- `src/polymarket_agent/data/models.py` — added `_float_field_first()`, fixed `Position.from_cli()` zero-value parsing, improved `Spread.from_cli()` bid/ask parsing
- `tests/test_client.py` — added zero-value positions regression test; fixed `markets get` mock JSON fixture; minor import-order cleanup
- `HANDOFF.md` — added this follow-up entry

### NOT Changed
- No changes to `PolymarketData` method signatures or command wiring from the gap-fill entry.
- No full test suite rerun (focused client/data-layer verification only).

### Verification
```bash
uv run pytest tests/test_client.py -q   # 16 passed
uv run ruff check src/polymarket_agent/data/models.py src/polymarket_agent/data/client.py tests/test_client.py   # All checks passed
uv run mypy src/polymarket_agent/data/models.py src/polymarket_agent/data/client.py   # Success: no issues found in 2 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-26 (Data Layer Gap-Fill: 5 Missing Methods)

### Problem
- Original design doc listed 10 `PolymarketData` methods; only 7 were implemented through Phases 1–4.
- Missing: `get_event(event_id)`, `get_price(token_id)`, `get_spread(token_id)`, `get_volume(event_id)`, `get_positions(address)`.
- Missing models: `Spread`, `Volume`, `Position`.

### Solution
- **3 new Pydantic models:**
  - `Spread(token_id, bid, ask, spread)` — bid-ask spread for a CLOB token. Two constructors: `from_cli()` (parses `clob spread` JSON) and `from_orderbook()` (derives bid/ask/spread from an OrderBook).
  - `Volume(event_id, total)` — aggregated volume for an event. `from_cli()` parses the nested `[{"markets": [...], "total": "..."}]` response.
  - `Position(market, outcome, shares, avg_price, current_price, pnl)` — open position for a wallet. `from_cli()` handles multiple field name conventions (e.g., `size`/`shares`, `avgPrice`/`avg_price`).
- **5 new client methods:**
  - `get_event(event_id)` → `Event | None` via `polymarket events get <id> -o json`
  - `get_price(token_id)` → `Spread` derived from order book (bid, ask, spread)
  - `get_spread(token_id)` → `Spread` via `polymarket clob spread <token_id> -o json`
  - `get_volume(event_id)` → `Volume` via `polymarket data volume <id> -o json`
  - `get_positions(address, limit)` → `list[Position]` via `polymarket data positions <addr> -o json`
- **7 new tests:** get_event, get_event_not_found, get_spread, get_price, get_volume, get_positions, get_positions_empty.

### Edits
- `src/polymarket_agent/data/models.py` — added `Spread`, `Volume`, `Position` models with `from_cli()` constructors
- `src/polymarket_agent/data/client.py` — added 5 new methods, updated imports
- `tests/test_client.py` — added mock data and 7 new test functions, extended `_mock_run` dispatcher

### NOT Changed
- No changes to strategies, execution, orchestrator, MCP server, or CLI.
- Existing models and methods untouched.
- MCP server does not yet expose the new methods as tools (future enhancement).

### Verification
```bash
uv run pytest tests/ -v           # 120 passed
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 21 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-26 (Phase 4 Follow-Up: Risk Gate Performance Optimization)

### Problem
- Previous follow-up review left a known performance risk: `tick()` called `_check_risk()` per signal, and `_check_risk()` recomputed daily loss (DB scan) and open-order count (executor/API call) each time.
- In live mode this could cause repeated CLOB API calls and unnecessary DB work for a single tick with many signals.

### Solution
- **Per-tick risk snapshot:** Added a private `_RiskSnapshot` dataclass and `_build_risk_snapshot()` helper so `tick()` computes `daily_loss` and `open_orders` once at the start of execution.
- **Snapshot reuse in order path:** `tick()` now passes the precomputed risk snapshot into `place_order(...)`, and `_check_risk(...)` consumes the snapshot instead of refetching DB/API state for each signal.
- **Incremental snapshot updates:** After each accepted order in `tick()`, `_update_risk_snapshot_after_order()` updates the cached `daily_loss` (buy increases loss, sell reduces it) and increments open-order count in live mode.
- **No behavior change for manual callers:** `Orchestrator.place_order()` still performs fresh risk checks when called without a snapshot (for example MCP manual trades).
- **Regression coverage:** Added a test that verifies `tick()` only calls `_calculate_daily_loss()` and `get_open_orders()` once across a multi-signal batch.

### Edits
- `src/polymarket_agent/orchestrator.py` — added `_RiskSnapshot`, `_build_risk_snapshot()`, `_update_risk_snapshot_after_order()`, and optional snapshot reuse in `place_order()` / `_check_risk()`
- `tests/test_risk_gate.py` — added per-tick risk snapshot reuse regression test
- `HANDOFF.md` — added this follow-up entry

### NOT Changed
- No changes to risk limit thresholds/semantics (`max_position_size`, `max_daily_loss`, `max_open_orders`).
- Snapshot open-order updates are intentionally conservative in live mode (increments after accepted order) rather than refetching from the API after each order.

### Verification
```bash
uv run pytest tests/test_risk_gate.py -q                      # 9 passed
uv run ruff check src/polymarket_agent/orchestrator.py tests/test_risk_gate.py   # All checks passed
uv run mypy src/polymarket_agent/orchestrator.py              # Success: no issues found in 1 source file
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-26 (Phase 4 Follow-Up: Review + Safety Fixes)

### Problem
- Reviewed the newly added Phase 4 live-trading/risk-management changes from the top handoff entry.
- Found correctness and safety gaps in the new code paths:
  - `LiveTrader` used `Signal.size` (USDC) directly as CLOB limit-order `size` instead of share quantity.
  - CLI live confirmation guard existed for `run` but could be bypassed via `tick`.
  - `Orchestrator.place_order()` bypassed the new risk gate, so manual callers (including MCP manual trades) could skip risk checks.

### Solution
- **LiveTrader order size fix (High):** Converted limit-order `OrderArgs.size` to share quantity (`signal.size / signal.target_price`) so live orders align with the project-wide `Signal.size` = USDC convention. Added a guard rejecting non-positive prices before API calls.
- **Risk gate centralization:** `Orchestrator.place_order()` now enforces monitor-mode and `_check_risk()` before delegating to the executor. `tick()` was simplified to reuse `place_order()` instead of duplicating risk logic in its execution loop.
- **CLI live safety tightened:** `polymarket-agent tick` now requires the same explicit `--live` confirmation flag as `run` when `mode: live`. `run` also performs the flag check before constructing the orchestrator, so it no longer fails early on missing live credentials before showing the safety message.
- **MCP lifecycle cleanup:** `mcp_server` lifespan now calls `orch.close()` in `finally`, ensuring DB/resources are released when the server shuts down.
- **Tests strengthened:** Added/updated regression coverage for live order share conversion, non-positive live price rejection, orchestrator manual-order risk gating, and `tick --live` confirmation.

### Edits
- `src/polymarket_agent/execution/live.py` — fixed limit-order size units (USDC -> shares), added non-positive price guard
- `src/polymarket_agent/orchestrator.py` — moved risk/mode checks into `place_order()` and simplified `tick()` to reuse it
- `src/polymarket_agent/cli.py` — early `run --live` guard before orchestrator construction; added `tick --live` confirmation guard
- `src/polymarket_agent/mcp_server.py` — added orchestrator cleanup in lifespan `finally`
- `tests/test_live_trader.py` — asserted CLOB `OrderArgs.size` receives share quantity; added invalid-price regression test
- `tests/test_risk_gate.py` — added manual `Orchestrator.place_order()` risk-gate regression test
- `tests/test_cli.py` — strengthened `run --live` test and added `tick --live` confirmation test
- `HANDOFF.md` — added this follow-up entry

### NOT Changed
- Did not refactor the risk gate performance path: `tick()` still recomputes daily loss / open orders per signal via `_check_risk()`, which may be expensive in live mode (DB scan + CLOB open-orders fetch each time).
- No full test suite rerun (focused verification only for reviewed/changed Phase 4 + CLI/MCP files).

### Verification
```bash
uv run pytest tests/test_live_trader.py tests/test_risk_gate.py tests/test_cli.py tests/test_mcp_server.py -q   # 44 passed
uv run ruff check src/polymarket_agent/cli.py src/polymarket_agent/orchestrator.py src/polymarket_agent/execution/live.py src/polymarket_agent/mcp_server.py tests/test_live_trader.py tests/test_risk_gate.py tests/test_cli.py   # All checks passed
uv run mypy src/polymarket_agent/cli.py src/polymarket_agent/orchestrator.py src/polymarket_agent/execution/live.py src/polymarket_agent/mcp_server.py   # Success: no issues found in 4 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-26 (Phase 4: Live Trading + Risk Management + Review Fixes)

### Problem
- Phase 4 pending: live order execution via py-clob-client, risk management enforcement, and several known issues from code review.
- Code review (Codex) flagged: prompt injection in AIAnalyst, orchestrator ignoring config mode, unused strategy config params.

### Solution

**Phase 4 Implementation:**
- **LiveTrader (`execution/live.py`)** — new Executor wrapping py-clob-client ClobClient for real order placement. Limit orders (GTC), trade logging to SQLite, cancel/open order support. Lazy imports for optional dependency. `from_env()` factory reads `POLYMARKET_PRIVATE_KEY` and optional `POLYMARKET_FUNDER` env vars.
- **Executor factory** — Orchestrator now selects PaperTrader or LiveTrader based on `config.mode`. Live mode imports LiveTrader lazily and calls `from_env()`.
- **Risk gate** — `_check_risk(signal)` method in Orchestrator enforces `max_position_size`, `max_daily_loss`, and `max_open_orders` before every trade execution. Daily loss calculated from same-day DB trades.
- **Executor ABC extension** — Added `cancel_order()` and `get_open_orders()` with default implementations (PaperTrader returns False/[]).
- **Database context manager** — `Database.__enter__`/`__exit__` for proper connection cleanup.
- **Subprocess timeout** — `_run_cli()` now has `timeout=30.0` parameter, raises RuntimeError on timeout.
- **CLI safety** — `polymarket-agent run --live` flag required for live mode. All CLI commands now call `orch.close()` in finally blocks.
- **py-clob-client optional dependency** — `pip install polymarket-agent[live]` installs py-clob-client.

**Code Review Fixes:**
- **Prompt injection (High)** — AIAnalyst now sanitizes market text: strips control characters, truncates to 500/1000 chars, uses explicit `--- BEGIN/END MARKET DATA ---` delimiters.
- **Arbitrageur min_deviation (Low)** — Now enforced: deviation must exceed both `price_sum_tolerance` AND `min_deviation` to generate a signal.
- **MarketMaker max_inventory (Low)** — Removed dead config parameter. Position-level limits handled by Orchestrator risk gate.
- **README AI docs (Medium)** — Skipped: code already gracefully degrades with clear error messages, per CLAUDE.md guidelines.

### Edits
- `pyproject.toml` — added `[project.optional-dependencies] live = ["py-clob-client>=0.0.1"]`
- `src/polymarket_agent/execution/base.py` — added `cancel_order()` and `get_open_orders()` to Executor ABC
- `src/polymarket_agent/execution/live.py` — **NEW** (LiveTrader with ClobClient wrapper)
- `src/polymarket_agent/orchestrator.py` — added `_build_executor()`, `_check_risk()`, `_calculate_daily_loss()`, `close()`; risk gate integrated into `tick()`
- `src/polymarket_agent/data/client.py` — added `timeout=30.0` to `_run_cli()`
- `src/polymarket_agent/db.py` — added `__enter__`/`__exit__` context manager
- `src/polymarket_agent/cli.py` — added `--live` flag to `run`, `orch.close()` in all commands
- `src/polymarket_agent/strategies/ai_analyst.py` — added `_sanitize_text()`, market text sanitization with delimiters
- `src/polymarket_agent/strategies/arbitrageur.py` — wired `_min_deviation` into signal gating logic
- `src/polymarket_agent/strategies/market_maker.py` — removed dead `_max_inventory` config
- `docs/plans/2026-02-26-polymarket-agent-phase4.md` — **NEW** (Phase 4 design doc)
- `tests/test_live_trader.py` — **NEW** (8 tests: init, from_env, place_order success/failure/rejected, cancel, open_orders)
- `tests/test_risk_gate.py` — **NEW** (7 tests: oversized, valid, daily loss, integrated tick, factory, live env, close)
- `tests/test_ai_analyst.py` — added sanitization test
- `tests/test_arbitrageur.py` — added min_deviation test
- `tests/test_market_maker.py` — updated config test to verify no max_inventory
- `tests/test_db.py` — added context manager test
- `tests/test_cli.py` — added --live flag test
- `tests/test_client.py` — added timeout test

### NOT Changed
- No changes to MCP server (already delegates to Orchestrator which now has the new executor/risk logic).
- No changes to strategy logic (except the 3 review fixes above).
- Pre-existing ruff isort issues in test_cli.py, test_client.py, test_db.py, test_paper_trader.py left as-is.

### Verification
```bash
uv run pytest tests/ -v           # 109 passed
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 21 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-26 (Phase 3 MCP Follow-Up: Signal Cache Split)

### Problem
- MCP `get_signals()` was implemented as a recompute path, so repeated read-only calls could run `AIAnalyst` and consume hourly AI quota / API cost.

### Solution
- **Split signal access into read-only vs explicit recompute:**
  1. `get_signals()` now returns the latest cached aggregated signal snapshot (no strategy execution).
  2. `refresh_signals()` explicitly recomputes signals and returns a fresh snapshot (may consume AI quota).
- **Cached signal snapshot in Orchestrator:** Added cached signal storage + timestamp, updated by both `tick()` and `generate_signals()`, so read-only consumers can inspect the latest computed signals safely.
- **Snapshot metadata:** MCP signal responses now include `source`, `last_updated`, and `freshness_seconds` alongside `signals`.
- **Tests updated:** Added coverage for cached-empty snapshots and explicit `refresh_signals()` recompute behavior.

### Edits
- `src/polymarket_agent/orchestrator.py` — added cached signal snapshot state + getters; cache updated from `tick()` and `generate_signals()`
- `src/polymarket_agent/mcp_server.py` — changed `get_signals()` to cached read-only snapshot; added `refresh_signals()` MCP tool and snapshot serialization helpers
- `tests/test_mcp_server.py` — updated `get_signals()` assertions and added `refresh_signals()` tests
- `HANDOFF.md` — added this entry and updated MCP tool counts/summary wording to include `refresh_signals()`

### NOT Changed
- No changes to trading execution semantics, strategy logic, or AIAnalyst rate-limit policy itself.
- Existing Phase 3 handoff entries remain as historical records (they describe the state before this MCP signal split).

### MCP API Note
- `get_signals()` response shape changed from `list[signal]` to a snapshot object:
  - `{"signals": [...], "source": "cache", "last_updated": <iso8601|null>, "freshness_seconds": <float|null>}`
- `get_signals()` is now read-only (cached snapshot only).
- Use `refresh_signals()` when a caller explicitly wants recomputation and accepts possible AI quota/API cost.
- MCP clients that previously iterated `get_signals()` directly as a list must be updated to read `result["signals"]`.

### Verification
```bash
uv run pytest tests/test_mcp_server.py -q                                    # 23 passed
uv run ruff check src/polymarket_agent/mcp_server.py src/polymarket_agent/orchestrator.py tests/test_mcp_server.py   # All checks passed
uv run mypy src/polymarket_agent/mcp_server.py src/polymarket_agent/orchestrator.py   # Success: no issues found in 2 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-26 (Phase 3 MCP Follow-Up: Review + Simplification)

### Problem
- Requested follow-up review of the newly added Phase 3 MCP code and `HANDOFF.md` to catch remaining issues and simplify where useful.

### Solution
- **Fixed MCP manual trade validation gap:** `place_trade()` now validates `side`, `size`, and `price` before building a `Signal` or calling the executor. This prevents invalid manual MCP requests (for example `price=0`) from causing downstream runtime errors such as division by zero in `PaperTrader`.
- **Simplified typing in `place_trade()`:** Replaced the `# type: ignore[arg-type]` with validated input + typed cast to `Literal["buy", "sell"]`.
- **Small MCP cleanup:** Added `_yes_price()` helper to remove repeated inline price extraction in `analyze_market()`.
- **More accurate AI availability error:** `analyze_market()` now reports AI analysis as unavailable when either `ANTHROPIC_API_KEY` is missing or the `anthropic` package is unavailable (previous message implied only the env var case).
- **Test coverage expanded:** Added MCP tool tests for zero-price and non-positive-size manual trades (test file now 21 tests).
- **HANDOFF cleanup:** Corrected stale `create_server()` references in the project summary/design notes to the current `configure()` + module-level `mcp` server pattern.

### Edits
- `src/polymarket_agent/mcp_server.py` — added manual trade input validation, removed `type: ignore`, improved AI unavailable error, extracted `_yes_price()` helper
- `tests/test_mcp_server.py` — added validation tests for zero price and non-positive size
- `HANDOFF.md` — added this entry and corrected stale MCP server factory wording in summary sections

### NOT Changed
- No changes to the data client, orchestrator, CLI wiring, or execution logic beyond rejecting invalid MCP manual trade inputs earlier.
- `get_signals()` still runs active strategies directly and may consume `AIAnalyst` hourly quota on read-only MCP calls (behavior/design follow-up, not changed here).
- No full-suite verification rerun (focused checks only for the touched MCP files).

### Verification
```bash
uv run pytest tests/test_mcp_server.py -q   # 21 passed
uv run ruff check src/polymarket_agent/mcp_server.py tests/test_mcp_server.py   # All checks passed
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-26 (Phase 3: MCP Server + Code Review Fixes)

### Problem
- Phase 3 pending: expose Polymarket data and trading as MCP tools for AI agents.

### Solution
- **MCP Server (`mcp_server.py`):** FastMCP server with lifespan context sharing an `AppContext` (Orchestrator + PolymarketData + AppConfig) across all 9 tools. Each tool is a thin wrapper that delegates to existing data/strategy/execution layers.
- **9 MCP tools implemented:**
  1. `search_markets(query, limit)` — keyword search in market questions via `PolymarketData.search_markets()`
  2. `get_market_detail(market_id)` — full market details + live orderbook
  3. `get_price_history(token_id, interval)` — historical price data
  4. `get_leaderboard(period)` — top traders from Polymarket leaderboard
  5. `get_portfolio()` — current balance, positions, recent trades
  6. `get_signals()` — returns the latest cached aggregated signal snapshot (read-only, no recompute)
  7. `refresh_signals()` — explicitly recomputes aggregated signals and updates the cache
  8. `place_trade(market_id, token_id, side, size, price)` — builds a Signal and delegates to executor; blocks in monitor mode
  9. `analyze_market(market_id)` — runs AIAnalyst on a single market; gracefully handles missing API key
- **Data layer additions:** Added `get_market()` (single market by ID), `search_markets()` (client-side filter), and `get_leaderboard()` (new CLI wrapper) to `PolymarketData`. Added `Trader` Pydantic model.
- **CLI integration:** New `polymarket-agent mcp` command using `configure()` + module-level `mcp` instance.
- **Dependency:** Added `mcp>=1.0` to pyproject.toml.

### Code Review Fixes
- **Bug: `create_server()` returned empty server** — Tools were registered on the module-level `mcp` instance, but `create_server()` returned a new `FastMCP` with no tools. Replaced with `configure()` setter + direct use of module-level `mcp`. CLI now calls `configure(); mcp.run()`.
- **Private attribute access** — Added public Orchestrator methods (`data`, `strategies`, `place_order()`, `generate_signals()`) so MCP tools no longer reach into `_strategies`, `_executor`, `_data`. `generate_signals()` also includes per-strategy try/except error handling.
- **Trader.from_cli falsy 0.0** — Fixed `or` chaining with explicit `in` checks so a trader with `pnl=0` or `marketsTraded=0` doesn't incorrectly fall through to the alternative field.
- **Market lookup limit** — Added `get_market(market_id)` direct-fetch method and `_find_market()` DRY helper so `get_market_detail` and `analyze_market` don't silently miss markets beyond the first 100.
- **Skipped: Orchestrator hardcodes PaperTrader** — Pre-existing issue, properly addressed in Phase 4 (Live Trading). Not introduced by Phase 3.
- **Skipped: ANTHROPIC_API_KEY documentation** — Code already gracefully degrades with clear error messages. Out of scope per CLAUDE.md guidelines.

### Edits
- `pyproject.toml` — added `mcp>=1.0` runtime dependency
- `src/polymarket_agent/data/models.py` — added `Trader` model, fixed `from_cli()` falsy 0.0 bug
- `src/polymarket_agent/data/client.py` — added `get_market()`, `search_markets()`, `get_leaderboard()`
- `src/polymarket_agent/orchestrator.py` — added public `data`, `strategies`, `place_order()`, `generate_signals()` methods
- `src/polymarket_agent/mcp_server.py` — **NEW** (MCP server with 9 tools, `configure()`, `_find_market()` helper)
- `src/polymarket_agent/cli.py` — added `mcp` command using `configure()` + module-level server
- `tests/test_client.py` — added tests for `search_markets()` and `get_leaderboard()`
- `tests/test_mcp_server.py` — **NEW** (now expanded beyond the original 19 tests; covers MCP tools including error paths)
- `docs/plans/2026-02-26-polymarket-agent-phase3.md` — **NEW** (implementation plan)

### NOT Changed
- No modifications to existing strategies or execution layer.
- Pre-existing ruff isort issues in test_cli.py, test_client.py, test_db.py left as-is.

### Verification
```bash
uv run pytest tests/ -v           # 85 passed
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 20 source files
uv run polymarket-agent --help    # Shows mcp command in CLI
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-25 (Phase 2 Complete: Integration Test + Final Verification)

### Problem
- Phase 2 Tasks 7–8 pending: integration test and final verification.

### Solution
- **Task 7 — Integration test:** End-to-end test running signal_trader + arbitrageur through the full orchestrator pipeline with mocked CLI data. Tests paper mode (verifies signals generated, trades executed, balance decreased) and monitor mode (verifies signals generated but zero trades). Uses `_pipeline()` context manager and `_STRATEGIES` constant for DRY setup.
- **Task 8 — Final verification:** Full test suite (63 passed), ruff check (clean), mypy strict (19 source files clean), smoke test with live data (50 markets, 9 signals, 5 trades).
- **Code review + simplification pass:** Tightened integration test assertions (paper mode balance check was always-true, monitor mode `>= 0` was trivially true). Extracted `_pipeline` context manager and `_make_analyst` helper for DRY. Added `token_id` param to aggregator test helper. Added docstrings to tests missing them.

### Edits
- `tests/test_integration.py` — **NEW** (2 integration tests with shared `_pipeline()` context manager)
- `tests/test_ai_analyst.py` — extracted `_make_analyst` helper, added docstring to rate-limit edge case test
- `tests/test_aggregator.py` — added `token_id` param to `_signal()` helper, added docstrings to new tests

### NOT Changed
- No source code changes in this batch (all production code was already complete from Tasks 1–6).
- Pre-existing ruff isort issues in test_cli.py, test_client.py, test_db.py left as-is.

### Verification
```bash
uv run pytest tests/ -v           # 63 passed
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 19 source files
uv run polymarket-agent tick      # 50 markets, 9 signals, 5 trades (live data)
```

### Branch
- Working branch: `phase2/strategy-modules` (14 commits ahead of `main`)
- Batch 3 commits:
  - `af43ce4` test: add integration test for full multi-strategy pipeline
  - `47d5d93` refactor: apply code review and simplification to batch 3

---

## Session Entry — 2026-02-25 (Phase 2 Batch 2 Review Follow-Up)

### Problem
- Review of the newly added Batch 2 code found two correctness issues:
- `aggregate_signals()` grouped by `(market_id, side)`, which merged different token IDs (for example AIAnalyst sell on Yes vs other strategy sell on No) and could deduplicate to the wrong instrument.
- `aggregate_signals()` enforced `min_strategies` using raw signal count, so duplicate signals from one strategy could incorrectly satisfy consensus.
- `AIAnalyst` only counted successful/parsible responses toward rate limiting, allowing repeated unparseable responses to exceed `max_calls_per_hour`.

### Solution
- Updated signal aggregation to group by `(market_id, token_id, side)` instead of `(market_id, side)`.
- Changed consensus enforcement to count unique strategy names per group.
- Updated `AIAnalyst` to count every attempted Claude call toward rate limiting (including parse failures/exceptions) via `finally` block.
- Added focused regression tests for both aggregation issues and the AIAnalyst rate-limit edge case.

### Edits
- `src/polymarket_agent/strategies/aggregator.py`
- `src/polymarket_agent/strategies/ai_analyst.py`
- `tests/test_aggregator.py`
- `tests/test_ai_analyst.py`

### Verification
- `uv run python -m pytest tests/test_ai_analyst.py tests/test_aggregator.py -q` (passes)

---

## Session Entry — 2026-02-25 (Phase 2 Batch 2: AIAnalyst + Aggregation + Config)

### Problem
- Phase 2 Tasks 4–6 pending: AIAnalyst strategy, signal aggregation, and config updates for new strategies.

### Solution
- **Task 4 — AIAnalyst:** New strategy that sends market questions to Claude and parses probability estimates. If the AI estimate diverges from market price by more than `min_divergence`, emits a buy or sell signal. Rate-limited via sliding-window `_call_timestamps`. Gracefully degrades when `ANTHROPIC_API_KEY` is unset or `anthropic` package is missing. Added `anthropic` runtime dependency.
- **Task 5 — Signal aggregation:** New `aggregate_signals()` function groups signals by `(market_id, token_id, side)`, deduplicates (keeps highest confidence per group), filters by `min_confidence`, and enforces `min_strategies` consensus (counts unique strategy names). Integrated into orchestrator `tick()` between strategy signal collection and execution.
- **Task 6 — Config updates:** Added `AggregationConfig` (Pydantic model with `min_confidence` and `min_strategies`) to `config.py`. Updated `config.yaml` with entries for all 4 strategies and aggregation settings. Wired orchestrator to read aggregation params from config.
- **Code review + simplification pass:** Fixed critical sell-signal semantics (sell targets Yes token, not No), tightened regex to only match [0, 1] probabilities, extracted `_mock_client` test helper, added sell/parse-failure/exception test cases.

### Edits
- `pyproject.toml` — added `anthropic` runtime dependency
- `src/polymarket_agent/strategies/ai_analyst.py` — **NEW** (AIAnalyst strategy)
- `src/polymarket_agent/strategies/aggregator.py` — **NEW** (signal aggregation)
- `src/polymarket_agent/orchestrator.py` — added AIAnalyst to `STRATEGY_REGISTRY`, integrated `aggregate_signals()` into `tick()`
- `src/polymarket_agent/config.py` — added `AggregationConfig` model
- `config.yaml` — added market_maker, arbitrageur, ai_analyst, aggregation sections
- `tests/test_ai_analyst.py` — **NEW** (8 tests)
- `tests/test_aggregator.py` — **NEW** (7 tests)
- `tests/test_config.py` — added aggregation config assertions

---

## Project Summary

**Polymarket Agent** is a Python auto-trading pipeline for Polymarket prediction markets. It wraps the official `polymarket` CLI (v0.1.4, installed via Homebrew) into a structured system with pluggable trading strategies, paper/live execution, and MCP server integration for AI agents.

## Current State: Phase 4 COMPLETE

- 120 tests passing, ruff lint clean, mypy strict clean (21 source files)
- All 4 strategies implemented: SignalTrader, MarketMaker, Arbitrageur, AIAnalyst
- Signal aggregation integrated (groups by market+token+side, unique strategy consensus)
- MCP server with 9 tools: search_markets, get_market_detail, get_price_history, get_leaderboard, get_portfolio, get_signals, refresh_signals, place_trade, analyze_market
- LiveTrader with py-clob-client for real order execution
- Risk management: max_position_size, max_daily_loss, max_open_orders enforced in Orchestrator
- CLI commands: `run` (with `--live` safety flag), `status`, `tick`, `mcp`

## Architecture

```
CLI (Typer) → Orchestrator → Data Layer (CLI wrapper + cache + 30s timeout)
                           → Strategy Engine (pluggable ABCs)
                           → Signal Aggregation (dedup, confidence, consensus)
                           → Risk Gate (position size, daily loss, open orders)
                           → Execution Layer (Paper/Live via factory)
                           → SQLite (trade logging, context manager)

MCP Server (FastMCP, stdio) → AppContext → Orchestrator + Data + Config
  ├── search_markets, get_market_detail, get_price_history, get_leaderboard
  ├── get_portfolio, get_signals, refresh_signals
  ├── place_trade
  └── analyze_market
```

## File Map

| File | Purpose |
|------|---------|
| `src/polymarket_agent/cli.py` | Typer CLI: `run`, `status`, `tick`, `mcp` commands |
| `src/polymarket_agent/orchestrator.py` | Main loop: fetch → analyze → aggregate → execute |
| `src/polymarket_agent/config.py` | Pydantic config from YAML (incl. AggregationConfig) |
| `src/polymarket_agent/data/models.py` | Market, Event, OrderBook, PricePoint, Trader, Spread, Volume, Position (Pydantic) |
| `src/polymarket_agent/data/client.py` | CLI wrapper with TTL caching + 12 public methods |
| `src/polymarket_agent/data/cache.py` | In-memory TTL cache |
| `src/polymarket_agent/db.py` | SQLite trade logging |
| `src/polymarket_agent/strategies/base.py` | Strategy ABC + Signal dataclass |
| `src/polymarket_agent/strategies/signal_trader.py` | Volume-filtered directional signals |
| `src/polymarket_agent/strategies/market_maker.py` | Bid/ask around orderbook midpoint |
| `src/polymarket_agent/strategies/arbitrageur.py` | Price-sum deviation detection |
| `src/polymarket_agent/strategies/ai_analyst.py` | Claude probability estimates + divergence trading |
| `src/polymarket_agent/strategies/aggregator.py` | Signal dedup, confidence filtering, consensus |
| `src/polymarket_agent/execution/base.py` | Executor ABC + Portfolio + Order + cancel_order/get_open_orders |
| `src/polymarket_agent/execution/paper.py` | Paper trading with virtual USDC |
| `src/polymarket_agent/execution/live.py` | Live trading via py-clob-client (optional dep) |
| `src/polymarket_agent/mcp_server.py` | MCP server: 9 tools, lifespan context, module-level `mcp` + `configure()` |
| `config.yaml` | Default config (paper mode, $1000, 4 strategies, aggregation) |
| `pyproject.toml` | Project config (deps incl. mcp>=1.0, ruff, mypy) |

## Key Design Decisions

1. **All data comes from `polymarket` CLI** — subprocess with `-o json`, parsed through `PolymarketData._run_cli()`. Never call subprocess directly elsewhere.
2. **Null-safe parsing** — Polymarket CLI returns `null` for optional fields. Helper functions `_str_field()`, `_float_field()`, `_parse_json_field()` in models.py.
3. **Strategy ABC** — All strategies implement `analyze(markets, data) -> list[Signal]`. Register in `STRATEGY_REGISTRY` dict in `orchestrator.py`.
4. **Executor ABC** — `place_order(signal) -> Order | None`. Paper and Live share interface.
5. **Signal dataclass** — `strategy`, `market_id`, `token_id`, `side` (buy/sell), `confidence`, `target_price`, `size` (USDC), `reason`.
6. **Signal aggregation** — `aggregate_signals()` groups by `(market_id, token_id, side)`, deduplicates (highest confidence wins), filters by `min_confidence`, enforces `min_strategies` consensus (unique strategy names). Runs between strategy collection and execution in `tick()`.
7. **MCP server** — FastMCP with lifespan context. `AppContext` dataclass holds Orchestrator + PolymarketData + AppConfig. Tools are thin wrappers that delegate to existing layers. `configure()` sets config/db paths for the module-level `mcp` instance before `mcp.run()`.
8. **Executor factory** — `_build_executor()` in Orchestrator selects PaperTrader or LiveTrader based on `config.mode`. Live mode lazily imports LiveTrader and reads env vars.
9. **Risk gate** — `_check_risk()` enforces max_position_size, max_daily_loss, max_open_orders before every trade in `tick()`.
10. **LiveTrader** — Thin wrapper around py-clob-client ClobClient. Limit orders (GTC), lazy imports, `from_env()` factory for env var auth.

## Known Issues from Code Review

### Critical
1. ~~**mypy strict fails**~~ — FIXED in Phase 2 Task 1.
2. **OrderBook.midpoint/spread** — Returns 0.0 or negative when asks/bids empty. MarketMaker guards against it, but the model still allows it.
3. ~~**Risk config not enforced**~~ — FIXED in Phase 4. `_check_risk()` enforces all 3 limits.
4. **Signal.size semantics** — Ambiguous whether dollars or shares. (Currently: USDC dollars.)
5. ~~**Database connection never closed**~~ — FIXED in Phase 4. Context manager + `orch.close()` in CLI.

### Important
6. ~~**No subprocess timeout**~~ — FIXED in Phase 4. `_run_cli()` has 30s timeout.
7. **Empty token_id signals** — SignalTrader emits `""` token_id when `clob_token_ids` missing.
8. **No sell-side test coverage** — Paper trader sell path untested.
9. **Config path relative to CWD** — Silently falls back to defaults.
10. ~~**MarketMaker `_max_inventory` not enforced**~~ — FIXED in Phase 4. Removed dead config; position limits enforced by Orchestrator risk gate.
11. ~~**Arbitrageur `_min_deviation` not used**~~ — FIXED in Phase 4. Now used in signal gating logic.
12. ~~**Prompt injection surface**~~ — FIXED in Phase 4. AIAnalyst sanitizes external text (strip control chars, truncate, delimiters).
13. ~~**MCP tools access private attributes**~~ — FIXED in Phase 3. Public methods added to Orchestrator.

## How to Work With This Codebase

### Setup
```bash
cd /Users/pharrelly/codebase/github/polymarket-agent
uv sync
```

### Run Tests
```bash
uv run pytest tests/ -v                    # all 120 tests
```

### Lint & Type Check
```bash
uv run ruff check src/                     # lint (currently clean)
uv run mypy src/                           # type check (currently clean)
```

### Smoke Test
```bash
uv run polymarket-agent tick               # single trading cycle with live data
uv run polymarket-agent status             # portfolio view
uv run polymarket-agent run                # continuous loop (Ctrl+C to stop)
uv run polymarket-agent mcp                # start MCP server (stdio transport)
```

### Adding a New Strategy
1. Create `src/polymarket_agent/strategies/<name>.py` implementing `Strategy` ABC
2. Add to `STRATEGY_REGISTRY` in `orchestrator.py`
3. Add config block in `config.yaml` under `strategies:`
4. Write tests in `tests/test_<name>.py`

### Using the MCP Server
The MCP server runs via stdio transport. To use with Claude Code, add to your MCP config:
```json
{
  "mcpServers": {
    "polymarket": {
      "command": "uv",
      "args": ["run", "polymarket-agent", "mcp"]
    }
  }
}
```

## Phase Plan

| Phase | Status | Description |
|-------|--------|-------------|
| **1: Data + Paper Trading** | COMPLETE | CLI wrapper, models, cache, paper executor, SignalTrader, orchestrator, CLI |
| **2: Strategy Modules** | COMPLETE | MarketMaker, Arbitrageur, AIAnalyst, signal aggregation, config, integration test |
| **3: MCP Server** | COMPLETE | 9 MCP tools, lifespan context, CLI integration, expanded MCP test coverage |
| **4: Live Trading** | COMPLETE | LiveTrader (py-clob-client), risk management, executor factory, DB cleanup, CLI safety |

## Phase 3 Implementation Plan

See `docs/plans/2026-02-26-polymarket-agent-phase3.md` for detailed plan with 6 tasks:

1. ~~Add mcp dependency + data layer additions~~ DONE
2. ~~MCP server core — lifespan + read-only tools~~ DONE
3. ~~MCP portfolio, signals, trading tools~~ DONE
4. ~~MCP analyze_market tool~~ DONE
5. ~~CLI integration + config~~ DONE
6. ~~Tests + final verification~~ DONE

## Dependencies

**Runtime:** pydantic>=2.0, pyyaml>=6.0, typer>=0.9, anthropic>=0.84, mcp>=1.0
**Optional (live):** py-clob-client>=0.0.1 (`pip install polymarket-agent[live]`)
**Dev:** pytest>=8.0, pytest-mock>=3.0, ruff>=0.4, mypy>=1.10, types-PyYAML
**External:** `polymarket` CLI (Homebrew: `brew install polymarket`)

## Design Documents

- `docs/plans/2026-02-25-polymarket-agent-trader-design.md` — Full system design (architecture, all phases)
- `docs/plans/2026-02-25-polymarket-agent-phase1.md` — Phase 1 implementation plan (COMPLETE)
- `docs/plans/2026-02-25-polymarket-agent-phase2.md` — Phase 2 implementation plan (COMPLETE)
- `docs/plans/2026-02-26-polymarket-agent-phase3.md` — Phase 3 implementation plan (COMPLETE)

## Verification Commands

```bash
# Everything should pass
uv run pytest tests/ -v           # 120 tests passing
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 21 source files
uv run polymarket-agent tick      # Fetches live data, paper trades
uv run polymarket-agent mcp       # Starts MCP server (stdio transport)
uv run polymarket-agent run --live  # Live trading (requires POLYMARKET_PRIVATE_KEY)
```
