# Polymarket Agent — Handoff Document

Last updated: 2026-02-28

---

## Session Entry — 2026-02-28 (Review Follow-Up: News `max_results` Wiring)

### Problem
- Follow-up review of the TA+news additions found a config/application mismatch: `news.max_results` existed in config, but AIAnalyst always queried news providers with a hardcoded `max_results=5`.
- This made `news.max_results` effectively ignored for prompt enrichment behavior.

### Solution
- **TDD regression first:** Added a failing AIAnalyst test verifying that attached news providers are called with the configured max-result count.
- **Minimal AIAnalyst update:** Extended `set_news_provider(...)` with optional `max_results`, persisted as strategy state, and used it in `_fetch_news()`.
- **Orchestrator wiring:** When attaching the shared news provider to AIAnalyst, pass `config.news.max_results` through.

### Edits
- `src/polymarket_agent/strategies/ai_analyst.py` — added configurable `_news_max_results`, updated `set_news_provider(...)`, and removed hardcoded `5` in `_fetch_news()`
- `src/polymarket_agent/orchestrator.py` — pass `max_results=self._config.news.max_results` when wiring news provider into AIAnalyst
- `tests/test_ai_analyst.py` — added `test_prompt_uses_configured_news_max_results`
- `HANDOFF.md` — added this review follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No changes to technical indicator formulas or TechnicalAnalyst signal logic.
- No changes to provider implementations (`google_rss`, `tavily`, cache/rate-limit behavior).
- No DB schema changes.

### Verification
```bash
uv run python -m pytest tests/test_ai_analyst.py::test_prompt_uses_configured_news_max_results -q   # failed first, then passed after fix
uv run python -m pytest tests/test_ai_analyst.py tests/test_orchestrator.py -q                       # 28 passed
ruff check src/polymarket_agent/strategies/ai_analyst.py src/polymarket_agent/orchestrator.py tests/test_ai_analyst.py  # All checks passed
./.venv/bin/mypy src/polymarket_agent/strategies/ai_analyst.py src/polymarket_agent/orchestrator.py  # Success: no issues found in 2 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-28 (Autotune: TA + News Parameter Support)

### Problem
- The autotune system uses a hardcoded list of known parameter names in `_build_tunable_params()`. New parameters from the TechnicalAnalyst strategy (`ema_fast_period`, `ema_slow_period`, `rsi_period`, `history_fidelity`) and the AIAnalyst strategy (`min_divergence`, `max_calls_per_hour`) were invisible to the tuner.
- The entire `news` config section (`max_calls_per_hour`, `cache_ttl`, `max_results`) was also not exposed as tunable.
- The LLM system prompt in `autotune.py` had no guidance about the new parameter types or their trade-offs.

### Solution
- **Extended `_build_tunable_params()`:** Added 7 new parameter checks in the per-strategy loop: `ema_fast_period` (3–15), `ema_slow_period` (10–50), `rsi_period` (5–30), `history_fidelity` (15–240), `min_divergence` (0.05–0.40), `max_calls_per_hour` (5–100). Added news config section with 3 params gated by `news.enabled`: `max_calls_per_hour` (10–200), `cache_ttl` (60–3600), `max_results` (1–10).
- **Updated `_SYSTEM_PROMPT`:** Added "PARAMETER GUIDANCE" section explaining what each parameter group controls and how to reason about trade-offs (e.g. shorter EMA = more responsive but noisier, EMA fast must be < slow, higher `min_divergence` = fewer but higher-conviction trades).
- **Updated README:** Added News Provider section, TechnicalAnalyst strategy docs, updated architecture diagram, config reference, project structure, roadmap, and tech stack.

### Edits
- `src/polymarket_agent/cli.py` — added 7 new strategy param checks + 3 news param checks (gated) in `_build_tunable_params()`
- `src/polymarket_agent/autotune.py` — added "PARAMETER GUIDANCE" section to `_SYSTEM_PROMPT`
- `tests/test_evaluate.py` — added 5 new tests: `test_build_tunable_params_includes_technical_analyst_params`, `test_build_tunable_params_includes_ai_analyst_params`, `test_build_tunable_params_includes_news_params_when_enabled`, `test_build_tunable_params_no_news_params_when_disabled`
- `README.md` — added TechnicalAnalyst strategy, News Provider section, updated architecture diagram/description, config reference, project structure, roadmap, tech stack

### NOT Changed
- No changes to `scripts/autotune.sh` — it calls CLI commands which now automatically include the new parameters.
- No changes to `autotune.py` validation logic — existing min/max clamping handles new params.
- No changes to strategy logic, execution layer, or DB schema.

### Verification
```bash
uv run pytest tests/test_evaluate.py tests/test_autotune.py -v   # 38 passed
uv run pytest tests/ -q                                           # 390 passed
ruff check src/                                                    # All checks passed
uv run mypy src/                                                   # Success: no issues found in 43 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-28 (Review Follow-Up: AI Analyst News Prompt Sanitization)

