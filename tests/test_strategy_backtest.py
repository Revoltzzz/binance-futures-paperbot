import pytest

from paperbot.backtest import run_backtest
from paperbot.data import demo_candles
from paperbot.models import Candle, Side
from paperbot.strategy import EmaCrossStrategy, ema


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


def test_backtest_executes_signal_at_next_candle_open():
    class FirstCandleLong:
        def signal(self, history):
            return Side.LONG if len(history) == 1 else None

    candles = [
        Candle(1, 100, 100.5, 99.5, 100),
        Candle(2, 102, 102.5, 101.5, 102),
        Candle(3, 103, 103.5, 102.5, 103),
    ]
    result = run_backtest(candles, strategy=FirstCandleLong())
    assert result.trades == 1
    # The signal uses candle 1's close, but the entry occurs at candle 2's open.
    assert result.ending_balance > 1000


def test_backtest_rejects_duplicate_or_out_of_order_timestamps():
    candles = [Candle(2, 100, 101, 99, 100), Candle(2, 100, 101, 99, 100)]
    with pytest.raises(ValueError, match="strictly increasing"):
        run_backtest(candles)


def test_backtest_rejects_nonpositive_starting_balance():
    with pytest.raises(ValueError, match="starting_balance"):
        run_backtest([], starting_balance=0)


def test_candle_ohlc_validation():
    with pytest.raises(ValueError):
        Candle(1, 100, 99, 98, 100)
