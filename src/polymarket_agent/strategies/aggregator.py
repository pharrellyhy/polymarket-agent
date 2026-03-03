"""Signal aggregation — deduplication, filtering, conflict resolution, and consensus."""

from dataclasses import replace

from polymarket_agent.strategies.base import Signal


def aggregate_signals(
    signals: list[Signal],
    *,
    min_confidence: float = 0.5,
    min_strategies: int = 1,
    conflict_resolution: bool = True,
    blend_confidence: bool = True,
) -> list[Signal]:
    """Aggregate signals from multiple strategies.

    1. Suppress conflicting signals (opposite sides for the same market+token).
    2. Group signals by (market_id, token_id, side).
    3. Filter groups that don't meet min_strategies threshold.
    4. Blend confidence across the group (average).
    5. Filter by min_confidence.
    """
    if not signals:
        return []

    # Conflict resolution: suppress signals where strategies disagree on side
    conflicted: set[tuple[str, str]] = set()
    if conflict_resolution:
        market_token_sides: dict[tuple[str, str], set[str]] = {}
        for signal in signals:
            key = (signal.market_id, signal.token_id)
            market_token_sides.setdefault(key, set()).add(signal.side)
        conflicted = {key for key, sides in market_token_sides.items() if len(sides) > 1}

    groups: dict[tuple[str, str, str], list[Signal]] = {}
    for signal in signals:
        if (signal.market_id, signal.token_id) in conflicted:
            continue
        group_key = (signal.market_id, signal.token_id, signal.side)
        groups.setdefault(group_key, []).append(signal)

    result: list[Signal] = []
    for group in groups.values():
        strategies = {signal.strategy for signal in group}
        if len(strategies) < min_strategies:
            continue
        # Confidence blending: use group average, attach to best-reason signal.
        best = max(group, key=lambda s: s.confidence)
        if blend_confidence:
            blended_confidence = sum(s.confidence for s in group) / len(group)
            best = replace(best, confidence=round(blended_confidence, 4))
        if best.confidence >= min_confidence:
            result.append(best)

    return result
