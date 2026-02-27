# Exit Manager Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a dedicated ExitManager that generates sell signals for held positions, and tighten conditional order bands, so the paper trading bot doesn't get stuck once positions fill up.

**Architecture:** New `ExitManager` class evaluates held positions each tick against current prices. It runs in the orchestrator tick loop after entry strategies. Exit signals bypass the "already holding" risk gate and aggregator min_strategies requirement. Conditional order defaults are tightened as a backstop.

**Tech Stack:** Python 3.12+, Pydantic v2, dataclasses, pytest

---

### Task 1: Add ExitManagerConfig to config.py

**Files:**
- Modify: `src/polymarket_agent/config.py:61-73`
- Modify: `config.yaml`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_exit_manager_config_defaults():
    from polymarket_agent.config import AppConfig
    cfg = AppConfig()
    assert cfg.exit_manager.enabled is True
    assert cfg.exit_manager.profit_target_pct == 0.15
    assert cfg.exit_manager.stop_loss_pct == 0.12
    assert cfg.exit_manager.signal_reversal is True
    assert cfg.exit_manager.max_hold_hours == 24
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_exit_manager_config_defaults -v`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'exit_manager'`

**Step 3: Add ExitManagerConfig model and wire into AppConfig**

In `src/polymarket_agent/config.py`, add before `AppConfig`:

```python
class ExitManagerConfig(BaseModel):
    """Exit manager configuration."""

    enabled: bool = True
    profit_target_pct: float = 0.15
    stop_loss_pct: float = 0.12
    signal_reversal: bool = True
    max_hold_hours: int = 24
```

Add field to `AppConfig`:

```python
    exit_manager: ExitManagerConfig = Field(default_factory=ExitManagerConfig)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_exit_manager_config_defaults -v`
Expected: PASS

**Step 5: Update config.yaml**

Add `exit_manager` section and tighten conditional order bands:

```yaml
exit_manager:
  enabled: true
  profit_target_pct: 0.15
  stop_loss_pct: 0.12
  signal_reversal: true
  max_hold_hours: 24
```

Change `conditional_orders.default_stop_loss_pct` from `0.5` to `0.12` and `default_take_profit_pct` from `0.5` to `0.15`.

**Step 6: Run all config tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/polymarket_agent/config.py config.yaml tests/test_config.py
git commit -m "feat: add ExitManagerConfig and tighten conditional order bands"
```

---

### Task 2: Add position metadata to PaperTrader

**Files:**
- Modify: `src/polymarket_agent/execution/paper.py:69-96` (`_execute_buy`)
- Modify: `src/polymarket_agent/execution/paper.py:22-46` (`recover_from_db`)
- Test: `tests/test_paper_trader.py`

**Step 1: Write the failing tests**

Add to `tests/test_paper_trader.py`:

```python
def test_paper_trader_buy_sets_metadata(trader) -> None:
    """Buy orders should record opened_at and entry_strategy in position."""
    paper, _db = trader
    paper.place_order(_make_signal(side="buy", price=0.5, size=50.0))
    pos = paper.get_portfolio().positions["0xtok_100"]
    assert "opened_at" in pos
    assert pos["entry_strategy"] == "test"


