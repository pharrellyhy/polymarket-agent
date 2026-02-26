"""Signal aggregation — deduplication, filtering, and consensus."""

from polymarket_agent.strategies.base import Signal


def aggregate_signals(
    signals: list[Signal],
    *,
    min_confidence: float = 0.5,
    min_strategies: int = 1,
) -> list[Signal]:
    """Aggregate signals from multiple strategies.

    1. Group signals by (market_id, token_id, side).
    2. Filter groups that don't meet min_strategies threshold.
    3. For each group, keep the signal with highest confidence.
    4. Filter by min_confidence.
    """
    if not signals:
        return []

    groups: dict[tuple[str, str, str], list[Signal]] = {}
    for signal in signals:
        key = (signal.market_id, signal.token_id, signal.side)
        groups.setdefault(key, []).append(signal)

    result: list[Signal] = []
    for group in groups.values():
        strategies = {signal.strategy for signal in group}
        if len(strategies) < min_strategies:
            continue
        best = max(group, key=lambda s: s.confidence)
        if best.confidence >= min_confidence:
            result.append(best)

    return result
