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
    assert loaded_duplicate[0] == loaded_after[0]  # polling without a new candle is a true no-op
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


def test_worker_allows_explicit_perpetual_futures_fallback_for_paper_research(tmp_path, monkeypatch):
    start = 1_800_000_000_000
    candles = _candles(start, 2)
    clock = {"now_ms": start + 300_000 + 60_000}

    class Clock:
        @staticmethod
        def now(tz=None):
            return datetime.fromtimestamp(clock["now_ms"] / 1000, tz=timezone.utc)

    monkeypatch.setattr(worker, "datetime", Clock)
    seen = {}
    def load(**kwargs):
        seen.update(kwargs)
        return list(candles), "Bybit USDT linear perpetual (fallback)"
    monkeypatch.setattr(worker, "load_public_klines", load)
    store = SQLitePaperStore(tmp_path / "paper.sqlite")

    message = worker.run_cycle(
        store, symbol="BTCUSDT", interval="5m", starting_balance=1000,
        strategy_name="ema", market_data_mode="derivatives-fallback",
    )
    assert "Initialized" in message
    assert seen["binance_only"] is False
    loaded = store.load("BTCUSDT:5m")
    assert loaded is not None
    assert loaded[1]["market_source"] == "Bybit USDT linear perpetual (fallback)"


def test_derivatives_fallback_mode_still_refuses_spot_data(tmp_path, monkeypatch):
    monkeypatch.setattr(
        worker,
        "load_public_klines",
        lambda **kwargs: (_candles(1_800_000_000_000, 2), "Kraken spot proxy (NOT futures data)"),
    )
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    import pytest
    with pytest.raises(worker.MarketDataError, match="spot-proxy data is never accepted"):
        worker.run_cycle(
            store, symbol="BTCUSDT", interval="5m", starting_balance=1000,
            strategy_name="ema", market_data_mode="derivatives-fallback",
        )
    assert store.load("BTCUSDT:5m") is None


def test_worker_rejects_unknown_market_data_mode(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="market_data_mode"):
        worker.run_cycle(
            SQLitePaperStore(tmp_path / "paper.sqlite"),
            symbol="BTCUSDT", interval="5m", starting_balance=1000,
            strategy_name="ema", market_data_mode="anything-goes",
        )

def test_error_backoff_grows_exponentially_and_is_capped():
    assert worker._error_backoff_seconds(60, 0) == 60
    assert worker._error_backoff_seconds(60, 1) == 120
    assert worker._error_backoff_seconds(60, 2) == 240
    assert worker._error_backoff_seconds(60, 10) == 900
    assert worker._error_backoff_seconds(60, 20, 3600) == 3600


def test_error_backoff_rejects_invalid_inputs():
    import pytest

    with pytest.raises(ValueError):
        worker._error_backoff_seconds(0, 1)
    with pytest.raises(ValueError):
        worker._error_backoff_seconds(60, -1)



def test_binance_restricted_location_error_is_classified():
    error = RuntimeError(
        "All public market-data providers failed. Binance USDⓈ-M Futures: "
        "MarketDataError: HTTP Error 451: Service unavailable from a restricted "
        "location according to Binance terms."
    )
    assert worker._is_binance_eligibility_restriction(error)


def test_unrelated_451_is_not_misclassified():
    assert not worker._is_binance_eligibility_restriction(
        RuntimeError("HTTP Error 451: generic proxy error")
    )


