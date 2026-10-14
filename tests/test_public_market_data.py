import pytest

from paperbot.binance_data import MarketDataError
from paperbot.models import Candle
from paperbot.public_market_data import (
    load_bybit_klines, load_kraken_spot_klines, load_okx_swap_klines, load_public_klines,
)


def bybit_payload():
    return {
        "retCode": 0, "retMsg": "OK",
        "result": {"list": [
            ["300000", "101", "103", "100", "102", "8", "800"],
            ["0", "100", "102", "99", "101", "5", "500"],
        ]},
    }


def okx_payload():
    return {
        "code": "0", "msg": "",
        "data": [
            ["300000", "101", "103", "100", "102", "8", "8", "800", "1"],
            ["0", "100", "102", "99", "101", "5", "5", "500", "1"],
        ],
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


def test_okx_swap_rows_are_sorted_and_parsed_as_completed_candles():
    candles = load_okx_swap_klines(
        "BTCUSDT", "5m", 2, now_ms=700000,
        fetcher=lambda url: okx_payload(), sleep=lambda _: None,
    )
    assert [c.timestamp for c in candles] == [0, 300000]
    assert candles[-1].close == 102


def test_okx_nonzero_response_code_is_explicit():
    import pytest
    with pytest.raises(MarketDataError, match="OKX swap market-data response error"):
        load_okx_swap_klines(fetcher=lambda _: {"code": "51000", "msg": "bad instrument"})


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


def test_okx_is_used_when_binance_and_bybit_fail(tmp_path):
    def fail(**kwargs):
        raise MarketDataError("blocked")
    candle_list = [Candle(0, 100, 102, 99, 101, 5)]
    candles, source = load_public_klines(
        cache_dir=tmp_path, binance_loader=fail, bybit_loader=fail,
        okx_loader=lambda **kwargs: candle_list,
    )
    assert candles == candle_list
    assert source == "OKX USDT perpetual swap (fallback)"
    assert (tmp_path / "okx" / "BTCUSDT_5m.json").exists()


def test_kraken_is_used_only_when_all_futures_providers_fail(tmp_path):
    def fail(**kwargs):
        raise MarketDataError("blocked")
    candle_list = [Candle(0, 100, 102, 99, 101, 5)]
    candles, source = load_public_klines(
        cache_dir=tmp_path, binance_loader=fail, bybit_loader=fail,
        okx_loader=fail, kraken_loader=lambda **kwargs: candle_list,
    )
    assert candles == candle_list
    assert source == "Kraken spot proxy (NOT futures data)"
    assert (tmp_path / "kraken" / "BTCUSDT_5m.json").exists()


def test_empty_primary_provider_falls_back_instead_of_crashing(tmp_path):
    candle_list = [Candle(0, 100, 102, 99, 101, 5)]
    candles, source = load_public_klines(
        cache_dir=tmp_path,
        binance_loader=lambda **kwargs: [],
        bybit_loader=lambda **kwargs: candle_list,
    )
    assert candles == candle_list
    assert source == "Bybit USDT linear perpetual (fallback)"


def test_provider_with_gapped_candles_is_rejected_and_falls_back(tmp_path):
    gapped = [Candle(0, 100, 102, 99, 101, 5), Candle(600000, 101, 103, 100, 102, 5)]
    valid = [Candle(0, 100, 102, 99, 101, 5), Candle(300000, 101, 103, 100, 102, 5)]
    candles, source = load_public_klines(
        cache_dir=tmp_path,
        binance_loader=lambda **kwargs: gapped,
        bybit_loader=lambda **kwargs: valid,
    )
    assert candles == valid
    assert source == "Bybit USDT linear perpetual (fallback)"


def test_all_provider_failures_are_reported(tmp_path):
    def fail(**kwargs):
        raise MarketDataError("HTTP Error 451")
    import pytest
    with pytest.raises(MarketDataError, match="All public market-data providers failed") as exc:
        load_public_klines(cache_dir=tmp_path, binance_loader=fail,
                           bybit_loader=fail, okx_loader=fail, kraken_loader=fail)
    message = str(exc.value)
    for provider in (
        "Binance USDⓈ-M Futures",
        "Bybit USDT linear perpetual (fallback)",
        "OKX USDT perpetual swap (fallback)",
        "Kraken spot proxy (NOT futures data)",
    ):
        assert provider in message
    assert message.count("HTTP Error 451") == 4


def test_provider_fallback_diagnostics_expose_binance_and_bybit_errors(tmp_path):
    diagnostics = []
    candle_list = [Candle(0, 100, 102, 99, 101, 5)]

    def binance_fails(**kwargs):
        raise MarketDataError("HTTP Error 451: restricted location")

    def bybit_fails(**kwargs):
        raise MarketDataError("HTTP Error 403: access denied")

    def okx_fails(**kwargs):
        raise MarketDataError("HTTP Error 403: access denied")

    candles, source = load_public_klines(
        cache_dir=tmp_path,
        binance_loader=binance_fails,
        bybit_loader=bybit_fails,
        okx_loader=okx_fails,
        kraken_loader=lambda **kwargs: candle_list,
        diagnostics=diagnostics,
    )

    assert candles == candle_list
    assert source == "Kraken spot proxy (NOT futures data)"
    assert len(diagnostics) == 3
    assert "Binance USDⓈ-M Futures" in diagnostics[0]
    assert "451" in diagnostics[0]
    assert "Bybit USDT linear perpetual (fallback)" in diagnostics[1]
    assert "403" in diagnostics[1]
    assert "OKX USDT perpetual swap (fallback)" in diagnostics[2]
    assert "403" in diagnostics[2]

def test_okx_paginates_history_and_returns_contiguous_sorted_candles():
    from urllib.parse import parse_qs, urlparse

    requested_urls = []

    def fetcher(url):
        requested_urls.append(url)
        params = parse_qs(urlparse(url).query)
        if "after" not in params:
            indexes = range(599, 299, -1)
        else:
            assert int(params["after"][0]) == 300 * 300000
            indexes = [299]
        return {
            "code": "0",
            "msg": "",
            "data": [
                [str(index * 300000), "100", "102", "99", "101", "5", "5", "500", "1"]
                for index in indexes
            ],
        }

    candles = load_okx_swap_klines(
        "BTCUSDT", "5m", 301, now_ms=200_000_000,
        fetcher=fetcher, sleep=lambda _: None,
    )

    assert len(requested_urls) == 2
    assert len(candles) == 301
    assert candles[0].timestamp == 299 * 300000
    assert candles[-1].timestamp == 599 * 300000


def test_okx_rejects_duplicate_timestamps_in_provider_response():
    import pytest

    duplicate_payload = {
        "code": "0",
        "msg": "",
        "data": [
            ["300000", "101", "103", "100", "102", "8", "8", "800", "1"],
            ["300000", "101", "103", "100", "102", "8", "8", "800", "1"],
        ],
    }
    with pytest.raises(MarketDataError, match="Duplicate OKX swap candle timestamp"):
        load_okx_swap_klines(
            "BTCUSDT", "5m", 2, now_ms=700000,
            fetcher=lambda _: duplicate_payload, sleep=lambda _: None,
        )

@pytest.mark.parametrize(
    ("symbol", "interval", "expected_instrument", "expected_bar", "duration_ms"),
    [
        ("BTCUSDT", "5m", "BTC-USDT-SWAP", "5m", 300000),
        ("BTCUSDT", "15m", "BTC-USDT-SWAP", "15m", 900000),
        ("BTCUSDT", "1h", "BTC-USDT-SWAP", "1H", 3600000),
        ("ETHUSDT", "5m", "ETH-USDT-SWAP", "5m", 300000),
        ("ETHUSDT", "15m", "ETH-USDT-SWAP", "15m", 900000),
        ("ETHUSDT", "1h", "ETH-USDT-SWAP", "1H", 3600000),
    ],
)
def test_okx_supports_btc_and_eth_on_required_timeframes(
    symbol, interval, expected_instrument, expected_bar, duration_ms
):
    from urllib.parse import parse_qs, urlparse

    def fetcher(url):
        params = parse_qs(urlparse(url).query)
        assert params["instId"] == [expected_instrument]
        assert params["bar"] == [expected_bar]
        return {
            "code": "0",
            "msg": "",
            "data": [
                [str(duration_ms), "101", "103", "100", "102", "8", "8", "800", "1"],
                ["0", "100", "102", "99", "101", "5", "5", "500", "1"],
            ],
        }

    candles = load_okx_swap_klines(
        symbol, interval, 2, now_ms=10_000_000,
        fetcher=fetcher, sleep=lambda _: None,
    )
    assert [c.timestamp for c in candles] == [0, duration_ms]

