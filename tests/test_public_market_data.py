from urllib.error import HTTPError

import pytest

from paperbot.binance_data import MarketDataError
from paperbot.models import Candle
from paperbot.public_market_data import load_bybit_klines, load_public_klines


def bybit_payload():
    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                ["300000", "101", "103", "100", "102", "8", "800"],
                ["0", "100", "102", "99", "101", "5", "500"],
            ]
        },
    }


def test_bybit_rows_are_sorted_and_parsed_as_completed_candles():
    candles = load_bybit_klines(
        "BTCUSDT", "5m", 2, now_ms=700000,
        fetcher=lambda url: bybit_payload(), sleep=lambda _: None,
    )
    assert [c.timestamp for c in candles] == [0, 300000]
    assert candles[-1].close == 102
    assert "category=linear" in "https://api.bybit.com/v5/market/kline?category=linear"


def test_bybit_nonzero_response_code_is_explicit():
    with pytest.raises(MarketDataError, match="Bybit market-data response error"):
        load_bybit_klines(fetcher=lambda _: {"retCode": 10001, "retMsg": "bad symbol"})


def test_public_provider_falls_back_and_labels_source(tmp_path):
    def binance_fails(**kwargs):
        raise MarketDataError("HTTP Error 451")

    candle_list = [Candle(0, 100, 102, 99, 101, 5)]
    candles, source = load_public_klines(
        "BTCUSDT", "5m", 1, tmp_path,
        binance_loader=binance_fails,
        bybit_loader=lambda **kwargs: candle_list,
    )
    assert candles == candle_list
    assert source == "Bybit USDT linear perpetual (fallback)"
    assert (tmp_path / "bybit" / "BTCUSDT_5m.json").exists()


def test_all_provider_failures_are_reported(tmp_path):
    def fail(**kwargs):
        raise MarketDataError("HTTP Error 451")

    with pytest.raises(MarketDataError, match="All public market-data providers failed") as exc:
        load_public_klines(
            cache_dir=tmp_path,
            binance_loader=fail,
            bybit_loader=fail,
        )
    assert "Binance: HTTP Error 451" in str(exc.value)
    assert "Bybit fallback: HTTP Error 451" in str(exc.value)