### Problem
- Reviewed the newest TA+news enhancement and found that news headline text was inserted into AIAnalyst prompts without sanitization.
- External headline strings could include control characters (or other noisy text) and bypass the existing `_sanitize_text()` safeguards used for market question/description.

### Solution
- **TDD regression first:** Added a failing test that injects control characters into a news headline and verifies they do not appear in the final LLM prompt.
- **Minimal hardening fix:** Updated AIAnalyst news prompt formatting to sanitize `title` and `published` fields before interpolation.
- **Scoped limits for clarity:** Added dedicated max-length constants for news title/date prompt fields.

### Edits
- `src/polymarket_agent/strategies/ai_analyst.py` — sanitize news title/date in `_format_news_summary()` and add `_MAX_NEWS_TITLE_LEN` / `_MAX_NEWS_PUBLISHED_LEN`
- `tests/test_ai_analyst.py` — added `test_prompt_sanitizes_news_titles`
- `HANDOFF.md` — added this review follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No changes to technical indicator computations, TechnicalAnalyst signal logic, or news provider selection/rate-limiting.
- No changes to orchestrator strategy wiring or DB schema.

### Verification
```bash
uv run python -m pytest tests/test_ai_analyst.py::test_prompt_sanitizes_news_titles -q                               # failed first, then passed after fix
uv run python -m pytest tests/test_ai_analyst.py tests/test_news_provider.py tests/test_technical_analyst.py tests/test_indicators.py -q  # 65 passed
ruff check src/polymarket_agent/strategies/ai_analyst.py tests/test_ai_analyst.py                                     # All checks passed
./.venv/bin/mypy src/polymarket_agent/strategies/ai_analyst.py                                                        # Success: no issues found in 1 source file
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-28 (Technical Analysis + News Intelligence Enhancement)

### Problem
- The AI Analyst strategy received only market question, description, and current price when asking the LLM for probability estimates — no quantitative price history context and no real-world news context.
- `DataProvider.get_price_history()` existed but no strategy used it.
- No mechanism to fetch recent news headlines relevant to each market.

### Solution
- **Technical indicators module (Step 1):** New `strategies/indicators.py` with pure-computation EMA, RSI (+ Stochastic RSI), Bollinger Band squeeze detection. Returns `TechnicalContext` Pydantic model. Minimum 21 data points required.
- **News provider package (Step 2):** New `news/` package with `NewsProvider` protocol, Google News RSS implementation (free, uses `feedparser`), optional Tavily implementation, and cached+rate-limited wrapper reusing existing `TTLCache`.
- **TechnicalAnalyst strategy (Step 3):** New rule-based strategy implementing `Strategy` ABC. Generates buy/sell signals on EMA crossover + RSI + squeeze confluence. Confidence weighted: EMA divergence (0.4) + RSI extremity (0.3) + squeeze confirmation (0.3).
- **Enhanced AI Analyst prompt (Step 4):** Modified `_evaluate()` to accept `DataProvider`, fetch price history + compute TA summary, fetch news headlines. Both sections are optional — graceful degradation. Increased `max_tokens` 16→32.
- **Wiring (Step 5):** Registered `TechnicalAnalyst` in `STRATEGY_REGISTRY`. Added `NewsConfig` to `AppConfig`. Orchestrator builds news provider and wires it into AI Analyst. Updated `config.yaml` with new sections. Added `feedparser>=6.0` dependency.

### Edits
- `src/polymarket_agent/strategies/indicators.py` — **NEW** EMA, RSI, Squeeze computations + TechnicalContext model
- `src/polymarket_agent/strategies/technical_analyst.py` — **NEW** rule-based TA strategy
- `src/polymarket_agent/news/__init__.py` — **NEW** news package init
- `src/polymarket_agent/news/models.py` — **NEW** NewsItem model
- `src/polymarket_agent/news/provider.py` — **NEW** NewsProvider protocol
- `src/polymarket_agent/news/google_rss.py` — **NEW** Google News RSS implementation
- `src/polymarket_agent/news/tavily_client.py` — **NEW** Tavily implementation (optional)
- `src/polymarket_agent/news/cached.py` — **NEW** cached + rate-limited wrapper
- `src/polymarket_agent/strategies/ai_analyst.py` — added TA context, news context, `set_news_provider()`, enriched prompt
- `src/polymarket_agent/orchestrator.py` — registered TechnicalAnalyst, added `_build_news_provider()`, wire news into AIAnalyst
- `src/polymarket_agent/config.py` — added `NewsConfig` model to `AppConfig`
- `config.yaml` — added `technical_analyst` strategy + `news` config sections, set `min_strategies: 2`
- `pyproject.toml` — added `feedparser>=6.0` dependency, `tavily` optional dep
- `tests/test_indicators.py` — **NEW** 18 indicator tests
- `tests/test_news_provider.py` — **NEW** 12 news provider tests
- `tests/test_technical_analyst.py` — **NEW** 12 TA strategy tests
- `tests/test_ai_analyst.py` — added 5 new enrichment tests
- `docs/plans/2026-02-28-ta-and-news.md` — **NEW** design document

### NOT Changed
- No changes to existing strategy logic (SignalTrader, MarketMaker, Arbitrageur)
- No changes to execution layer, MCP server, or dashboard
- No changes to DB schema
- All existing tests unaffected (backward-compatible changes)

### Verification
```bash
uv run pytest tests/ -v                     # 367 passed
ruff check src/                              # All checks passed
uv run mypy src/                             # Only pre-existing dashboard errors (12)
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Review Follow-Up: Conditional Order Sibling Cleanup)

