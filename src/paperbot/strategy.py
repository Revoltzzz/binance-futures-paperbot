import math

from .models import Candle, Side


def ema(values: list[float], period: int) -> list[float]:
    """EMA with a seeded simple average; entries before seed are NaN."""
    if not isinstance(period, int) or isinstance(period, bool) or period < 1:
        raise ValueError("period must be an integer >= 1")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("EMA values must be finite")
    out = [float("nan")] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


class EmaCrossStrategy:
    """Simple EMA crossover baseline; demo only, no profit guarantee."""
    def __init__(self, fast_period: int = 5, slow_period: int = 12):
        if (
            not isinstance(fast_period, int) or isinstance(fast_period, bool)
            or not isinstance(slow_period, int) or isinstance(slow_period, bool)
            or fast_period < 1 or slow_period <= fast_period
        ):
            raise ValueError("require integer periods with 1 <= fast_period < slow_period")
        self.fast_period, self.slow_period = fast_period, slow_period

    def signal(self, candles: list[Candle]) -> Side | None:
        if len(candles) < self.slow_period + 1:
            return None
        closes = [c.close for c in candles]
        fast, slow = ema(closes, self.fast_period), ema(closes, self.slow_period)
        i = len(closes) - 1
        if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
            return Side.LONG
        if fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
            return Side.SHORT
        return None


class BreakRetestStrategy:
    """Experimental price-action strategy inspired by structure and break/retest concepts.

    This is deliberately a separate candidate, not a replacement for the EMA baseline.
    It uses only completed candles supplied in history and confirms entries on the
    retest candle close. It is a simple mechanical approximation, not a full discretionary
    supply/demand or multi-timeframe implementation.
    """

    def __init__(
        self,
        lookback: int = 20,
        trend_period: int = 50,
        retest_bars: int = 3,
        tolerance_pct: float = 0.002,
    ):
        if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback < 2:
            raise ValueError("lookback must be an integer >= 2")
        if not isinstance(trend_period, int) or isinstance(trend_period, bool) or trend_period < 2:
            raise ValueError("trend_period must be an integer >= 2")
        if not isinstance(retest_bars, int) or isinstance(retest_bars, bool) or retest_bars < 1:
            raise ValueError("retest_bars must be an integer >= 1")
        if not math.isfinite(tolerance_pct) or not 0 <= tolerance_pct < 0.05:
            raise ValueError("tolerance_pct must be finite and in [0, 0.05)")
        self.lookback = lookback
        self.trend_period = trend_period
        self.retest_bars = retest_bars
        self.tolerance_pct = tolerance_pct

    def signal(self, candles: list[Candle]) -> Side | None:
        """Signal on a confirmed retest after a prior close beyond a rolling level."""
        if len(candles) < max(self.lookback + 2, self.trend_period):
            return None

        closes = [c.close for c in candles]
        trend = ema(closes, self.trend_period)[-1]
        current = candles[-1]
        if not math.isfinite(trend):
            return None

        first_break_index = max(self.lookback, len(candles) - 1 - self.retest_bars)
        for index in range(len(candles) - 2, first_break_index - 1, -1):
            breakout = candles[index]
            prior = candles[index - self.lookback:index]
            resistance = max(c.high for c in prior)
            support = min(c.low for c in prior)

            # A bullish break must close above the prior range; the latest candle
            # must retest the broken level and close back above it with a green body.
            if (
                breakout.close > resistance
                and current.low <= resistance * (1 + self.tolerance_pct)
                and current.close > resistance
                and current.close > current.open
                and current.close > trend
            ):
                return Side.LONG

            if (
                breakout.close < support
                and current.high >= support * (1 - self.tolerance_pct)
                and current.close < support
                and current.close < current.open
                and current.close < trend
            ):
                return Side.SHORT

        return None

