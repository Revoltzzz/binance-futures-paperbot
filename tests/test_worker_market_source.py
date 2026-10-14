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


def test_worker_refuses_okx_fallback_without_updating_account(monkeypatch, tmp_path):
    store = MemoryStore()
    candles = [Candle(1792015200000, 100, 101, 99, 100, 1)]
    monkeypatch.setattr(
        worker,
        "load_public_klines",
        lambda **kwargs: (candles, "OKX USDT perpetual swap (fallback)"),
    )

    with pytest.raises(MarketDataError, match="Binance-only paper mode"):
        worker.run_cycle(
            store,
            symbol="BTCUSDT",
            interval="5m",
            starting_balance=1000,
            strategy_name="ema",
        )

    assert store.saved == []