### Problem
- Reviewed the newest runtime-fix additions and found a cleanup gap in conditional order execution.
- When one conditional order triggered and fully closed a position, sibling conditional orders for the same token could remain active.
- Those leftover siblings had no valid position to protect and could become stale noise later.

### Solution
- **TDD regression first:** Added a failing orchestrator test that reproduces the sibling-order leftover case.
- **Minimal orchestrator fix:** After a conditional order executes, `_check_conditional_orders()` now checks whether the position is still held; if not, it cancels remaining active conditional orders for that token via `cancel_conditional_orders_for_token()`.
- **Small test cleanup:** Removed one pre-existing unused local variable flagged by `ruff`.

### Edits
- `src/polymarket_agent/orchestrator.py` — cancel sibling active conditional orders when a triggered conditional sell closes the position
- `tests/test_orders.py` — added `test_triggered_order_cancels_sibling_orders_when_position_closes`; removed unused `order_id` local in trailing-stop test
- `HANDOFF.md` — added this review follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No changes to conditional-order trigger thresholds, order types, or DB schema.
- No changes to strategy generation, position sizing, or paper-trader fill math.

### Verification
```bash
uv run python -m pytest tests/test_orders.py::TestConditionalOrderChecking::test_triggered_order_cancels_sibling_orders_when_position_closes -q   # failed first, then passed after fix
uv run python -m pytest tests/test_orders.py -q                                                                                                    # 16 passed
ruff check src/polymarket_agent/orchestrator.py tests/test_orders.py                                                                               # All checks passed
./.venv/bin/mypy src/polymarket_agent/orchestrator.py                                                                                              # Success: no issues found in 1 source file
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-28 (Runtime Fixes: .env Loading, AI Analyst, Zero-Price Writeoff, Stale Orders)

### Problem
- `OPENAI_API_KEY` was configured in `.env` but never loaded — `os.environ.get()` couldn't see it.
- AI analyst returned empty responses from the OpenAI proxy, causing "Could not parse probability" warnings every tick.
- Positions that hit zero price caused an infinite loop: ExitManager generated a sell signal, paper trader rejected it (price ≤ 0), position stayed open, repeat every tick.
- Conditional orders (stop-loss/take-profit) for closed positions spammed "No position to sell" warnings every tick because they were never cancelled.

### Solution
- **python-dotenv integration:** Added `python-dotenv>=1.0` dependency. `load_config()` now calls `load_dotenv()` before parsing YAML, making `.env` vars available to all code paths.
- **AI analyst LLM hardening:** Added `temperature=0` for deterministic output, bumped `max_tokens` 10→16, added system message for OpenAI path, handled `None` content from API.
- **Zero-price writeoff:** Split `<= 0` guard into `< 0` (reject as invalid) and `== 0` (write off position). Writeoff removes position, logs cost basis for PnL traceability, and returns an Order so downstream cleanup runs.
- **Stale conditional order cleanup:** Added `cancel_conditional_orders_for_token()` DB method. Orchestrator now: (1) cancels stale orders before attempting execution if position is gone, (2) cancels all related orders after an exit trade executes.
- **Code simplification:** Used `dataclasses.replace()` instead of manual Signal reconstruction in paper trader sell path.

### Edits
- `pyproject.toml` — added `python-dotenv>=1.0` dependency
- `uv.lock` — updated lockfile
- `src/polymarket_agent/config.py` — imported `load_dotenv`, call it in `load_config()`
- `src/polymarket_agent/strategies/ai_analyst.py` — `temperature=0`, `max_tokens=16`, system message, `None` content handling in `_call_llm()`
- `src/polymarket_agent/execution/paper.py` — zero-price writeoff with cost basis, negative price rejection, `dataclasses.replace()` simplification
- `src/polymarket_agent/db.py` — added `cancel_conditional_orders_for_token()` method
- `src/polymarket_agent/orchestrator.py` — stale order cancellation in `_check_conditional_orders()`, order cleanup after exit trades
- `tests/test_paper_trader.py` — added `test_paper_trader_sell_writeoff_at_zero_price` and `test_paper_trader_sell_negative_price_rejected`

### NOT Changed
- No changes to strategy logic, exit rules, or signal aggregation.
- No changes to MCP server, dashboard, or live trader.
- No DB schema changes (only new query method).

### Verification
```bash
uv run pytest tests/ -v              # 337 passed
uv run ruff check src/               # All checks passed
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Review Follow-Up: Exit Manager Numeric Robustness)

