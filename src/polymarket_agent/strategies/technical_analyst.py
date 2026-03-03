"""TechnicalAnalyst strategy — rule-based signals from price history indicators."""

import logging
from typing import Any, Literal

from polymarket_agent.data.models import Market
from polymarket_agent.data.provider import DataProvider
from polymarket_agent.strategies.base import Signal, Strategy
from polymarket_agent.strategies.indicators import TechnicalContext, analyze_market_technicals

logger = logging.getLogger(__name__)

_DEFAULT_EMA_FAST: int = 8
_DEFAULT_EMA_SLOW: int = 21
_DEFAULT_RSI_PERIOD: int = 14
_DEFAULT_HISTORY_INTERVAL: str = "1w"
_DEFAULT_HISTORY_FIDELITY: int = 60
_DEFAULT_ORDER_SIZE: float = 25.0
_MIN_PRICE: float = 0.05
_MAX_PRICE: float = 0.95


class TechnicalAnalyst(Strategy):
    """Generate signals from technical indicator confluence.

    Fetches price history for each market via ``DataProvider.get_price_history()``,
    computes EMA crossover, RSI, and Bollinger squeeze indicators, then generates
    buy/sell signals when multiple indicators agree.

    Signal logic:
    - **BUY**: bullish EMA crossover + RSI not overbought + squeeze confirmation
    - **SELL**: bearish EMA crossover + RSI not oversold + squeeze confirmation
    - **Confidence**: regime-adaptive blend of EMA/RSI/squeeze/MACD components
    """

    name: str = "technical_analyst"

    def __init__(self) -> None:
        self._ema_fast_period: int = _DEFAULT_EMA_FAST
        self._ema_slow_period: int = _DEFAULT_EMA_SLOW
        self._rsi_period: int = _DEFAULT_RSI_PERIOD
        self._history_interval: str = _DEFAULT_HISTORY_INTERVAL
        self._history_fidelity: int = _DEFAULT_HISTORY_FIDELITY
        self._order_size: float = _DEFAULT_ORDER_SIZE
        self._macd_enabled: bool = True
        self._regime_adaptive: bool = True

    def configure(self, config: dict[str, Any]) -> None:
        self._ema_fast_period = int(config.get("ema_fast_period", _DEFAULT_EMA_FAST))
        self._ema_slow_period = int(config.get("ema_slow_period", _DEFAULT_EMA_SLOW))
        self._rsi_period = int(config.get("rsi_period", _DEFAULT_RSI_PERIOD))
        self._history_interval = str(config.get("history_interval", _DEFAULT_HISTORY_INTERVAL))
        self._history_fidelity = int(config.get("history_fidelity", _DEFAULT_HISTORY_FIDELITY))
        self._order_size = float(config.get("order_size", _DEFAULT_ORDER_SIZE))
        self._macd_enabled = bool(config.get("macd_enabled", self._macd_enabled))
        self._regime_adaptive = bool(config.get("regime_adaptive", self._regime_adaptive))

    def analyze(self, markets: list[Market], data: DataProvider) -> list[Signal]:
        signals: list[Signal] = []
        for market in markets:
            if not market.active or market.closed:
                continue
            if not market.outcome_prices or not market.clob_token_ids:
                continue
            yes_price = market.outcome_prices[0]
            if yes_price < _MIN_PRICE or yes_price > _MAX_PRICE:
                continue
            signal = self._evaluate(market, data)
            if signal is not None:
                signals.append(signal)
        return signals

    def _evaluate(self, market: Market, data: DataProvider) -> Signal | None:
        token_id = market.clob_token_ids[0]
        try:
            history = data.get_price_history(
                token_id,
                interval=self._history_interval,
                fidelity=self._history_fidelity,
            )
        except Exception:
            logger.debug("Failed to fetch price history for %s", token_id)
            return None

        ctx = analyze_market_technicals(
            history,
            token_id,
            ema_fast_period=self._ema_fast_period,
            ema_slow_period=self._ema_slow_period,
            rsi_period=self._rsi_period,
        )
        if ctx is None:
            return None

        return self._generate_signal(market, ctx)

    def _generate_signal(self, market: Market, ctx: TechnicalContext) -> Signal | None:
        side = self._determine_side(ctx)
        if side is None:
            return None

        confidence = self._compute_confidence(ctx, side)
        if confidence <= 0:
            return None

        return Signal(
            strategy=self.name,
            market_id=market.id,
            token_id=ctx.token_id,
            side=side,
            confidence=round(confidence, 4),
            target_price=ctx.current_price,
            size=self._order_size,
            reason=self._build_reason(ctx, side),
        )

    @staticmethod
    def _determine_side(ctx: TechnicalContext) -> Literal["buy", "sell"] | None:
        """Determine trade direction from indicator confluence."""
        if ctx.ema_crossover == "bullish" and not ctx.rsi.is_overbought:
            # Squeeze releasing with positive momentum confirms, but not required
            if ctx.squeeze.squeeze_releasing and ctx.squeeze.momentum <= 0:
                return None
            return "buy"
        if ctx.ema_crossover == "bearish" and not ctx.rsi.is_oversold:
            if ctx.squeeze.squeeze_releasing and ctx.squeeze.momentum >= 0:
                return None
            return "sell"
        return None

    def _compute_confidence(self, ctx: TechnicalContext, side: str) -> float:
        """Regime-adaptive weighted confidence from indicator strength."""
        # Determine regime-based weights
        if self._regime_adaptive:
            regime = ctx.regime.regime if ctx.regime else "transitional"
            if regime == "trending":
                w_ema, w_rsi, w_squeeze, w_macd = 0.45, 0.10, 0.15, 0.30
            elif regime == "ranging":
                w_ema, w_rsi, w_squeeze, w_macd = 0.15, 0.40, 0.25, 0.20
            else:
                w_ema, w_rsi, w_squeeze, w_macd = 0.30, 0.25, 0.20, 0.25
        else:
            w_ema, w_rsi, w_squeeze, w_macd = 0.40, 0.30, 0.30, 0.0

        # EMA divergence component
        ema_diff = abs(ctx.ema_fast.value - ctx.ema_slow.value)
        ema_pct = ema_diff / ctx.ema_slow.value if ctx.ema_slow.value > 0 else 0.0
        ema_score = min(ema_pct / 0.05, 1.0)

        # RSI extremity component
        if side == "buy":
            rsi_score = max(0.0, (50.0 - ctx.rsi.rsi) / 50.0)
        else:
            rsi_score = max(0.0, (ctx.rsi.rsi - 50.0) / 50.0)

        # StochRSI timing boost
        if side == "buy" and ctx.rsi.stoch_rsi < 0.2:
            rsi_score = min(1.0, rsi_score + 0.2)
        elif side == "sell" and ctx.rsi.stoch_rsi > 0.8:
            rsi_score = min(1.0, rsi_score + 0.2)

        # Squeeze component
        squeeze_score = 0.5
        if ctx.squeeze.squeeze_releasing:
            if (side == "buy" and ctx.squeeze.momentum > 0) or (side == "sell" and ctx.squeeze.momentum < 0):
                squeeze_score = 1.0
        elif ctx.squeeze.is_squeezing:
            squeeze_score = 0.3

        # MACD component
        macd_score = 0.0
        if self._macd_enabled and ctx.macd is not None:
            histogram_threshold = 0.005
            macd_score = min(abs(ctx.macd.histogram) / histogram_threshold, 1.0)
            # Boost if MACD crossover confirms the side
            if (side == "buy" and ctx.macd.crossover == "bullish") or (
                side == "sell" and ctx.macd.crossover == "bearish"
            ):
                macd_score = min(1.0, macd_score + 0.2)
            # Boost if divergence confirms
            if ctx.divergence:
                if side == "buy" and ctx.divergence.macd_divergence == "bullish":
                    macd_score = min(1.0, macd_score + 0.15)
                elif side == "sell" and ctx.divergence.macd_divergence == "bearish":
                    macd_score = min(1.0, macd_score + 0.15)

        return ema_score * w_ema + rsi_score * w_rsi + squeeze_score * w_squeeze + macd_score * w_macd

    def _build_reason(self, ctx: TechnicalContext, side: str) -> str:
        parts = [
            f"ema_cross={ctx.ema_crossover}",
            f"rsi={ctx.rsi.rsi:.1f}",
            f"trend={ctx.trend_direction}",
            f"price_change={ctx.price_change_pct:+.2%}",
        ]
        if ctx.squeeze.squeeze_releasing:
            parts.append("squeeze_release")
        if self._macd_enabled and ctx.macd is not None:
            parts.append(f"macd={ctx.macd.crossover}")
        if self._regime_adaptive and ctx.regime is not None:
            parts.append(f"regime={ctx.regime.regime}")
        return f"TA {side}: " + ", ".join(parts)