def test_paper_trader_recover_sets_default_metadata() -> None:
    """Recovered positions without metadata get sensible defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.record_portfolio_snapshot(
            balance=900.0,
            total_value=950.0,
            positions_json='{"0xtok_100":{"market_id":"100","shares":50.0,"avg_price":0.5,"current_price":0.55}}',
        )
        paper = PaperTrader(starting_balance=1000.0, db=db)
        paper.recover_from_db()
        pos = paper.get_portfolio().positions["0xtok_100"]
        assert "opened_at" in pos
        assert pos["entry_strategy"] == "unknown"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_paper_trader.py::test_paper_trader_buy_sets_metadata tests/test_paper_trader.py::test_paper_trader_recover_sets_default_metadata -v`
Expected: FAIL — `KeyError: 'opened_at'` / `KeyError: 'entry_strategy'`

**Step 3: Add metadata to _execute_buy**

In `src/polymarket_agent/execution/paper.py`, in `_execute_buy`, when creating a new position (the `else` branch around line 91), add:

```python
        else:
            self._positions[signal.token_id] = {
                "market_id": signal.market_id,
                "shares": shares,
                "avg_price": signal.target_price,
                "current_price": signal.target_price,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "entry_strategy": signal.strategy,
            }
```

Add import at top of file:

```python
from datetime import datetime, timezone
```

**Step 4: Add default metadata to recover_from_db**

In `recover_from_db`, after successfully parsing positions, backfill missing metadata:

```python
                # Backfill metadata for positions recovered without it
                now_iso = datetime.now(timezone.utc).isoformat()
                for pos in self._positions.values():
                    if "opened_at" not in pos:
                        pos["opened_at"] = now_iso
                    if "entry_strategy" not in pos:
                        pos["entry_strategy"] = "unknown"
```

Add this right after the `self._positions = positions` assignment inside the `if isinstance(positions, dict)` block.

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_paper_trader.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/polymarket_agent/execution/paper.py tests/test_paper_trader.py
git commit -m "feat: add opened_at and entry_strategy metadata to positions"
```

---

### Task 3: Create ExitManager class

**Files:**
- Create: `src/polymarket_agent/strategies/exit_manager.py`
- Create: `tests/test_exit_manager.py`

**Step 1: Write failing tests for all 4 exit rules**

Create `tests/test_exit_manager.py`:

```python
"""Tests for the ExitManager."""

from datetime import datetime, timedelta, timezone

import pytest

from polymarket_agent.config import ExitManagerConfig
from polymarket_agent.strategies.base import Signal
from polymarket_agent.strategies.exit_manager import ExitManager


def _make_position(
    token_id: str = "0xtok1",
    market_id: str = "100",
    avg_price: float = 0.40,
    shares: float = 100.0,
    entry_strategy: str = "signal_trader",
    opened_at: str | None = None,
) -> tuple[str, dict]:
    if opened_at is None:
        opened_at = datetime.now(timezone.utc).isoformat()
    return token_id, {
        "market_id": market_id,
        "shares": shares,
        "avg_price": avg_price,
        "current_price": avg_price,
        "opened_at": opened_at,
        "entry_strategy": entry_strategy,
    }


@pytest.fixture
def exit_manager() -> ExitManager:
    return ExitManager(ExitManagerConfig())


class TestProfitTarget:
    def test_sell_when_profit_target_reached(self, exit_manager: ExitManager) -> None:
        token_id, pos = _make_position(avg_price=0.40)
        positions = {token_id: pos}
        current_prices = {token_id: 0.48}  # 20% gain > 15% target

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 1
        assert signals[0].side == "sell"
        assert signals[0].token_id == token_id
        assert "profit_target" in signals[0].reason

    def test_no_sell_below_profit_target(self, exit_manager: ExitManager) -> None:
        token_id, pos = _make_position(avg_price=0.40)
        positions = {token_id: pos}
        current_prices = {token_id: 0.44}  # 10% gain < 15% target

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 0


class TestStopLoss:
    def test_sell_when_stop_loss_hit(self, exit_manager: ExitManager) -> None:
        token_id, pos = _make_position(avg_price=0.50)
        positions = {token_id: pos}
        current_prices = {token_id: 0.42}  # 16% loss > 12% stop

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 1
        assert signals[0].side == "sell"
        assert "stop_loss" in signals[0].reason

    def test_no_sell_above_stop_loss(self, exit_manager: ExitManager) -> None:
        token_id, pos = _make_position(avg_price=0.50)
        positions = {token_id: pos}
        current_prices = {token_id: 0.47}  # 6% loss < 12% stop

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 0


class TestSignalReversal:
    def test_signal_trader_sell_when_price_above_midpoint(self, exit_manager: ExitManager) -> None:
        """Signal trader bought because yes_price < 0.5. Sell when it crosses above."""
        token_id, pos = _make_position(avg_price=0.35, entry_strategy="signal_trader")
        positions = {token_id: pos}
        current_prices = {token_id: 0.52}  # above midpoint but within profit target

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 1
        assert "signal_reversal" in signals[0].reason

    def test_signal_trader_no_sell_when_still_below_midpoint(self, exit_manager: ExitManager) -> None:
        token_id, pos = _make_position(avg_price=0.35, entry_strategy="signal_trader")
        positions = {token_id: pos}
        current_prices = {token_id: 0.40}  # still below midpoint, within bands

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 0

    def test_unknown_strategy_skips_reversal(self, exit_manager: ExitManager) -> None:
        token_id, pos = _make_position(avg_price=0.40, entry_strategy="unknown")
        positions = {token_id: pos}
        current_prices = {token_id: 0.42}  # within all bands

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 0


class TestStalePosition:
    def test_sell_stale_position(self, exit_manager: ExitManager) -> None:
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        token_id, pos = _make_position(avg_price=0.40, opened_at=old_time)
        positions = {token_id: pos}
        current_prices = {token_id: 0.41}  # within all bands

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 1
        assert "stale" in signals[0].reason

    def test_no_sell_fresh_position(self, exit_manager: ExitManager) -> None:
        recent_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        token_id, pos = _make_position(avg_price=0.40, opened_at=recent_time)
        positions = {token_id: pos}
        current_prices = {token_id: 0.41}

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 0


class TestDisabled:
    def test_disabled_returns_empty(self) -> None:
        cfg = ExitManagerConfig(enabled=False)
        em = ExitManager(cfg)
        token_id, pos = _make_position(avg_price=0.40)
        current_prices = {token_id: 0.01}  # extreme loss

        signals = em.evaluate({token_id: pos}, current_prices)

        assert len(signals) == 0


class TestPriority:
    def test_first_matching_rule_wins(self, exit_manager: ExitManager) -> None:
        """Profit target fires before staleness check."""
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        token_id, pos = _make_position(avg_price=0.40, opened_at=old_time)
        positions = {token_id: pos}
        current_prices = {token_id: 0.50}  # 25% gain

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 1
        assert "profit_target" in signals[0].reason
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_exit_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polymarket_agent.strategies.exit_manager'`

**Step 3: Implement ExitManager**

Create `src/polymarket_agent/strategies/exit_manager.py`:

```python
"""ExitManager — generates sell signals for held positions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from polymarket_agent.config import ExitManagerConfig
from polymarket_agent.strategies.base import Signal