### Problem
- Review of the newest Exit Manager additions found a robustness gap: `ExitManager.evaluate()` and `_check_exit()` assumed numeric `avg_price`/`shares` values.
- If recovered or externally mutated position metadata contains malformed numeric strings (for example `"avg_price": "bad"`), exit evaluation raises `ValueError` and can break the tick cycle.

### Solution
- **TDD regression coverage first:** Added failing tests for malformed `avg_price` and malformed `shares` in exit-manager position payloads.
- **Safe numeric parsing:** Added `_as_float(...)` helper and switched relevant parsing paths to skip malformed positions instead of raising:
  - `evaluate()` shares parsing
  - `_check_exit()` average price parsing
  - `_check_signal_reversal()` arbitrage average price parsing
- **Behavior preserved:** Valid positions and rule priority/threshold behavior remain unchanged.

### Edits
- `src/polymarket_agent/strategies/exit_manager.py` — added `_as_float(...)` and hardened numeric parsing in exit evaluation paths
- `tests/test_exit_manager.py` — added `TestMalformedPositionData` with 2 regression tests
- `HANDOFF.md` — added this review follow-up entry

### NOT Changed
- No changes to exit rule thresholds, ordering, or signal schema.
- No orchestrator, DB, or paper execution logic changes in this follow-up.

