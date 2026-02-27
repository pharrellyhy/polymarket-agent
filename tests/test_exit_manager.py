"""Tests for the ExitManager."""

from datetime import datetime, timedelta, timezone

import pytest
from polymarket_agent.config import ExitManagerConfig
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
        token_id, pos = _make_position(avg_price=0.45, entry_strategy="signal_trader")
        positions = {token_id: pos}
        current_prices = {token_id: 0.51}  # above midpoint but within profit target (0.45*1.15=0.5175)

        signals = exit_manager.evaluate(positions, current_prices)

        assert len(signals) == 1
        assert "signal_reversal" in signals[0].reason

    def test_signal_trader_no_sell_when_still_below_midpoint(self, exit_manager: ExitManager) -> None:
        token_id, pos = _make_position(avg_price=0.45, entry_strategy="signal_trader")
        positions = {token_id: pos}
        current_prices = {token_id: 0.48}  # still below midpoint, within bands

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


class TestMalformedPositionData:
    def test_malformed_avg_price_is_skipped(self, exit_manager: ExitManager) -> None:
        token_id, pos = _make_position()
        pos["avg_price"] = "bad"
        signals = exit_manager.evaluate({token_id: pos}, {token_id: 0.5})
        assert signals == []

    def test_malformed_shares_is_skipped(self, exit_manager: ExitManager) -> None:
        token_id, pos = _make_position()
        pos["shares"] = "bad"
        signals = exit_manager.evaluate({token_id: pos}, {token_id: 0.5})
        assert signals == []


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
