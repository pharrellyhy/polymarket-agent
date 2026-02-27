"""Historical data provider for backtesting.

Loads CSV files with market price data and replays them through the same
DataProvider interface used by the live CLI wrapper.
"""

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from polymarket_agent.data.models import Market, OrderBook, OrderBookLevel, PricePoint, Spread

logger = logging.getLogger(__name__)


@dataclass
class _TimeStep:
    """A single observation of a market at a point in time."""

    timestamp: str
    market_id: str
    question: str
    yes_price: float
    volume: float
    token_id: str


class HistoricalDataProvider:
    """Provides market data from CSV files for backtesting.

    CSV columns: timestamp, market_id, question, yes_price, volume, token_id

    The provider maintains a time cursor that controls which rows are
    visible to callers. Call :meth:`advance` to move forward in time.

    Args:
        data_dir: Directory containing CSV files.
        default_spread: Synthetic spread applied around the price to build
            an orderbook (half on each side).
    """

    def __init__(self, data_dir: Path, *, default_spread: float = 0.02) -> None:
        self._default_spread = default_spread
        self._steps: list[_TimeStep] = []
        self._timestamps: list[str] = []
        self._cursor: int = 0
        self._load_csv_files(data_dir)

    # ------------------------------------------------------------------
    # DataProvider protocol methods
    # ------------------------------------------------------------------

    def get_active_markets(self, *, tag: str | None = None, limit: int = 50) -> list[Market]:
        """Return markets visible at the current time step."""
        current = self._current_steps()
        seen: dict[str, _TimeStep] = {}
        for step in current:
            seen[step.market_id] = step
        markets: list[Market] = []
        for step in list(seen.values())[:limit]:
            markets.append(self._step_to_market(step))
        return markets

    def get_orderbook(self, token_id: str) -> OrderBook:
        """Synthesize an orderbook from the current price for a token."""
        price = self._current_price(token_id)
        half_spread = self._default_spread / 2
        bid_price = max(price - half_spread, 0.001)
        ask_price = min(price + half_spread, 0.999)
        return OrderBook(
            bids=[OrderBookLevel(price=bid_price, size=1000.0)],
            asks=[OrderBookLevel(price=ask_price, size=1000.0)],
        )

    def get_price(self, token_id: str) -> Spread:
        """Return bid/ask/spread derived from the current price."""
        price = self._current_price(token_id)
        half_spread = self._default_spread / 2
        bid = max(price - half_spread, 0.001)
        ask = min(price + half_spread, 0.999)
        return Spread(token_id=token_id, bid=bid, ask=ask, spread=ask - bid)

    def get_price_history(
        self,
        token_id: str,
        *,
        interval: str = "1d",
        fidelity: int = 60,
    ) -> list[PricePoint]:
        """Return all historical price points up to the current cursor for a token."""
        points: list[PricePoint] = []
        for step in self._steps[: self._cursor + 1]:
            if step.token_id == token_id:
                points.append(PricePoint(timestamp=step.timestamp, price=step.yes_price))
        return points

    # ------------------------------------------------------------------
    # Time cursor
    # ------------------------------------------------------------------

    def advance(self, timestamp: str) -> None:
        """Move the cursor forward to the given timestamp.

        All rows with ``timestamp <= target`` become visible.
        """
        for i, ts in enumerate(self._timestamps):
            if ts > timestamp:
                self._cursor = max(i - 1, 0)
                return
        self._cursor = len(self._timestamps) - 1

    @property
    def current_timestamp(self) -> str:
        """Return the timestamp at the current cursor position."""
        if not self._timestamps:
            return ""
        return self._timestamps[self._cursor]

    @property
    def unique_timestamps(self) -> list[str]:
        """Return the sorted unique timestamps available for stepping."""
        seen: list[str] = []
        prev = ""
        for ts in self._timestamps:
            if ts != prev:
                seen.append(ts)
                prev = ts
        return seen

    @property
    def total_steps(self) -> int:
        """Return the number of raw data rows loaded."""
        return len(self._steps)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_csv_files(self, data_dir: Path) -> None:
        """Load all CSV files in the directory, sorted by timestamp."""
        csv_files = sorted(data_dir.glob("*.csv"))
        if not csv_files:
            logger.warning("No CSV files found in %s", data_dir)
            return

        for csv_file in csv_files:
            with csv_file.open(newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        step = _TimeStep(
                            timestamp=row["timestamp"],
                            market_id=row["market_id"],
                            question=row["question"],
                            yes_price=float(row["yes_price"]),
                            volume=float(row["volume"]),
                            token_id=row["token_id"],
                        )
                        self._steps.append(step)
                    except (KeyError, ValueError):
                        logger.warning("Skipping malformed CSV row in %s: %s", csv_file, row)

        self._steps.sort(key=lambda s: s.timestamp)
        self._timestamps = [s.timestamp for s in self._steps]
        logger.info("Loaded %d rows from %d CSV files", len(self._steps), len(csv_files))

    def _current_steps(self) -> list[_TimeStep]:
        """Return all steps at the current timestamp."""
        if not self._timestamps:
            return []
        target_ts = self._timestamps[self._cursor]
        return [s for s in self._steps[: self._cursor + 1] if s.timestamp == target_ts]

    def _current_price(self, token_id: str) -> float:
        """Return the most recent price for a token at or before the cursor."""
        for step in reversed(self._steps[: self._cursor + 1]):
            if step.token_id == token_id:
                return step.yes_price
        msg = f"No price data for token {token_id} at cursor {self._cursor}"
        raise RuntimeError(msg)

    @staticmethod
    def _step_to_market(step: _TimeStep) -> Market:
        """Convert a time step row to a Market model."""
        return Market(
            id=step.market_id,
            question=step.question,
            outcomes=["Yes", "No"],
            outcome_prices=[step.yes_price, round(1.0 - step.yes_price, 4)],
            volume=step.volume,
            active=True,
            closed=False,
            clob_token_ids=[step.token_id],
        )
