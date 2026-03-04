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
    strategy_weights: dict[str, float] | None = None,
) -> list[Signal]:
    """Aggregate signals from multiple strategies.

    1. Resolve conflicting signals (opposite sides for the same market+token).
       When strategy_weights is provided, the higher-weight side wins
       instead of suppressing both.
    2. Group signals by (market_id, token_id, side).
    3. Filter groups that don't meet min_strategies threshold.
    4. Blend confidence across the group (weighted average if weights provided).
    5. Filter by min_confidence.
    """
    if not signals:
        return []

    # Conflict resolution
    conflicted: set[tuple[str, str]] = set()
    conflict_losers: set[tuple[str, str, str]] = set()  # (market_id, token_id, losing_side)
    if conflict_resolution:
        market_token_sides: dict[tuple[str, str], dict[str, list[Signal]]] = {}
        for signal in signals:
            key = (signal.market_id, signal.token_id)
            market_token_sides.setdefault(key, {}).setdefault(signal.side, []).append(signal)

        for key, sides in market_token_sides.items():
            if len(sides) <= 1:
                continue
            if strategy_weights is not None:
                # Weighted conflict resolution: higher-weight side wins
                side_weights: dict[str, float] = {}
                for side, side_signals in sides.items():
                    side_weights[side] = sum(
                        strategy_weights.get(s.strategy, 1.0) for s in side_signals
                    )
                winning_side = max(side_weights, key=lambda s: side_weights[s])
                for side in sides:
                    if side != winning_side:
                        conflict_losers.add((key[0], key[1], side))
            else:
                # All-or-nothing: any disagreement suppresses both sides
                conflicted.add(key)

    groups: dict[tuple[str, str, str], list[Signal]] = {}
    for signal in signals:
        if (signal.market_id, signal.token_id) in conflicted:
            continue
        if (signal.market_id, signal.token_id, signal.side) in conflict_losers:
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
            if strategy_weights is not None:
                # Weighted average confidence
                total_weight = sum(strategy_weights.get(s.strategy, 1.0) for s in group)
                if total_weight > 0:
                    blended_confidence = sum(
                        s.confidence * strategy_weights.get(s.strategy, 1.0) for s in group
                    ) / total_weight
                else:
                    blended_confidence = sum(s.confidence for s in group) / len(group)
            else:
                blended_confidence = sum(s.confidence for s in group) / len(group)
            best = replace(best, confidence=round(blended_confidence, 4))
        if best.confidence >= min_confidence:
            result.append(best)

    return result
