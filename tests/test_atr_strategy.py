import pytest

from paperbot.models import Candle, Side
from paperbot.strategy import AtrVolatilityBreakoutStrategy


def candles_with_breakout():
    candles = []
    for i in range(60):
        close = 100.0 + i * 0.2
        candles.append(Candle(i * 300_000, close - 0.05, close + 0.1,
                              close - 0.1, close, 100.0))
    # Break above every high in the immediately preceding lookback window.
    candles.append(Candle(60 * 300_000, 112.0, 113.0, 111.8, 112.8, 150.0))
    return candles


def test_atr_breakout_detects_confirmed_upward_range_break():
    strategy = AtrVolatilityBreakoutStrategy(
        lookback=20, atr_period=14, trend_period=10, min_atr_pct=0.001
    )
    assert strategy.signal(candles_with_breakout()) is Side.LONG


def test_atr_breakout_waits_for_enough_history():
    strategy = AtrVolatilityBreakoutStrategy(
        lookback=20, atr_period=14, trend_period=50
    )
    assert strategy.signal(candles_with_breakout()[:49]) is None


def test_atr_breakout_rejects_low_volatility():
    candles = [
        Candle(i * 300_000, 100 + i * 0.001, 100.01 + i * 0.001,
               99.99 + i * 0.001, 100 + i * 0.001, 100.0)
        for i in range(60)
    ]
    strategy = AtrVolatilityBreakoutStrategy(
        lookback=10, atr_period=10, trend_period=10, min_atr_pct=0.02
    )
    assert strategy.signal(candles) is None


@pytest.mark.parametrize("kwargs", [
    {"lookback": 1},
    {"atr_period": 1},
    {"trend_period": 1},
    {"min_atr_pct": -0.01},
    {"min_atr_pct": float("nan")},
])
def test_atr_breakout_rejects_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        AtrVolatilityBreakoutStrategy(**kwargs)
