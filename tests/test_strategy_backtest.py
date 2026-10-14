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
    assert result.ending_balance > 1000


def test_backtest_rejects_duplicate_or_out_of_order_timestamps():
    candles = [Candle(2, 100, 101, 99, 100), Candle(2, 100, 101, 99, 100)]
    with pytest.raises(ValueError, match="strictly increasing"):
        run_backtest(candles)


def test_backtest_rejects_nonpositive_starting_balance():
    with pytest.raises(ValueError, match="starting_balance"):
        run_backtest([], starting_balance=0)


def test_daily_loss_limit_closes_position_and_blocks_same_day_entries():
    class AlternatingSignals:
        def signal(self, history):
            return Side.LONG if len(history) % 2 else Side.SHORT

    # Each open position is stopped out. Three roughly 1%-risk losses plus fees
    # should breach the 3% daily equity limit and halt further entries.
    day = 1_800_000_000_000
    candles = [
        Candle(day + i * 300_000, 100, 102, 98, 100)
        for i in range(8)
    ]
    result = run_backtest(candles, strategy=AlternatingSignals())
    assert result.trades == 3
    assert result.losses == 3
    assert result.ending_balance < 970



def test_backtest_journal_records_trade_timestamps_fees_and_exit_reason():
    class FirstCandleLong:
        def signal(self, history):
            return Side.LONG if len(history) == 1 else None

    candles = [
        Candle(1_800_000_000_000, 100, 100.5, 99.5, 100),
        Candle(1_800_000_300_000, 102, 102.5, 101.5, 102),
        Candle(1_800_000_600_000, 103, 103.5, 102.5, 103),
    ]
    result = run_backtest(candles, strategy=FirstCandleLong())
    assert result.trades == len(result.trade_history) == 1
    trade = result.trade_history[0]
    assert trade.entry_time_utc == "2027-01-15T08:05:00+00:00"
    assert trade.exit_time_utc == "2027-01-15T08:10:00+00:00"
    assert trade.side == "long"
    assert trade.exit_reason == "end_of_data"
    assert trade.fees > 0
    assert trade.net_pnl == pytest.approx(trade.gross_pnl - trade.fees)
    assert trade.balance_after == pytest.approx(result.ending_balance)


def test_daily_loss_circuit_breaker_is_in_risk_journal():
    class AlternatingSignals:
        def signal(self, history):
            return Side.LONG if len(history) % 2 else Side.SHORT

    day = 1_800_000_000_000
    candles = [Candle(day + i * 300_000, 100, 102, 98, 100) for i in range(8)]
    result = run_backtest(candles, strategy=AlternatingSignals())
    assert len(result.risk_events) == 1
    event = result.risk_events[0]
    assert event.event == "daily_loss_limit"
    assert event.balance_after < 1000
    assert "blocked" in event.message


def test_candle_ohlc_validation():
    with pytest.raises(ValueError):
        Candle(1, 100, 99, 98, 100)
