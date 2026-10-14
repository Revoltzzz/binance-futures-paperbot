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