logger = logging.getLogger(__name__)

_MIDPOINT = 0.5


class ExitManager:
    """Evaluate held positions and generate sell signals when exit conditions are met.

    Exit rules are evaluated in priority order; the first matching rule wins:
    1. Profit target — current price >= entry * (1 + profit_target_pct)
    2. Stop loss — current price <= entry * (1 - stop_loss_pct)
    3. Signal reversal — entry condition no longer holds
    4. Stale position — held longer than max_hold_hours
    """

    def __init__(self, config: ExitManagerConfig) -> None:
        self._config = config

    def evaluate(
        self,
        positions: dict[str, dict[str, Any]],
        current_prices: dict[str, float],
    ) -> list[Signal]:
        """Return sell signals for positions that should be closed."""
        if not self._config.enabled:
            return []

        signals: list[Signal] = []
        for token_id, pos in positions.items():
            current_price = current_prices.get(token_id)
            if current_price is None:
                continue

            reason = self._check_exit(pos, current_price)
            if reason is None:
                continue

            shares = float(pos.get("shares", 0))
            if shares <= 0:
                continue

            size = shares * current_price
            signals.append(
                Signal(
                    strategy="exit_manager",
                    market_id=str(pos.get("market_id", "")),
                    token_id=token_id,
                    side="sell",
                    confidence=1.0,
                    target_price=current_price,
                    size=size,
                    reason=reason,
                )
            )
        return signals

    def _check_exit(self, pos: dict[str, Any], current_price: float) -> str | None:
        """Check exit rules in priority order. Return reason string or None."""
        avg_price = float(pos.get("avg_price", 0))
        if avg_price <= 0:
            return None

        # Rule 1: Profit target
        if current_price >= avg_price * (1.0 + self._config.profit_target_pct):
            pct = (current_price - avg_price) / avg_price * 100
            return f"profit_target: +{pct:.1f}% (entry={avg_price:.4f}, current={current_price:.4f})"

        # Rule 2: Stop loss
        if current_price <= avg_price * (1.0 - self._config.stop_loss_pct):
            pct = (avg_price - current_price) / avg_price * 100
            return f"stop_loss: -{pct:.1f}% (entry={avg_price:.4f}, current={current_price:.4f})"

        # Rule 3: Signal reversal
        if self._config.signal_reversal:
            reversal = self._check_signal_reversal(pos, current_price)
            if reversal is not None:
                return reversal

        # Rule 4: Stale position
        opened_at_str = pos.get("opened_at")
        if opened_at_str:
            try:
                opened_at = datetime.fromisoformat(opened_at_str)
                age = datetime.now(timezone.utc) - opened_at
                if age > timedelta(hours=self._config.max_hold_hours):
                    hours = age.total_seconds() / 3600
                    return f"stale: held {hours:.1f}h > max {self._config.max_hold_hours}h"
            except (ValueError, TypeError):
                pass

        return None

    @staticmethod
    def _check_signal_reversal(pos: dict[str, Any], current_price: float) -> str | None:
        """Check if the original entry condition has reversed."""
        entry_strategy = pos.get("entry_strategy", "unknown")

        if entry_strategy == "signal_trader":
            # Signal trader buys when yes_price < midpoint. Reversed when price >= midpoint.
            if current_price >= _MIDPOINT:
                return f"signal_reversal: price {current_price:.4f} crossed above midpoint ({_MIDPOINT})"

        if entry_strategy == "arbitrageur":
            # Arbitrageur buys underpriced side. If price has normalized, exit.
            # We approximate: if current price is within 2% of avg_price, the arb closed.
            avg_price = float(pos.get("avg_price", 0))
            if avg_price > 0 and abs(current_price - avg_price) / avg_price < 0.02:
                return f"signal_reversal: arb deviation closed (entry={avg_price:.4f}, current={current_price:.4f})"

        return None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_exit_manager.py -v`
Expected: All PASS

**Step 5: Lint and type check**

Run: `ruff check src/polymarket_agent/strategies/exit_manager.py && mypy src/polymarket_agent/strategies/exit_manager.py`
Expected: Clean

**Step 6: Commit**

```bash
git add src/polymarket_agent/strategies/exit_manager.py tests/test_exit_manager.py
git commit -m "feat: add ExitManager with profit target, stop loss, reversal, and staleness rules"
```

---

### Task 4: Integrate ExitManager into orchestrator tick loop

**Files:**
- Modify: `src/polymarket_agent/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

