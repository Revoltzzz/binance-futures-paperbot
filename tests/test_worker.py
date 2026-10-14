from datetime import datetime, timezone

import paperbot.worker as worker
from paperbot.models import Candle
from paperbot.storage import SQLitePaperStore


def _candles(start, count):
    return [
        Candle(start + index * 300_000, 100, 101, 99, 100.5, 10)
        for index in range(count)
    ]


def test_worker_persists_initial_state_and_processes_new_candle_once(tmp_path, monkeypatch):
    start = 1_800_000_000_000
    candles = _candles(start, 2)
    clock = {"now_ms": start + 300_000 + 60_000}

    class Clock:
        @staticmethod
        def now(tz=None):
            return datetime.fromtimestamp(clock["now_ms"] / 1000, tz=timezone.utc)

    monkeypatch.setattr(worker, "datetime", Clock)
    monkeypatch.setattr(
        worker,
        "load_public_klines",
        lambda **kwargs: (list(candles), "Binance USDⓈ-M Futures"),
    )
    store = SQLitePaperStore(tmp_path / "paper.sqlite")

    message = worker.run_cycle(
        store, symbol="BTCUSDT", interval="5m",
        starting_balance=1000, strategy_name="ema",
    )
    assert "Initialized" in message
    loaded = store.load("BTCUSDT:5m")
    assert loaded is not None
    assert loaded[0] == 1
    assert loaded[1]["last_timestamp"] == candles[-1].timestamp

    candles.append(_candles(candles[-1].timestamp + 300_000, 1)[0])
    clock["now_ms"] = candles[-1].timestamp + 300_000 + 60_000
    message = worker.run_cycle(
        store, symbol="BTCUSDT", interval="5m",
        starting_balance=1000, strategy_name="ema",
    )
    assert "Processed 1 candle(s)" in message
    loaded_after = store.load("BTCUSDT:5m")
    assert loaded_after is not None
    assert loaded_after[0] == 2
    assert loaded_after[1]["last_timestamp"] == candles[-1].timestamp

    message = worker.run_cycle(
        store, symbol="BTCUSDT", interval="5m",
        starting_balance=1000, strategy_name="ema",
    )
    assert "No new candles" in message
    loaded_duplicate = store.load("BTCUSDT:5m")
    assert loaded_duplicate is not None
    assert loaded_duplicate[1]["last_timestamp"] == candles[-1].timestamp
    assert loaded_duplicate[1]["broker"] == loaded_after[1]["broker"]


def test_worker_refuses_spot_proxy(tmp_path, monkeypatch):
    start = 1_800_000_000_000
    candles = _candles(start, 2)
    clock = {"now_ms": start + 300_000 + 60_000}

    class Clock:
        @staticmethod
        def now(tz=None):
            return datetime.fromtimestamp(clock["now_ms"] / 1000, tz=timezone.utc)

    monkeypatch.setattr(worker, "datetime", Clock)
    monkeypatch.setattr(
        worker,
        "load_public_klines",
        lambda **kwargs: (candles, "Kraken spot proxy (NOT futures data)"),
    )
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    try:
        worker.run_cycle(
            store, symbol="BTCUSDT", interval="5m",
            starting_balance=1000, strategy_name="ema",
        )
    except Exception as exc:
        assert "spot-proxy" in str(exc)
    else:
        raise AssertionError("worker must refuse spot-proxy market data")
    assert store.load("BTCUSDT:5m") is None
