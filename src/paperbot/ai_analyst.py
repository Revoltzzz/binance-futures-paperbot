"""Deterministic, explainable multi-timeframe market analysis for the AI analyst UI.

Signals are research suggestions only. This module has no broker or order-routing code.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .models import Candle


@dataclass(frozen=True)
class MarketAnalysis:
    symbol: str
    timeframe: str
    last_price: float
    trend: str
    rsi: float | None
    atr: float | None
    ema_fast: float | None
    ema_slow: float | None
    ema_trend: float | None
    vwap: float | None
    support: tuple[float, ...]
    resistance: tuple[float, ...]
    signal: str
    score: float
    entry_low: float | None
    entry_high: float | None
    stop_loss: float | None
    target_1: float | None
    target_2: float | None
    risk_reward_2: float | None
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = alpha * item + (1 - alpha) * value
    return value


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [b - a for a, b in zip(values, values[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = ((period - 1) * avg_gain + gain) / period
        avg_loss = ((period - 1) * avg_loss + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    true_ranges = []
    for previous, current in zip(candles, candles[1:]):
        true_ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    value = sum(true_ranges[:period]) / period
    for item in true_ranges[period:]:
        value = ((period - 1) * value + item) / period
    return value


def _vwap(candles: list[Candle], window: int = 96) -> float | None:
    sample = candles[-window:]
    volume = sum(item.volume for item in sample)
    if volume <= 0:
        return None
    return sum(((item.high + item.low + item.close) / 3) * item.volume
               for item in sample) / volume


def _levels(candles: list[Candle], radius: int = 3, max_levels: int = 4) -> tuple[tuple[float, ...], tuple[float, ...]]:
    sample = candles[-160:]
    lows, highs = [], []
    for index in range(radius, len(sample) - radius):
        item = sample[index]
        neighborhood = sample[index-radius:index] + sample[index+1:index+radius+1]
        if all(item.low < other.low for other in neighborhood):
            lows.append(item.low)
        if all(item.high > other.high for other in neighborhood):
            highs.append(item.high)

    def cluster(values: list[float]) -> tuple[float, ...]:
        if not values:
            return ()
        values.sort()
        groups: list[list[float]] = [[values[0]]]
        for value in values[1:]:
            center = sum(groups[-1]) / len(groups[-1])
            if abs(value - center) / center <= 0.003:
                groups[-1].append(value)
            else:
                groups.append([value])
        return tuple(round(sum(group) / len(group), 8) for group in groups[-max_levels:])

    return cluster(lows), cluster(highs)


def analyze_market(
    candles: Iterable[Candle], symbol: str, timeframe: str, *,
    mode: str = "intraday", min_score: float = 4.0,
) -> MarketAnalysis:
    """Return a transparent long/short/watch plan from completed candles only."""
    data = list(candles)
    if len(data) < 60:
        raise ValueError("At least 60 completed candles are required for analysis")
    if mode not in {"scalping", "intraday"}:
        raise ValueError("mode must be scalping or intraday")
    closes = [c.close for c in data]
    latest = data[-1]
    fast_period, slow_period, trend_period = (9, 21, 50) if mode == "scalping" else (12, 26, 50)
    fast, slow, trend_ema = (_ema(closes, p) for p in (fast_period, slow_period, trend_period))
    rsi, atr, vwap = _rsi(closes), _atr(data), _vwap(data)
    support, resistance = _levels(data)
    warnings: list[str] = []
    reasons: list[str] = []
    if atr is None or fast is None or slow is None or trend_ema is None:
        raise ValueError("Insufficient candles to calculate indicators")

    recent_volume = [c.volume for c in data[-21:-1]]
    avg_volume = sum(recent_volume) / len(recent_volume) if recent_volume else 0.0
    volume_ok = avg_volume > 0 and latest.volume >= avg_volume * 1.15
    bullish = fast > slow and latest.close > trend_ema
    bearish = fast < slow and latest.close < trend_ema
    if bullish:
        reasons.append(f"EMA{fast_period} is above EMA{slow_period}; price is above EMA{trend_period}")
    if bearish:
        reasons.append(f"EMA{fast_period} is below EMA{slow_period}; price is below EMA{trend_period}")
    if vwap is not None and ((bullish and latest.close > vwap) or (bearish and latest.close < vwap)):
        reasons.append("Price is on the trend side of rolling VWAP")
    if volume_ok:
        reasons.append("Latest completed candle volume is at least 1.15× the prior 20-candle average")
    if rsi is not None:
        reasons.append(f"RSI(14) is {rsi:.1f}")

    long_score = 0.0
    short_score = 0.0
    long_score += 2.0 if bullish else 0.0
    short_score += 2.0 if bearish else 0.0
    if vwap is not None:
        long_score += 1.0 if latest.close > vwap else 0.0
        short_score += 1.0 if latest.close < vwap else 0.0
    long_score += 1.0 if rsi is not None and 50 <= rsi <= 68 else 0.0
    short_score += 1.0 if rsi is not None and 32 <= rsi <= 50 else 0.0
    long_score += 1.0 if volume_ok else 0.0
    short_score += 1.0 if volume_ok else 0.0
    # A close through the preceding 20-candle extreme is a breakout confirmation.
    prior = data[-21:-1]
    long_score += 1.0 if latest.close > max(c.high for c in prior) else 0.0
    short_score += 1.0 if latest.close < min(c.low for c in prior) else 0.0

    direction = "LONG" if long_score >= short_score else "SHORT"
    score = max(long_score, short_score)
    if score < min_score or (direction == "LONG" and not bullish) or (direction == "SHORT" and not bearish):
        direction = "NO TRADE"
    if atr / latest.close > 0.04:
        warnings.append("Volatility is elevated (>4% ATR/price); reduce risk or wait.")
        direction = "NO TRADE"
    if len(data) < 100:
        warnings.append("Short history: treat support/resistance and scores as provisional.")

    entry_low = entry_high = stop = target1 = target2 = rr2 = None
    if direction in {"LONG", "SHORT"}:
        entry_low = round(latest.close - atr * 0.15, 8)
        entry_high = round(latest.close + atr * 0.15, 8)
        risk = atr * (1.35 if mode == "scalping" else 1.6)
        if direction == "LONG":
            stop = round(latest.close - risk, 8)
            target1 = round(latest.close + risk * 1.5, 8)
            target2 = round(latest.close + risk * 2.5, 8)
        else:
            stop = round(latest.close + risk, 8)
            target1 = round(latest.close - risk * 1.5, 8)
            target2 = round(latest.close - risk * 2.5, 8)
        rr2 = 2.5
        reasons.append("Entry zone and ATR-based exits are estimates; wait for a candle close and retest/confirmation.")
    else:
        warnings.append("No setup currently meets the trend and confluence threshold. Do not force a trade.")

    trend = "BULLISH" if bullish else "BEARISH" if bearish else "MIXED"
    return MarketAnalysis(
        symbol=symbol.upper(), timeframe=timeframe, last_price=latest.close,
        trend=trend, rsi=round(rsi, 2) if rsi is not None else None,
        atr=round(atr, 8), ema_fast=round(fast, 8), ema_slow=round(slow, 8),
        ema_trend=round(trend_ema, 8), vwap=round(vwap, 8) if vwap is not None else None,
        support=support, resistance=resistance, signal=direction, score=round(score, 2),
        entry_low=entry_low, entry_high=entry_high, stop_loss=stop,
        target_1=target1, target_2=target2, risk_reward_2=rr2,
        reasons=tuple(reasons), warnings=tuple(warnings),
    )


def classify_news_sentiment(title: str, summary: str = "") -> tuple[str, int]:
    """Small transparent keyword heuristic; not an LLM fact-check or trade trigger."""
    text = f"{title} {summary}".lower()
    positive = ("approval", "inflow", "adoption", "partnership", "upgrade", "launch",
                "bullish", "surge", "record high", "integration", "accumulation")
    negative = ("hack", "exploit", "outflow", "lawsuit", "ban", "secures", "liquidation",
                "bearish", "crash", "lawsuit", "breach", "insolvency", "delist")
    score = sum(text.count(word) for word in positive) - sum(text.count(word) for word in negative)
    label = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"
    return label, score
