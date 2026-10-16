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



class RsiMeanReversionStrategy:
    """Experimental RSI reversal with a Bollinger-band location filter.

    Signals only when RSI exits an extreme and price remains stretched outside
    the corresponding band. This is a research candidate, not a claim of edge.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        band_period: int = 20,
        oversold: float = 30.0,
        overbought: float = 70.0,
        band_stddev: float = 2.0,
    ):
        if not isinstance(rsi_period, int) or isinstance(rsi_period, bool) or rsi_period < 2:
            raise ValueError("rsi_period must be an integer >= 2")
        if not isinstance(band_period, int) or isinstance(band_period, bool) or band_period < 2:
            raise ValueError("band_period must be an integer >= 2")
        if (
            not math.isfinite(oversold) or not math.isfinite(overbought)
            or not 0 < oversold < overbought < 100
        ):
            raise ValueError("require 0 < oversold < overbought < 100")
        if not math.isfinite(band_stddev) or not 0.5 <= band_stddev <= 4:
            raise ValueError("band_stddev must be finite and in [0.5, 4]")
        self.rsi_period = rsi_period
        self.band_period = band_period
        self.oversold = oversold
        self.overbought = overbought
        self.band_stddev = band_stddev

    def _rsi(self, closes: list[float]) -> list[float]:
        """Wilder-smoothed RSI; NaN until enough price changes are available."""
        values = [float("nan")] * len(closes)
        if len(closes) <= self.rsi_period:
            return values
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(change, 0.0) for change in changes]
        losses = [max(-change, 0.0) for change in changes]
        avg_gain = sum(gains[:self.rsi_period]) / self.rsi_period
        avg_loss = sum(losses[:self.rsi_period]) / self.rsi_period

        def value(gain: float, loss: float) -> float:
            if loss == 0:
                return 100.0 if gain > 0 else 50.0
            if gain == 0:
                return 0.0
            return 100.0 - 100.0 / (1.0 + gain / loss)

        values[self.rsi_period] = value(avg_gain, avg_loss)
        for index in range(self.rsi_period + 1, len(closes)):
            change_index = index - 1
            avg_gain = ((avg_gain * (self.rsi_period - 1)) + gains[change_index]) / self.rsi_period
            avg_loss = ((avg_loss * (self.rsi_period - 1)) + losses[change_index]) / self.rsi_period
            values[index] = value(avg_gain, avg_loss)
        return values

    def signal(self, candles: list[Candle]) -> Side | None:
        minimum = max(self.rsi_period + 2, self.band_period + 1)
        if len(candles) < minimum:
            return None
        closes = [c.close for c in candles]
        rsi = self._rsi(closes)
        previous_rsi, current_rsi = rsi[-2], rsi[-1]
        if not math.isfinite(previous_rsi) or not math.isfinite(current_rsi):
            return None

        window = closes[-self.band_period:]
        mean = sum(window) / self.band_period
        variance = sum((value - mean) ** 2 for value in window) / self.band_period
        deviation = math.sqrt(variance)
        lower_band = mean - self.band_stddev * deviation
        upper_band = mean + self.band_stddev * deviation
        current = candles[-1]

        if previous_rsi < self.oversold <= current_rsi and current.close <= lower_band:
            return Side.LONG
        if previous_rsi > self.overbought >= current_rsi and current.close >= upper_band:
            return Side.SHORT
        return None

class AtrVolatilityBreakoutStrategy:
    """Experimental range breakout with ATR volatility and EMA trend filters.

    Uses only completed candles. A signal requires a close outside the prior
    range, sufficient recent true-range volatility, and alignment with the
    trend EMA. This is a research candidate, not a profitability claim.
    """

    def __init__(
        self,
        lookback: int = 20,
        atr_period: int = 14,
        trend_period: int = 50,
        min_atr_pct: float = 0.002,
    ):
        for name, value, minimum in (
            ("lookback", lookback, 2),
            ("atr_period", atr_period, 2),
            ("trend_period", trend_period, 2),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if not math.isfinite(min_atr_pct) or not 0 <= min_atr_pct < 0.1:
            raise ValueError("min_atr_pct must be finite and in [0, 0.1)")
        self.lookback = lookback
        self.atr_period = atr_period
        self.trend_period = trend_period
        self.min_atr_pct = min_atr_pct

    def signal(self, candles: list[Candle]) -> Side | None:
        minimum = max(self.lookback + 1, self.atr_period + 1, self.trend_period)
        if len(candles) < minimum:
            return None
        current = candles[-1]
        closes = [c.close for c in candles]
        trend = ema(closes, self.trend_period)[-1]
        if not math.isfinite(trend) or current.close <= 0:
            return None

        # True range includes gaps relative to the previous close.
        recent = candles[-self.atr_period:]
        true_ranges = []
        for index in range(len(candles) - self.atr_period, len(candles)):
            candle = candles[index]
            previous_close = candles[index - 1].close
            true_ranges.append(max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            ))
        atr_pct = (sum(true_ranges) / len(true_ranges)) / current.close
        if atr_pct < self.min_atr_pct:
            return None

        prior = candles[-self.lookback - 1:-1]
        resistance = max(c.high for c in prior)
        support = min(c.low for c in prior)
        if current.close > resistance and current.close > trend:
            return Side.LONG
        if current.close < support and current.close < trend:
            return Side.SHORT
        return None