**Step 1: Write the failing test**

Add to `tests/test_orchestrator.py`:

```python
def test_orchestrator_exit_manager_generates_sells(mocker: object) -> None:
    """ExitManager should generate sell signals for held positions that hit profit target."""
    mocker.patch("polymarket_agent.data.client.subprocess.run", side_effect=_mock_run)
    with tempfile.TemporaryDirectory() as tmpdir:
        config = AppConfig(
            mode="paper",
            starting_balance=1000.0,
            strategies={},  # no entry strategies
        )
        orch = Orchestrator(config=config, db_path=Path(tmpdir) / "test.db")

        # Manually seed a position in the executor
        from datetime import datetime, timezone
        orch._executor._positions["0xtok1"] = {
            "market_id": "100",
            "shares": 100.0,
            "avg_price": 0.40,
            "current_price": 0.40,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "entry_strategy": "signal_trader",
        }
        orch._executor._balance = 960.0  # 1000 - 40 cost

        # Mock get_price to return a price above profit target (0.40 * 1.15 = 0.46)
        from polymarket_agent.data.models import Spread
        mock_spread = Spread(token_id="0xtok1", bid=0.50, ask=0.52, spread=0.02)
        mocker.patch.object(orch._data, "get_price", return_value=mock_spread)

        result = orch.tick()

        assert result["trades_executed"] >= 1
        portfolio = orch.get_portfolio()
        # Position should be closed
        assert "0xtok1" not in portfolio.positions
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py::test_orchestrator_exit_manager_generates_sells -v`
Expected: FAIL — `trades_executed == 0`

**Step 3: Integrate ExitManager into orchestrator**

In `src/polymarket_agent/orchestrator.py`:

1. Add import at top:

```python
from polymarket_agent.strategies.exit_manager import ExitManager
```

2. In `__init__`, after building strategies, add:

