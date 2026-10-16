from paperbot.models import Candle, Side
from paperbot.session import PaperTradingSession


def _candles(count=3, start=1_800_000_300_000):
    return [
        Candle(start + i * 300_000, 100, 100.5, 99.5, 100, 10)
        for i in range(count)
    ]


def _session(candles=None):
    candles = candles or _candles(2)
    return PaperTradingSession(
        symbol="BTCUSDT",
        interval="5m",
        market_source="Binance USDⓈ-M Futures",
        starting_balance=1000,
        initial_candles=candles,
    )


def test_session_processes_only_new_candles_once():
    initial = _candles(2)
    session = _session(initial)
    next_candle = _candles(3)[-1]
    assert session.process_new_candles(initial + [next_candle], session.market_source) == 1
    balance_after_first_process = session.broker.balance
    assert session.process_new_candles(initial + [next_candle], session.market_source) == 0
    assert session.broker.balance == balance_after_first_process


def test_session_executes_signal_on_next_candle_open():
    initial = _candles(2)
    session = _session(initial)

    class SignalOnThirdCandle:
        def signal(self, history):
            return Side.LONG if len(history) == 3 else None

    session.strategy = SignalOnThirdCandle()
    candles = initial + _candles(2, start=initial[-1].timestamp + 300_000)
    assert session.process_new_candles(candles[:3], session.market_source) == 1
    assert session.broker.position is None
    assert session.pending_signal == Side.LONG

    assert session.process_new_candles(candles, session.market_source) == 1
    assert session.broker.position is not None
    assert session.broker.position.side == Side.LONG
    assert session.broker.position.entry_price > candles[-1].open


def test_session_pauses_when_a_candle_gap_is_detected():
    initial = _candles(2)
    session = _session(initial)
    gap_candle = Candle(initial[-1].timestamp + 600_000, 100, 100.5, 99.5, 100, 10)

    assert session.process_new_candles(initial + [gap_candle], session.market_source) == 0
    assert session.paused_reason is not None
    assert "gap" in session.paused_reason.lower()


def test_session_pauses_if_market_data_provider_changes():
    initial = _candles(2)
    session = _session(initial)
    assert session.process_new_candles(initial, "Bybit USDT linear perpetual") == 0
    assert session.paused_reason is not None
    assert "provider changed" in session.paused_reason.lower()

def test_restore_rejects_gapped_candle_history():
    import pytest

    session = _session(_candles(3))
    state = session.to_dict()
    state["history"][1][0] += 300_000

    with pytest.raises(ValueError, match="history is gapped"):
        PaperTradingSession.from_dict(state)


def test_restore_rejects_non_finite_broker_balance():
    import pytest

    session = _session()
    state = session.to_dict()
    state["broker"]["balance"] = float("nan")

    with pytest.raises(ValueError, match="balance must be finite"):
        PaperTradingSession.from_dict(state)


def test_restore_rejects_inconsistent_open_position():
    import pytest

    session = _session()
    state = session.to_dict()
    state["broker"]["position"] = {
        "side": "long",
        "quantity": 1.0,
        "entry_price": 100.0,
        "stop_price": 101.0,
        "take_profit_price": 102.0,
        "entry_fee": 0.1,
        "funding_cost": 0.0,
    }

    with pytest.raises(ValueError, match="long position stop/target"):
        PaperTradingSession.from_dict(state)



def test_restore_rejects_non_finite_or_non_positive_session_start_equity():
    import pytest

    for invalid in (float("nan"), float("inf"), 0, -1):
        session = _session()
        state = session.to_dict()
        state["session_start_equity"] = invalid
        with pytest.raises(ValueError, match="session-start equity must be finite and positive"):
            PaperTradingSession.from_dict(state)


def test_restore_rejects_non_boolean_daily_halt_flag():
    import pytest

    session = _session()
    state = session.to_dict()
    state["daily_halted"] = "false"
    with pytest.raises(ValueError, match="daily_halted flag must be boolean"):
        PaperTradingSession.from_dict(state)


def test_restore_rejects_misaligned_candle_timestamps():
    import pytest

    session = _session(_candles(3))
    state = session.to_dict()
    state["history"][1][0] += 1
    with pytest.raises(ValueError, match="timestamp is not aligned"):
        PaperTradingSession.from_dict(state)
