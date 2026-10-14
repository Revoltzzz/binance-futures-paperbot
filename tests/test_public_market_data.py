from paperbot.binance_data import MarketDataError
from paperbot.models import Candle
from paperbot.public_market_data import (
    load_bybit_klines, load_kraken_spot_klines, load_public_klines,
)


def bybit_payload():
    return {
        "retCode": 0, "retMsg": "OK",
        "result": {"list": [
            ["300000", "101", "103", "100", "102", "8", "800"],
            ["0", "100", "102", "99", "101", "5", "500"],
        ]},
    }


def kraken_payload():
    return {
        "error": [],
        "result": {"XXBTZUSD": [
            [0, "100", "102", "99", "101", "100.5", "5", 10],
            [300, "101", "103", "100", "102", "101.5", "8", 12],
        ], "last": "300"},
    }


def test_bybit_rows_are_sorted_and_parsed_as_completed_candles():
    candles = load_bybit_klines("BTCUSDT", "5m", 2, now_ms=700000,
                                fetcher=lambda url: bybit_payload(), sleep=lambda _: None)
    assert [c.timestamp for c in candles] == [0, 300000]
    assert candles[-1].close == 102


def test_bybit_nonzero_response_code_is_explicit():
    import pytest
    with pytest.raises(MarketDataError, match="Bybit market-data response error"):
        load_bybit_klines(fetcher=lambda _: {"retCode": 10001, "retMsg": "bad symbol"})


def test_kraken_spot_rows_are_parsed_and_limited():
    candles = load_kraken_spot_klines("BTCUSDT", "5m", 1, now_ms=700000,
                                     fetcher=lambda url: kraken_payload(), sleep=lambda _: None)
    assert len(candles) == 1
    assert candles[0].timestamp == 300000
    assert candles[0].close == 102


def test_public_provider_falls_back_and_labels_source(tmp_path):
    def binance_fails(**kwargs):
        raise MarketDataError("HTTP Error 451")
    candle_list = [Candle(0, 100, 102, 99, 101, 5)]
    candles, source = load_public_klines(
        "BTCUSDT", "5m", 1, tmp_path, binance_loader=binance_fails,
        bybit_loader=lambda **kwargs: candle_list,
    )
    assert candles == candle_list
    assert source == "Bybit USDT linear perpetual (fallback)"
    assert (tmp_path / "bybit" / "BTCUSDT_5m.json").exists()


def test_kraken_is_used_when_both_futures_providers_fail(tmp_path):
    def fail(**kwargs):
        raise MarketDataError("blocked")
    candle_list = [Candle(0, 100, 102, 99, 101, 5)]
    candles, source = load_public_klines(
        cache_dir=tmp_path, binance_loader=fail, bybit_loader=fail,
        kraken_loader=lambda **kwargs: candle_list,
    )
    assert candles == candle_list
    assert source == "Kraken spot proxy (NOT futures data)"
    assert (tmp_path / "kraken" / "BTCUSDT_5m.json").exists()


def test_all_provider_failures_are_reported(tmp_path):
    def fail(**kwargs):
        raise MarketDataError("HTTP Error 451")
    import pytest
    with pytest.raises(MarketDataError, match="All public market-data providers failed") as exc:
        load_public_klines(cache_dir=tmp_path, binance_loader=fail,
                           bybit_loader=fail, kraken_loader=fail)
    assert "Binance USDⓈ-M Futures: HTTP Error 451" in str(exc.value)
    assert "Bybit USDT linear perpetual (fallback): HTTP Error 451" in str(exc.value)
    assert "Kraken spot proxy (NOT futures data): HTTP Error 451" in str(exc.value)