```python
        self._exit_manager = ExitManager(config.exit_manager)
```

3. In `tick()`, after the aggregation step (after `logger.info("Aggregated to %d signals", len(signals))`), add exit manager evaluation:

```python
        # Run exit manager on held positions
        exit_signals: list[Signal] = []
        if self._config.exit_manager.enabled and self._config.mode != "monitor":
            exit_signals = self._evaluate_exits()
            if exit_signals:
                logger.info("ExitManager generated %d sell signal(s)", len(exit_signals))
```

4. In the trade execution section, execute exit signals first (before entry signals), bypassing risk gate:

Replace the existing trade execution block (lines ~110-126) with:

```python
        trades_executed = 0
        if self._config.mode != "monitor":
            # Execute exit signals first (bypass risk gate and position sizing)
            for signal in exit_signals:
                order = self._executor.place_order(signal)
                if order is not None:
                    trades_executed += 1
                    self._record_signal(signal, status="executed")
                    self._alerts.alert(
                        f"Exit trade: {signal.side} {signal.size:.2f} USDC "
                        f"on {signal.market_id} ({signal.reason})"
                    )
                else:
                    self._record_signal(signal, status="rejected")

            # Execute entry signals through normal risk/sizing pipeline
            risk_snapshot = self._build_risk_snapshot()
            for signal in signals:
                sized_signal = self._apply_position_sizing(signal)
                if self.place_order(sized_signal, risk_snapshot=risk_snapshot) is None:
                    self._record_signal(sized_signal, status="rejected")
                    continue
                trades_executed += 1
                self._record_signal(sized_signal, status="executed")
                self._alerts.alert(
                    f"Trade executed: {sized_signal.side} {sized_signal.size:.2f} USDC "
                    f"on {sized_signal.market_id} ({sized_signal.strategy})"
                )
                self._update_risk_snapshot_after_order(risk_snapshot, sized_signal)
                self._auto_create_conditional_orders(sized_signal)
```

5. Add the `_evaluate_exits` helper method to the Orchestrator class:

```python
    def _evaluate_exits(self) -> list[Signal]:
        """Fetch current prices for held positions and run the exit manager."""
        portfolio = self.get_portfolio()
        if not portfolio.positions:
            return []

        current_prices: dict[str, float] = {}
        for token_id in portfolio.positions:
            try:
                spread = self._data.get_price(token_id)
                current_prices[token_id] = spread.bid
            except Exception:
                logger.debug("Failed to fetch price for %s, skipping exit check", token_id)

        return self._exit_manager.evaluate(portfolio.positions, current_prices)
```

6. In `reload_config`, rebuild the exit manager:

After `self._alerts = self._build_alert_manager(new_config)`, add:

```python
        self._exit_manager = ExitManager(new_config.exit_manager)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator.py::test_orchestrator_exit_manager_generates_sells -v`
Expected: PASS

**Step 5: Run all orchestrator tests**

Run: `uv run pytest tests/test_orchestrator.py tests/test_risk_gate.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/polymarket_agent/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: integrate ExitManager into orchestrator tick loop"
```

---

### Task 5: Full test suite and type checks

**Files:**
- All modified files

**Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

**Step 2: Run linter**

Run: `ruff check src/`
Expected: Clean (or fix any issues)

**Step 3: Run type checker**

Run: `mypy src/`
Expected: Clean (or fix any issues)

**Step 4: Commit any fixes**

```bash
git add -u
git commit -m "fix: resolve lint and type check issues from exit manager integration"
```

---

### Task 6: Reset DB and verify end-to-end

**Step 1: Delete old database**

```bash
rm -f polymarket_agent.db
```

**Step 2: Run a single tick to verify**

```bash
uv run polymarket-agent tick
```

Expected: Should show trades being executed (buys for new positions), no "already holding" rejections on a fresh start.

**Step 3: Run a few ticks to see exits in action**

```bash
uv run polymarket-agent run
```

Watch for ExitManager sell signals in subsequent ticks. The bot should now cycle through positions: buy → hold → sell (via exit rules) → buy new.

**Step 4: Check report**

```bash
uv run polymarket-agent report --period 1h
```

Expected: Shows trades with both buys and sells.
