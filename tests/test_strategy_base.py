"""Tests for strategy base class and Signal model."""

from polymarket_agent.strategies.base import Signal, Strategy


class DummyStrategy(Strategy):
    name = "dummy"

    def analyze(self, markets, data):
        return [
            Signal(
                strategy=self.name,
                market_id="100",
                token_id="0xtok1",
                side="buy",
                confidence=0.8,
                target_price=0.5,
                size=50.0,
                reason="Test signal",
            )
        ]


def test_signal_creation():
    signal = Signal(
        strategy="test",
        market_id="100",
        token_id="0xtok1",
        side="buy",
        confidence=0.75,
        target_price=0.6,
        size=25.0,
        reason="price looks low",
    )
    assert signal.strategy == "test"
    assert signal.side == "buy"
    assert signal.confidence == 0.75


def test_dummy_strategy_produces_signals():
    strategy = DummyStrategy()
    signals = strategy.analyze([], None)
    assert len(signals) == 1
    assert signals[0].strategy == "dummy"