### Verification
```bash
uv run python -m pytest tests/test_exit_manager.py -q   # 13 passed
./.venv/bin/ruff check src/polymarket_agent/strategies/exit_manager.py tests/test_exit_manager.py   # All checks passed
./.venv/bin/mypy src/polymarket_agent/strategies/exit_manager.py   # Success: no issues found in 1 source file
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Exit Manager: Position Exit Logic)

### Problem
- The paper trading bot got permanently stuck once positions filled up. Strategies only generated buy/entry signals. The risk gate rejected all buy signals for held positions ("already holding position"). No mechanism existed to generate sell signals, so positions never closed. Conditional orders had 50% bands that rarely triggered.

### Solution
- **ExitManagerConfig (Task 1):** Added `ExitManagerConfig` Pydantic model to `config.py` with fields: `enabled`, `profit_target_pct` (0.15), `stop_loss_pct` (0.12), `signal_reversal` (true), `max_hold_hours` (24). Tightened conditional order bands from 50% to 12-15%.
- **Position metadata (Task 2):** Added `opened_at` (ISO timestamp) and `entry_strategy` (strategy name) to PaperTrader position dicts. Backfills sensible defaults for positions recovered from DB snapshots.
- **ExitManager class (Task 3):** New `strategies/exit_manager.py` with 4 exit rules evaluated in priority order (first match wins): profit target (+15%), stop loss (-12%), signal reversal (entry thesis invalidated), stale position (held >24h). Returns sell `Signal` objects.
- **Orchestrator integration (Task 4):** ExitManager runs in `tick()` after entry strategies. Exit signals execute before entries, bypass risk gate/position sizing/aggregator. `_evaluate_exits()` helper fetches bid prices for held positions. ExitManager rebuilt on config hot-reload.
- **Full verification (Tasks 5-6):** 333 tests pass, ruff clean, mypy clean. DB reset + end-to-end tick verified fresh buys execute and exit rules evaluate correctly.

### Edits
- `src/polymarket_agent/config.py` — added `ExitManagerConfig` model, wired into `AppConfig`
- `config.yaml` — added `exit_manager` section; tightened `conditional_orders.default_stop_loss_pct` (0.5→0.12) and `default_take_profit_pct` (0.5→0.15)
- `src/polymarket_agent/execution/paper.py` — added `opened_at`/`entry_strategy` to `_execute_buy`; backfill in `recover_from_db()`
- `src/polymarket_agent/strategies/exit_manager.py` — **NEW** ExitManager with 4 exit rules
- `src/polymarket_agent/orchestrator.py` — integrated ExitManager into `tick()` loop; added `_evaluate_exits()` helper; rebuild on `reload_config()`
- `tests/test_config.py` — added `test_exit_manager_config_defaults`
- `tests/test_paper_trader.py` — added 2 metadata tests (buy sets metadata, recover backfills)
- `tests/test_exit_manager.py` — **NEW** 11 tests across 6 test classes (profit target, stop loss, signal reversal, staleness, disabled, priority)
- `tests/test_orchestrator.py` — added `test_orchestrator_exit_manager_generates_sells`
- `docs/plans/2026-02-27-exit-manager-design.md` — **NEW** design document
- `docs/plans/2026-02-27-exit-manager-plan.md` — **NEW** implementation plan

### NOT Changed
- No changes to entry signal pipeline (risk gate, position sizing, aggregation all preserved)
- No changes to strategy implementations (SignalTrader, Arbitrageur, etc.)
- No changes to data layer, MCP server, or live trader
- No changes to existing tests or their assertions

### Verification
```bash
uv run pytest tests/ -v              # 333 passed
ruff check src/                       # All checks passed
uv run mypy src/                      # Success: no issues found in 35 source files
uv run polymarket-agent tick          # 7 trades executed on fresh DB
uv run polymarket-agent status        # Shows 7 positions with P&L
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (Review Follow-Up: Provider Validation Hardening)

### Problem
- Review of the new OpenAI-compatible support found a config-safety gap: unknown provider strings were silently routed down OpenAI paths instead of being rejected/falling back predictably.
- Impact:
  - `autotune` could fail with misleading API-key/import errors for typoed providers.
  - `AIAnalyst` could hold an invalid provider value and behave unexpectedly.
  - CLI `autotune` would do full evaluation work before surfacing provider mistakes.

### Solution
- **autotune provider validation:** Added explicit provider normalization/validation in `_init_client()` and raise `ValueError` for unsupported providers.
- **AIAnalyst provider normalization:** `configure()` now lowercases/validates provider and falls back to default (`anthropic`) with a warning on unknown values.
- **Defensive init guard:** `_init_client()` in `AIAnalyst` now has an explicit unknown-provider fallback branch.
- **CLI fast-fail validation:** `polymarket-agent autotune` now validates `--provider` early and raises `BadParameter` before orchestrator/evaluation work.
- **Regression tests:** Added targeted tests for unknown-provider rejection/fallback and CLI validation path.

### Edits
- `src/polymarket_agent/autotune.py` — added `_SUPPORTED_PROVIDERS` and strict provider validation in `_init_client()`
- `src/polymarket_agent/strategies/ai_analyst.py` — added provider normalization/validation + fallback safeguards
- `src/polymarket_agent/cli.py` — added early `--provider` validation in `autotune` command
- `tests/test_autotune.py` — added unknown-provider `_init_client` test and CLI invalid-provider test
- `tests/test_ai_analyst.py` — added invalid-provider fallback test
- `HANDOFF.md` — added this review follow-up entry; removed oldest session entry to keep last 10 entries

