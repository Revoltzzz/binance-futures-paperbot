from .models import Candle, Side


def ema(values: list[float], period: int) -> list[float]:
    """EMA with a seeded simple average; entries before seed are NaN."""
    if period < 1:
        raise ValueError("period must be >= 1")
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
        if fast_period < 1 or slow_period <= fast_period:
            raise ValueError("require 1 <= fast_period < slow_period")
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
