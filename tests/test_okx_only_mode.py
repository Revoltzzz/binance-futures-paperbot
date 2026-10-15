import pytest

from paperbot import worker
from paperbot.binance_data import MarketDataError
from paperbot.models import Candle


class MemoryStore:
    def __init__(self):
        self.saved = []

    def load(self, session_id):
        return None

    def save(self, session_id, state, expected_revision):
        self.saved.append((session_id, state, expected_revision))
        return 1


def test_worker_accepts_only_okx_perpetual_data_in_okx_only_mode(monkeypatch):
    store = MemoryStore()
    candles = [Candle(1_800_000_000_000, 100, 101, 99, 100)]
    seen = {}

    def loader(**kwargs):
        seen.update(kwargs)
        return candles, "OKX USDT perpetual swap (fallback)"

    monkeypatch.setattr(worker, "load_public_klines", loader)
    message = worker.run_cycle(
        store, symbol="BTCUSDT", interval="5m", starting_balance=1000,
        strategy_name="ema", market_data_mode="okx-only",
    )
    assert "Initialized" in message
    assert seen["okx_only"] is True
    assert seen["binance_only"] is False


def test_worker_rejects_non_okx_source_in_okx_only_mode(monkeypatch):
    store = MemoryStore()
    candles = [Candle(1_800_000_000_000, 100, 101, 99, 100)]
    monkeypatch.setattr(
        worker, "load_public_klines",
        lambda **kwargs: (candles, "Bybit USDT linear perpetual (fallback)"),
    )
    with pytest.raises(MarketDataError, match="OKX-only paper mode"):
        worker.run_cycle(
            store, symbol="BTCUSDT", interval="5m", starting_balance=1000,
            strategy_name="ema", market_data_mode="okx-only",
        )
    assert store.saved == []
}
