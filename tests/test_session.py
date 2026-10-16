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


def test_restore_rejects_non_positive_or_boolean_broker_balance():
    import pytest

    for invalid in (0, -1, True):
        session = _session()
        state = session.to_dict()
        state["broker"]["balance"] = invalid
        with pytest.raises(ValueError, match="broker balance must be finite and positive"):
            PaperTradingSession.from_dict(state)


def test_restore_rejects_invalid_starting_balance():
    import pytest

    for invalid in (0, -1, True, float("nan"), float("inf")):
        session = _session()
        state = session.to_dict()
        state["starting_balance"] = invalid
        with pytest.raises(ValueError, match="starting balance must be a finite positive number"):
            PaperTradingSession.from_dict(state)


def test_restore_rejects_boolean_session_start_equity():
    import pytest

    session = _session()
    state = session.to_dict()
    state["session_start_equity"] = True
    with pytest.raises(ValueError, match="session-start equity must be a finite positive number"):
        PaperTradingSession.from_dict(state)


def test_restore_rejects_malformed_broker_trade_history():
    import pytest

    session = _session()
    state = session.to_dict()
    state["broker"]["trades"] = [{
        "side": "long",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "quantity": 1.0,
        "pnl": float("nan"),
        "fees": 0.1,
        "reason": "take_profit",
        "funding_cost": 0.0,
    }]

    with pytest.raises(ValueError, match="broker trade 0 P&L must be a finite number"):
        PaperTradingSession.from_dict(state)


def test_restore_rejects_non_list_broker_trade_history():
    import pytest

    state = _session().to_dict()
    state["broker"]["trades"] = {}
    with pytest.raises(ValueError, match="broker trade history must be a list"):
        PaperTradingSession.from_dict(state)


def test_restore_rejects_malformed_trade_journal_entry():
    import pytest

    state = _session().to_dict()
    state["trades"] = [{
        "Entry time (UTC)": "2026-10-16T10:00:00+00:00",
        "Exit time (UTC)": "2026-10-16T10:05:00+00:00",
        "Side": "LONG",
        "Entry price": 100.0,
        "Exit price": 101.0,
        "Quantity": 1.0,
        "Net P&L (USDT)": 1.0,
        "Fees (USDT)": float("inf"),
        "Funding cost (USDT)": 0.0,
        "Exit reason": "take_profit",
        "Balance after (USDT)": 1001.0,
    }]

    with pytest.raises(ValueError, match="trade journal entry 0 fees must be a finite number"):
        PaperTradingSession.from_dict(state)


def test_restore_rejects_malformed_event_journal_entry():
    import pytest

    state = _session().to_dict()
    state["events"] = [{
        "Time (UTC)": "2026-10-16T10:00:00+00:00",
        "Event": "daily_loss_limit",
        "Marked equity (USDT)": float("nan"),
        "Balance after (USDT)": 1000.0,
        "Details": "halted",
    }]

    with pytest.raises(ValueError, match="event journal entry 0 marked equity must be a finite number"):
        PaperTradingSession.from_dict(state)


def test_restore_accepts_valid_broker_trade_journal_and_event():
    session = _session()
    state = session.to_dict()
    state["broker"]["trades"] = [{
        "side": "long",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "quantity": 1.0,
        "pnl": 0.8,
        "fees": 0.2,
        "reason": "take_profit",
        "funding_cost": 0.0,
    }]
    state["trades"] = [{
        "Entry time (UTC)": "2026-10-16T10:00:00+00:00",
        "Exit time (UTC)": "2026-10-16T10:05:00+00:00",
        "Side": "LONG",
        "Entry price": 100.0,
        "Exit price": 101.0,
        "Quantity": 1.0,
        "Net P&L (USDT)": 0.8,
        "Fees (USDT)": 0.2,
        "Funding cost (USDT)": 0.0,
        "Exit reason": "take_profit",
        "Balance after (USDT)": 1000.8,
    }]
    state["events"] = [{
        "Time (UTC)": "2026-10-16T10:05:00+00:00",
        "Event": "daily_loss_limit",
        "Marked equity (USDT)": 990.0,
        "Balance after (USDT)": 990.0,
        "Details": "halted",
    }]

    restored = PaperTradingSession.from_dict(state)
    assert len(restored.broker.trades) == 1
    assert len(restored.trades) == 1
    assert len(restored.events) == 1


def test_restore_rejects_boolean_open_position_numbers():
    import pytest

    for field in ("quantity", "entry_price", "stop_price", "take_profit_price", "entry_fee", "funding_cost"):
        session = _session()
        state = session.to_dict()
        state["broker"]["position"] = {
            "side": "long",
            "quantity": 1.0,
            "entry_price": 100.0,
            "stop_price": 99.0,
            "take_profit_price": 102.0,
            "entry_fee": 0.1,
            "funding_cost": 0.0,
        }
        state["broker"]["position"][field] = True
        with pytest.raises(ValueError, match="must not be booleans"):
            PaperTradingSession.from_dict(state)
