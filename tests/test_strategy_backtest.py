import pytest

from paperbot.backtest import run_backtest
from paperbot.data import demo_candles
from paperbot.models import Candle, Side
from paperbot.strategy import BreakRetestStrategy, EmaCrossStrategy, ema


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

    # Fee-aware sizing keeps each stopped trade near 1% total risk. Compounding
    # three such losses stays just above 97% equity, so the fourth breach halts entries.
    day = 1_800_000_000_000
    candles = [
        Candle(day + i * 300_000, 100, 102, 98, 100)
        for i in range(8)
    ]
    result = run_backtest(candles, strategy=AlternatingSignals())
    assert result.trades == 4
    assert result.losses == 4
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
    assert trade.net_pnl == pytest.approx(trade.gross_pnl - trade.fees - trade.funding_cost)
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



def test_backtest_applies_fixed_funding_at_utc_eight_hour_boundary():
    class FirstCandleLong:
        def signal(self, history):
            return Side.LONG if len(history) == 1 else None

    candles = [
        Candle(1_799_999_400_000, 100, 100.5, 99.5, 100),
        Candle(1_799_999_700_000, 100, 100.5, 99.5, 100),
        Candle(1_800_000_000_000, 100, 100.5, 99.5, 100),
    ]
    result = run_backtest(candles, strategy=FirstCandleLong())
    assert result.trades == 1
    trade = result.trade_history[0]
    assert trade.funding_cost > 0
    assert trade.net_pnl == pytest.approx(
        trade.gross_pnl - trade.fees - trade.funding_cost
    )



def test_backtest_uses_supplied_historical_funding_rate():
    class FirstCandleLong:
        def signal(self, history):
            return Side.LONG if len(history) == 1 else None

    boundary_ms = 1_800_000_000_000
    candles = [
        Candle(boundary_ms - 600_000, 100, 100.5, 99.5, 100),
        Candle(boundary_ms - 300_000, 100, 100.5, 99.5, 100),
        Candle(boundary_ms, 100, 100.5, 99.5, 100),
    ]
    result = run_backtest(candles, strategy=FirstCandleLong(),
                          funding_rates={boundary_ms: 0.001})
    assert result.trades == 1
    assert result.trade_history[0].funding_cost > 0


def test_backtest_fails_explicitly_when_historical_funding_rate_is_missing():
    class FirstCandleLong:
        def signal(self, history):
            return Side.LONG if len(history) == 1 else None

    boundary_ms = 1_800_000_000_000
    candles = [
        Candle(boundary_ms - 600_000, 100, 100.5, 99.5, 100),
        Candle(boundary_ms - 300_000, 100, 100.5, 99.5, 100),
        Candle(boundary_ms, 100, 100.5, 99.5, 100),
    ]
    with pytest.raises(ValueError, match="Missing historical funding rate"):
        run_backtest(candles, strategy=FirstCandleLong(), funding_rates={})


def test_backtest_applies_historical_funding_at_nonstandard_event_timestamp():
    class FirstCandleLong:
        def signal(self, history):
            return Side.LONG if len(history) == 1 else None

    # A non-8-hour event proves historical mode follows the supplied venue schedule.
    event_ms = 1_800_000_000_000 + 2 * 60 * 60 * 1000
    candles = [
        Candle(event_ms - 600_000, 100, 100.5, 99.5, 100),
        Candle(event_ms - 300_000, 100, 100.5, 99.5, 100),
        Candle(event_ms, 100, 100.5, 99.5, 100),
    ]
    result = run_backtest(
        candles,
        strategy=FirstCandleLong(),
        funding_rates={event_ms: 0.001},
    )
    assert result.trades == 1
    assert result.trade_history[0].funding_cost > 0


def test_break_retest_strategy_signals_long_only_after_confirmed_retest():
    candles = [
        Candle(1, 98, 100, 97, 99),
        Candle(2, 99, 101, 98, 100),
        Candle(3, 100, 101, 99, 100.5),
        Candle(4, 101, 104, 100.5, 103),  # Close above prior resistance.
        Candle(5, 101.5, 103, 100.9, 102.5),  # Retest and bullish close.
    ]
    strategy = BreakRetestStrategy(
        lookback=3, trend_period=3, retest_bars=2, tolerance_pct=0.002
    )
    assert strategy.signal(candles) == Side.LONG


def test_break_retest_strategy_does_not_chase_breakout_without_retest():
    candles = [
        Candle(1, 98, 100, 97, 99),
        Candle(2, 99, 101, 98, 100),
        Candle(3, 100, 101, 99, 100.5),
        Candle(4, 101, 104, 100.5, 103),
        Candle(5, 103, 105, 102.5, 104.5),
    ]
    strategy = BreakRetestStrategy(
        lookback=3, trend_period=3, retest_bars=2, tolerance_pct=0.002
    )
    assert strategy.signal(candles) is None


def test_break_retest_strategy_validates_parameters():
    with pytest.raises(ValueError, match="lookback"):
        BreakRetestStrategy(lookback=1)
    with pytest.raises(ValueError, match="trend_period"):
        BreakRetestStrategy(trend_period=1)
    with pytest.raises(ValueError, match="retest_bars"):
        BreakRetestStrategy(retest_bars=0)
    with pytest.raises(ValueError, match="tolerance_pct"):
        BreakRetestStrategy(tolerance_pct=0.1)


def test_backtest_reports_profit_factor_and_net_expectancy():
    class AlternatingSignals:
        def signal(self, history):
            return Side.LONG if len(history) % 2 else Side.SHORT

    day = 1_800_000_000_000
    candles = [
        Candle(day + i * 300_000, 100, 102, 98, 100)
        for i in range(8)
    ]
    result = run_backtest(candles, strategy=AlternatingSignals())
    net = [trade.net_pnl for trade in result.trade_history]
    expected_profit = sum(value for value in net if value > 0)
    expected_loss = -sum(value for value in net if value < 0)
    assert result.gross_profit == pytest.approx(expected_profit)
    assert result.gross_loss == pytest.approx(expected_loss)
    assert result.profit_factor == pytest.approx(expected_profit / expected_loss)
    assert result.expectancy_per_trade == pytest.approx(sum(net) / len(net))


def test_profit_factor_is_none_when_there_are_no_losing_trades():
    class FirstCandleLong:
        def signal(self, history):
            return Side.LONG if len(history) == 1 else None

    candles = [
        Candle(1_800_000_000_000, 100, 100.5, 99.5, 100),
        Candle(1_800_000_300_000, 102, 102.5, 101.5, 102),
        Candle(1_800_000_600_000, 103, 103.5, 102.5, 103),
    ]
    result = run_backtest(candles, strategy=FirstCandleLong())
    assert result.profit_factor is None