def test_worker_persists_new_safety_pause_without_processing_candles(tmp_path, monkeypatch):
    start = 1_800_000_000_000
    candles = _candles(start, 2)
    clock = {"now_ms": start + 300_000 + 60_000}

    class Clock:
        @staticmethod
        def now(tz=None):
            return datetime.fromtimestamp(clock["now_ms"] / 1000, tz=timezone.utc)

    monkeypatch.setattr(worker, "datetime", Clock)
    source = {"name": "Bybit USDT linear perpetual (fallback)"}
    monkeypatch.setattr(
        worker, "load_public_klines",
        lambda **kwargs: (list(candles), source["name"]),
    )
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    worker.run_cycle(
        store, symbol="BTCUSDT", interval="5m", starting_balance=1000,
        strategy_name="ema", market_data_mode="derivatives-fallback",
    )
    before = store.load("BTCUSDT:5m")
    assert before is not None and before[0] == 1

    source["name"] = "OKX USDT perpetual swap (fallback)"
    message = worker.run_cycle(
        store, symbol="BTCUSDT", interval="5m", starting_balance=1000,
        strategy_name="ema", market_data_mode="derivatives-fallback",
    )
    after = store.load("BTCUSDT:5m")
    assert "PAUSED at revision 2" in message
    assert after is not None and after[0] == 2
    assert "provider changed" in after[1]["paused_reason"].lower()


def test_worker_pauses_when_recent_realized_expectancy_is_non_positive(tmp_path, monkeypatch):
    import pytest

    start = 1_800_000_000_000
    candles = _candles(start, 2)

    class Clock:
        @staticmethod
        def now(tz=None):
            return datetime.fromtimestamp((start + 600_000) / 1000, tz=timezone.utc)

    monkeypatch.setattr(worker, "datetime", Clock)
    monkeypatch.setattr(
        worker, "load_public_klines",
        lambda **kwargs: (list(candles), "Binance USDⓈ-M Futures"),
    )
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    worker.run_cycle(
        store, symbol="BTCUSDT", interval="5m", starting_balance=1000,
        strategy_name="ema",
    )
    loaded = store.load("BTCUSDT:5m")
    assert loaded is not None
    revision, state = loaded
    state["trades"] = [
        {"Net P&L (USDT)": -1.0, "Side": "LONG"} for _ in range(10)
    ]
    store.save("BTCUSDT:5m", state, expected_revision=revision)

    message = worker.run_cycle(
        store, symbol="BTCUSDT", interval="5m", starting_balance=1000,
        strategy_name="ema",
    )
    after = store.load("BTCUSDT:5m")
    assert "PAUSED" in message
    assert "Performance circuit breaker" in message
    assert after is not None and after[0] == revision + 2
    assert "non-positive net expectancy" in after[1]["paused_reason"]
    assert "resetting" in after[1]["paused_reason"]

def test_worker_rejects_empty_candle_history_without_creating_account(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(worker, "load_public_klines", lambda **kwargs: ([], "OKX USDT perpetual swap (fallback)"))
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    with pytest.raises(worker.MarketDataError, match="no candles"):
        worker.run_cycle(
            store, symbol="BTCUSDT", interval="5m", starting_balance=100,
            strategy_name="ema", market_data_mode="okx-only",
        )
    assert store.load("BTCUSDT:5m") is None


def test_worker_rejects_gapped_candle_history_without_mutating_existing_account(tmp_path, monkeypatch):
    import pytest

    start = 1_800_000_000_000
    candles = [_candles(start, 1)[0], _candles(start + 600_000, 1)[0]]
    monkeypatch.setattr(
        worker, "load_public_klines",
        lambda **kwargs: (candles, "OKX USDT perpetual swap (fallback)"),
    )
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    with pytest.raises(worker.MarketDataError, match="missing candles"):
        worker.run_cycle(
            store, symbol="BTCUSDT", interval="5m", starting_balance=100,
            strategy_name="ema", market_data_mode="okx-only",
        )
    assert store.load("BTCUSDT:5m") is None


def test_worker_rejects_misaligned_candle_timestamps(tmp_path, monkeypatch):
    import pytest

    start = 1_800_000_000_000 + 1
    candles = _candles(start, 2)
    monkeypatch.setattr(
        worker, "load_public_klines",
        lambda **kwargs: (candles, "OKX USDT perpetual swap (fallback)"),
    )
    store = SQLitePaperStore(tmp_path / "paper.sqlite")
    with pytest.raises(worker.MarketDataError, match="not aligned"):
        worker.run_cycle(
            store, symbol="BTCUSDT", interval="5m", starting_balance=100,
            strategy_name="ema", market_data_mode="okx-only",
        )
    assert store.load("BTCUSDT:5m") is None

