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
    exit_mark = 105 * (1 - broker.risk.config.slippage_rate)
    expected = broker.balance + (exit_mark - pos.entry_price) * pos.quantity - exit_mark * pos.quantity * broker.risk.config.fee_rate
    assert broker.equity(105) == pytest.approx(expected)


def test_trade_pnl_and_balance_include_entry_and_exit_fees():
    broker = PaperBroker(1000)
    pos = broker.open_position(Side.LONG, 100)
    assert pos is not None
    assert 0 < pos.quantity < 10
    trade = broker.close_position(110, "test_exit")
    assert trade is not None
    expected_entry_fee = pos.entry_price * pos.quantity * broker.risk.config.fee_rate
    expected_exit_price = 110 * (1 - broker.risk.config.slippage_rate)
    expected_exit_fee = expected_exit_price * pos.quantity * broker.risk.config.fee_rate
    expected_net_pnl = (expected_exit_price - pos.entry_price) * pos.quantity - expected_entry_fee - expected_exit_fee
    assert trade.fees == pytest.approx(expected_entry_fee + expected_exit_fee)
    assert trade.pnl == pytest.approx(expected_net_pnl)
    assert broker.balance == pytest.approx(1000 + expected_net_pnl)



def test_slippage_is_applied_adversely_to_both_sides():
    broker = PaperBroker(1000)
    long_pos = broker.open_position(Side.LONG, 100)
    assert long_pos is not None
    assert long_pos.entry_price > 100
    trade = broker.close_position(100)
    assert trade is not None
    assert trade.exit_price < 100
    assert trade.pnl < 0


def test_positive_funding_charges_longs_and_credits_shorts():
    long_broker = PaperBroker(1000)
    long_pos = long_broker.open_position(Side.LONG, 100)
    assert long_pos is not None
    before_long = long_broker.balance
    long_cost = long_broker.apply_funding(100, 0.001)
    assert long_cost > 0
    assert long_broker.balance == pytest.approx(before_long - long_cost)

    short_broker = PaperBroker(1000)
    short_pos = short_broker.open_position(Side.SHORT, 100)
    assert short_pos is not None
    before_short = short_broker.balance
    short_cost = short_broker.apply_funding(100, 0.001)
    assert short_cost < 0
    assert short_broker.balance == pytest.approx(before_short - short_cost)


def test_funding_cost_is_included_in_trade_net_pnl():
    broker = PaperBroker(1000)
    pos = broker.open_position(Side.LONG, 100)
    assert pos is not None
    funding_cost = broker.apply_funding(100, 0.001)
    trade = broker.close_position(101)
    assert trade is not None
    assert trade.funding_cost == pytest.approx(funding_cost)
    assert trade.pnl == pytest.approx(
        (trade.exit_price - trade.entry_price) * trade.quantity
        - trade.fees - trade.funding_cost
    )
