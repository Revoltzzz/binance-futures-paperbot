import pytest
from paperbot.data import demo_candles
from paperbot.models import Candle
from paperbot.strategy import EmaCrossStrategy, ema
from paperbot.backtest import run_backtest


def test_ema_seed_and_smoothing():
    values = ema([1, 2, 3, 4], 2)
    assert values[1] == pytest.approx(1.5)
    assert values[2] == pytest.approx(2.5)


def test_strategy_waits_for_enough_history():
    strategy = EmaCrossStrategy(2, 4)
    assert strategy.signal(demo_candles(4)) is None


def test_strategy_rejects_bad_periods():
    with pytest.raises(ValueError):
        EmaCrossStrategy(5, 5)


def test_backtest_is_deterministic_and_finite():
    candles = demo_candles(150)
    a = run_backtest(candles)
    b = run_backtest(candles)
    assert a == b
    assert a.trades >= 0
    assert a.ending_balance > 0
    assert a.max_drawdown_pct >= 0


def test_candle_ohlc_validation():
    with pytest.raises(ValueError):
        Candle(1, 100, 99, 98, 100)
