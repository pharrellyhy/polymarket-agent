"""Execution validation using order book analysis.

Pre-signal-emission filter that checks whether a trade can be executed
profitably given real order book depth and slippage.
"""

import logging
from typing import TYPE_CHECKING

from polymarket_agent.data.models import OrderBook

if TYPE_CHECKING:
    from polymarket_agent.data.provider import DataProvider

logger = logging.getLogger(__name__)

_DEFAULT_MIN_PROFIT: float = 0.05
_DEFAULT_MAX_SLIPPAGE: float = 0.02


def estimate_vwap(orderbook: OrderBook, size: float, side: str) -> tuple[float, float]:
    """Estimate volume-weighted average price for a given trade size.

    Args:
        orderbook: Order book with asks and bids.
        size: Trade size in USDC.
        side: "buy" (uses asks) or "sell" (uses bids).

    Returns:
        Tuple of (vwap_price, available_size_in_usdc).
    """
    levels = orderbook.asks if side == "buy" else orderbook.bids
    if not levels:
        return 0.0, 0.0

    # Sort: ascending for buys (lowest ask first), descending for sells (highest bid first)
    sorted_levels = sorted(levels, key=lambda lv: lv.price, reverse=(side == "sell"))

    total_cost = 0.0
    total_shares = 0.0
    remaining = size

    for level in sorted_levels:
        level_value = level.price * level.size  # USDC value at this level
        if level_value >= remaining:
            shares_at_level = remaining / level.price
            total_cost += remaining
            total_shares += shares_at_level
            remaining = 0.0
            break
        total_cost += level_value
        total_shares += level.size
        remaining -= level_value

    available = size - remaining
    if total_shares <= 0:
        return 0.0, available

    vwap = total_cost / total_shares
    return vwap, available


def estimate_slippage(vwap_price: float, mid_price: float) -> float:
    """Estimate slippage as the relative deviation from midpoint.

    Args:
        vwap_price: Volume-weighted average execution price.
        mid_price: Order book midpoint price.

    Returns:
        Slippage as a fraction (e.g., 0.02 = 2%).
    """
    if mid_price <= 0:
        return 0.0
    return abs(vwap_price - mid_price) / mid_price


def validate_execution(
    data: "DataProvider",
    token_id: str,
    size: float,
    side: str,
    expected_profit: float,
    min_profit: float = _DEFAULT_MIN_PROFIT,
    max_slippage: float = _DEFAULT_MAX_SLIPPAGE,
) -> tuple[bool, str]:
    """Validate whether a trade can be executed profitably.

    Checks order book depth and estimated slippage against thresholds.

    Args:
        data: Data provider for fetching order books.
        token_id: CLOB token ID to trade.
        size: Intended trade size in USDC.
        side: "buy" or "sell".
        expected_profit: Expected profit from the arbitrage.
        min_profit: Minimum acceptable profit after slippage.
        max_slippage: Maximum acceptable slippage percentage.

    Returns:
        Tuple of (is_valid, reason_string).
    """
    try:
        orderbook = data.get_orderbook(token_id)
    except Exception:
        logger.debug("Failed to fetch orderbook for %s", token_id)
        return False, "orderbook_unavailable"

    mid = orderbook.midpoint
    if mid <= 0:
        return False, "no_liquidity"

    vwap, available = estimate_vwap(orderbook, size, side)
    if available < size * 0.5:
        return False, f"insufficient_depth: available={available:.2f} < {size * 0.5:.2f}"

    slippage = estimate_slippage(vwap, mid)
    if slippage > max_slippage:
        return False, f"slippage_too_high: {slippage:.4f} > {max_slippage:.4f}"

    net_profit = expected_profit - (slippage * size)
    if net_profit < min_profit:
        return False, f"net_profit_too_low: {net_profit:.4f} < {min_profit:.4f}"

    return True, f"ok: vwap={vwap:.4f}, slippage={slippage:.4f}, net_profit={net_profit:.4f}"