### NOT Changed
- No changes to provider API request payload shapes.
- No changes to autotune parameter validation/apply semantics.
- No changes to MCP tool behavior in this follow-up.

### Verification
```bash
uv run python -m pytest tests/test_ai_analyst.py tests/test_autotune.py -q   # 38 passed
./.venv/bin/ruff check src/polymarket_agent/autotune.py src/polymarket_agent/strategies/ai_analyst.py src/polymarket_agent/cli.py tests/test_ai_analyst.py tests/test_autotune.py   # All checks passed
./.venv/bin/mypy src/polymarket_agent/autotune.py src/polymarket_agent/strategies/ai_analyst.py src/polymarket_agent/cli.py   # Success: no issues found in 3 source files
```

### Branch
- Working branch: `main`

---

## Session Entry — 2026-02-27 (OpenAI-Compatible API Support)

### Problem
- AIAnalyst strategy was hardcoded to Anthropic SDK — no way to use OpenAI-compatible providers (OpenAI, local Ollama/vLLM endpoints).
- `autotune.sh` used `claude -p` which fails with 403 in non-interactive/launchd contexts. No way to use alternative LLM providers for auto-tuning.

### Solution
- **AIAnalyst provider abstraction (Step 1):** Added `_DEFAULT_PROVIDER`, `_provider`, `_base_url`, `_api_key_env` instance vars. Split `_init_client()` into `_init_anthropic_client()` / `_init_openai_client()`. Extracted `_call_llm()` to abstract API response shapes. `configure()` now reads `provider`, `base_url`, `api_key_env` and re-initializes the client when changed.
- **autotune.py module (Step 2):** New `src/polymarket_agent/autotune.py` with `run_autotune()` function. Builds system prompt with tuning rules, sends eval JSON to LLM, parses structured JSON response, validates changes against tunable parameter min/max ranges, applies changes to config.yaml via PyYAML. Supports both anthropic and openai providers.
- **CLI autotune subcommand (Step 3):** New `polymarket-agent autotune` command with `--provider`, `--model`, `--base-url`, `--api-key-env`, `--period` options. Runs evaluate logic internally then calls `run_autotune()`.
- **autotune.sh provider dispatch (Step 4):** `AUTOTUNE_PROVIDER` env var selects "claude" (original `claude -p` flow), "openai", or "anthropic" (new direct API flow via CLI subcommand). Also added `AUTOTUNE_MODEL`, `AUTOTUNE_BASE_URL`, `AUTOTUNE_API_KEY_ENV` env vars.
- **Config + deps (Step 5):** Added commented-out `provider`, `base_url`, `api_key_env` fields to `config.yaml` ai_analyst section. Added `openai = ["openai>=1.0"]` optional dependency group to pyproject.toml.
- **MCP docstring update (Step 6):** Made `analyze_market` docstring and error message provider-agnostic.
- **Tests (Step 7):** 7 new OpenAI provider tests in test_ai_analyst.py. 19 new tests in test_autotune.py (parsing, validation, config modification, integration). Updated test_mcp_server.py assertion for generic error message. All 315 tests pass.

### Edits
- `src/polymarket_agent/strategies/ai_analyst.py` — provider abstraction: `_init_anthropic_client()`, `_init_openai_client()`, `_call_llm()`, updated `configure()`
- `src/polymarket_agent/autotune.py` — **NEW** LLM-based config auto-tuner module
- `src/polymarket_agent/cli.py` — added `autotune` subcommand
- `scripts/autotune.sh` — provider dispatch (claude/openai/anthropic)
- `scripts/com.polymarket-agent.autotune.plist` — added env var placeholders for provider/model/keys
- `config.yaml` — commented-out provider fields under ai_analyst
- `pyproject.toml` — added `openai` optional dependency group
- `src/polymarket_agent/mcp_server.py` — provider-agnostic docstring and error message
- `tests/test_ai_analyst.py` — 7 new OpenAI provider tests
- `tests/test_autotune.py` — **NEW** 19 tests for autotune module
- `tests/test_mcp_server.py` — updated error message assertion

### NOT Changed
- `config.py` — `strategies: dict[str, dict[str, Any]]` already passes through arbitrary keys
- `orchestrator.py` — `_load_strategies()` already handles `cls()` + `configure()`
- `strategies/base.py` — Strategy ABC is flexible enough
- No changes to existing Anthropic behavior — default provider remains "anthropic"

