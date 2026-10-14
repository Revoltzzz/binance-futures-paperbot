import pytest
from paperbot.models import Candle, Side
from paperbot.paper import PaperBroker


def test_long_take_profit_updates_balance_and_records_trade():
    broker = PaperBroker(1000)
    pos = broker.open_position(Side.LONG, 100)
    assert pos is not None
    candle = Candle(1, 100, pos.take_profit_price + 0.1, 99.5, 100)
    trade = broker.on_candle(candle)
    assert trade is not None and trade.reason == "take_profit"
    assert broker.position is None
    assert len(broker.trades) == 1


def test_stop_loss_is_checked_before_take_profit_when_both_cross():
    broker = PaperBroker(1000)
    pos = broker.open_position(Side.LONG, 100)
    trade = broker.on_candle(Candle(1, 100, pos.take_profit_price + 1, pos.stop_price - 1, 100))
    assert trade.reason == "stop_loss"


def test_only_one_open_position():
    broker = PaperBroker(1000)
    assert broker.open_position(Side.LONG, 100) is not None
    assert broker.open_position(Side.SHORT, 100) is None


def test_invalid_balance_rejected():
    with pytest.raises(ValueError):
        PaperBroker(0)
    with pytest.raises(ValueError, match="finite positive"):
        PaperBroker(float("nan"))


def test_non_finite_execution_prices_are_rejected():
    broker = PaperBroker(1000)
    assert broker.open_position(Side.LONG, float("nan")) is None
    assert broker.open_position(Side.LONG, 100) is not None
    assert broker.close_position(float("inf")) is None
    with pytest.raises(ValueError, match="mark_price"):
        broker.equity(float("nan"))


def test_marked_equity_includes_estimated_exit_fee():
    broker = PaperBroker(1000)
    pos = broker.open_position(Side.LONG, 100)
    assert pos is not None
    expected = broker.balance + (105 - 100) * pos.quantity - 105 * pos.quantity * 0.0004
    assert broker.equity(105) == pytest.approx(expected)


def test_trade_pnl_and_balance_include_entry_and_exit_fees():
    broker = PaperBroker(1000)
    pos = broker.open_position(Side.LONG, 100)
    assert pos is not None
    assert 0 < pos.quantity < 10
    trade = broker.close_position(110, "test_exit")
    assert trade is not None
    expected_entry_fee = 100 * pos.quantity * 0.0004
    expected_exit_fee = 110 * pos.quantity * 0.0004
    expected_net_pnl = 10 * pos.quantity - expected_entry_fee - expected_exit_fee
    assert trade.fees == pytest.approx(expected_entry_fee + expected_exit_fee)
    assert trade.pnl == pytest.approx(expected_net_pnl)
    assert broker.balance == pytest.approx(1000 + expected_net_pnl)
