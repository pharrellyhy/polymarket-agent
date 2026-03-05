# Plan: Market Filtering Preferences + Configurable max_tokens

**Date:** 2026-03-05
**Status:** IMPLEMENTED

## Context

After 2.5 hours of running with structured prompts, the portfolio bled -2.5% ($405→$395). The agent analyzes all 50 markets equally—including sports markets where the LLM has no edge—and churns positions without clear direction. Adding market categorization, volume filtering, and trending prioritization will focus the LLM on markets where it has an edge. Additionally, `max_tokens` is hardcoded in the LLM call and needs to be configurable.

## Changes

### 1. Extend `Market` model — `src/polymarket_agent/data/models.py`

**Add fields** to `Market` class (after `volume_24h`):
- `one_day_price_change: float = 0.0`
- `is_new: bool = False`

**Add to `from_cli`:**
- `one_day_price_change=_float_field(data, "oneDayPriceChange")`
- `is_new=bool(data.get("new", False))`

**Add `categorize_market()` function** after the helper functions, before the `Market` class:
- Module-level `_CATEGORY_KEYWORDS` dict mapping category → keyword lists
- Categories: `politics`, `crypto`, `finance`, `tech`, `sports`, `entertainment`, `science`
- Function does lowercase substring matching on `market.question`, returns first match or `"other"`
- Priority order: politics > crypto > finance > tech > sports > entertainment > science

### 2. Extend config — `src/polymarket_agent/config.py`

**Add `CategoryConfig` model:**
```python
class CategoryConfig(BaseModel):
    preferred: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
```

**Extend `FocusConfig`** with new fields:
- `categories: CategoryConfig = Field(default_factory=CategoryConfig)`
- `min_volume_24h: float = 0.0`
- `prioritize_trending: bool = False`
- `fetch_limit: int = 50`

### 3. Configurable `max_tokens` — `src/polymarket_agent/strategies/ai_analyst.py`

**In `__init__`:** add `self._max_tokens: int = 1024`

**In `configure()`:** add `self._max_tokens = int(config.get("max_tokens", 1024))`

**In `_call_llm()`:** replace all hardcoded `max_tokens` values:
- Anthropic path: `max_tokens=self._max_tokens`
- OpenAI path: `max_tokens=self._max_tokens` (when structured_prompt is True)

### 4. Update orchestrator — `src/polymarket_agent/orchestrator.py`

**Import** `categorize_market` from `polymarket_agent.data.models`

**Pass `fetch_limit`** to `get_active_markets()` in both `tick()` and `generate_signals()`:
```python
markets = self._data.get_active_markets(limit=self._config.focus.fetch_limit)
```

**Extend `_apply_focus_filter()`** — add volume + category filtering BEFORE the `focus.enabled` guard so they work unconditionally:

```
Phase 1: Volume filter (min_volume_24h) — drop low-volume markets
Phase 2: Category filter — exclude categories, sort preferred first
Phase 3: Trending sort (prioritize_trending) — sort by volume_24h desc
Phase 4: Existing focus logic (IDs/slugs/queries) — unchanged
Phase 5: max_brackets truncation — unchanged
```

### 5. Update `config.yaml`

```yaml
focus:
  enabled: false
  search_queries: []
  max_brackets: 30
  fetch_limit: 100
  min_volume_24h: 500
  prioritize_trending: true
  categories:
    preferred: [politics, crypto, finance, tech]
    excluded: [sports]

# Under strategies.ai_analyst:
  max_tokens: 2048
```

### 6. Tests

- `tests/test_models.py` — test `categorize_market()` (politics, crypto, sports, other, case insensitive, priority), test new `Market` fields parse correctly
- `tests/test_config.py` — test `CategoryConfig` and extended `FocusConfig` defaults and YAML loading
- `tests/test_orchestrator.py` — test volume filter, category exclude filter, trending sort
- `tests/test_ai_analyst.py` — test `max_tokens` default, Anthropic flow-through, OpenAI flow-through

## File modification order

1. `src/polymarket_agent/data/models.py`
2. `src/polymarket_agent/config.py`
3. `src/polymarket_agent/strategies/ai_analyst.py`
4. `src/polymarket_agent/orchestrator.py`
5. `config.yaml`
6. Tests
7. `HANDOFF.md` — update with session entry

## Verification

```bash
uv run pytest tests/ -v                                    # all tests pass
uv run ruff check src/                                     # no lint errors
uv run polymarket-agent tick 2>&1 | head -30               # verify filtering logs
# Should see: "Volume filter: X → Y markets", "Category exclude filter: X → Y"
```