### Verification
```bash
uv run pytest tests/ -v                  # 315 passed
uv run ruff check src/ tests/test_ai_analyst.py tests/test_autotune.py tests/test_mcp_server.py  # All checks passed
uv run mypy src/                         # Success: no issues found in 34 source files
uv run polymarket-agent autotune --help  # Shows autotune command
```

### Branch
- Working branch: `main`

---

## Project Summary

**Polymarket Agent** is a Python auto-trading pipeline for Polymarket prediction markets. It wraps the official `polymarket` CLI (v0.1.4, installed via Homebrew) into a structured system with pluggable trading strategies, paper/live execution, and MCP server integration for AI agents.

## Current State: Phase 4 COMPLETE

- 337 tests passing, ruff lint clean, mypy strict clean (35 source files)
- All 4 strategies implemented: SignalTrader, MarketMaker, Arbitrageur, AIAnalyst
- Signal aggregation integrated (groups by market+token+side, unique strategy consensus)
- MCP server with 14 tools: search_markets, get_market_detail, get_price_history, get_leaderboard, get_portfolio, get_signals, refresh_signals, place_trade, analyze_market, get_event, get_price, get_spread, get_volume, get_positions
- LiveTrader with py-clob-client for real order execution
- Risk management: max_position_size, max_daily_loss, max_open_orders enforced in Orchestrator
- CLI commands: `run` (with `--live` safety flag + config hot-reload), `status`, `tick`, `report`, `evaluate`, `backtest`, `dashboard`, `mcp`
- Auto-tune pipeline: `scripts/autotune.sh` + launchd plist for periodic Claude Code-driven config tuning
- Exit manager: automatic position exits via profit target, stop loss, signal reversal, and staleness rules

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
| `src/polymarket_agent/cli.py` | Typer CLI: `run` (hot-reload), `status`, `tick`, `report`, `evaluate`, `backtest`, `dashboard`, `mcp` |
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
| `src/polymarket_agent/strategies/exit_manager.py` | ExitManager: generates sell signals for held positions (4 exit rules) |
| `src/polymarket_agent/strategies/aggregator.py` | Signal dedup, confidence filtering, consensus |
| `src/polymarket_agent/execution/base.py` | Executor ABC + Portfolio + Order + cancel_order/get_open_orders |
| `src/polymarket_agent/execution/paper.py` | Paper trading with virtual USDC |
| `src/polymarket_agent/execution/live.py` | Live trading via py-clob-client (optional dep) |
| `src/polymarket_agent/mcp_server.py` | MCP server: 14 tools, lifespan context, module-level `mcp` + `configure()` |
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
2. ~~**OrderBook.midpoint/spread**~~ — FIXED. Returns 0.0 when either side is empty.
3. ~~**Risk config not enforced**~~ — FIXED in Phase 4. `_check_risk()` enforces all 3 limits.
4. ~~**Signal.size semantics**~~ — FIXED. Docstring clarifies size is always USDC; execution layer converts to shares.
5. ~~**Database connection never closed**~~ — FIXED in Phase 4. Context manager + `orch.close()` in CLI.

### Important
6. ~~**No subprocess timeout**~~ — FIXED in Phase 4. `_run_cli()` has 30s timeout.
7. ~~**Empty token_id signals**~~ — FIXED. SignalTrader now skips markets with missing token IDs.
8. ~~**No sell-side test coverage**~~ — FIXED. 5 sell-side PaperTrader tests added.
9. ~~**Config path relative to CWD**~~ — FIXED. CLI now logs a warning when config file not found.
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
uv run pytest tests/ -v                    # all 141 tests
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
- `docs/plans/2026-02-27-exit-manager-design.md` — Exit manager design document
- `docs/plans/2026-02-27-exit-manager-plan.md` — Exit manager implementation plan

## Verification Commands

```bash
# Everything should pass
uv run pytest tests/ -v           # 333 tests passing
uv run ruff check src/            # All checks passed
uv run mypy src/                  # Success: no issues found in 35 source files
uv run polymarket-agent tick      # Fetches live data, paper trades
uv run polymarket-agent mcp       # Starts MCP server (stdio transport)
uv run polymarket-agent run --live  # Live trading (requires POLYMARKET_PRIVATE_KEY)
```
