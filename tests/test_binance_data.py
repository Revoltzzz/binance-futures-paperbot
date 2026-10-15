import json
from urllib.error import URLError

import pytest

from paperbot.binance_data import MarketDataError, load_binance_klines, parse_klines


def row(open_time, close_time, open_="100", high="102", low="99", close="101", volume="5"):
    return [open_time, open_, high, low, close, volume, close_time, "0", 1, "0", "0", "0"]


def test_parser_sorts_and_discards_incomplete_candle():
    payload = [row(300000, 599999), row(0, 299999), row(600000, 899999)]
    candles = parse_klines(payload, "5m", now_ms=700000)
    assert [c.timestamp for c in candles] == [0, 300000]


def test_parser_rejects_inexact_close_timestamp():
    with pytest.raises(MarketDataError, match="close timestamp does not match interval"):
        parse_klines([row(0, 299998)], "5m", now_ms=400000)


def test_parser_rejects_non_finite_prices():
    with pytest.raises(MarketDataError, match="Invalid kline"):
        parse_klines([row(0, 299999, close="nan")], "5m", now_ms=400000)


def test_parser_rejects_duplicate_timestamps():
    with pytest.raises(MarketDataError, match="Duplicate"):
        parse_klines([row(0, 299999), row(0, 299999)], "5m", now_ms=900000)


def test_parser_detects_missing_candles():
    with pytest.raises(MarketDataError, match="Missing candle"):
        parse_klines([row(0, 299999), row(600000, 899999)], "5m", now_ms=1000000)


def test_parser_rejects_malformed_and_invalid_ohlc():
    with pytest.raises(MarketDataError, match="Malformed"):
        parse_klines([[0, "100"]], "5m", now_ms=1000000)
    with pytest.raises(MarketDataError, match="Invalid kline"):
        parse_klines([row(0, 299999, high="98")], "5m", now_ms=1000000)


def test_fetcher_retries_then_succeeds_and_writes_cache(tmp_path):
    calls = []
    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            raise URLError("temporary failure")
        return [row(0, 299999), row(300000, 599999)]

    candles = load_binance_klines("BTCUSDT", "5m", 2, tmp_path, now_ms=700000,
                                  fetcher=fetch, sleep=lambda _: None)
    assert len(calls) == 2
    assert len(candles) == 2
    cached = tmp_path / "BTCUSDT_5m.json"
    assert cached.exists()
    assert json.loads(cached.read_text())[0][0] == 0
    assert "symbol=BTCUSDT" in calls[0]


def test_fetcher_failure_is_explicit_and_does_not_use_stale_cache(tmp_path):
    cache = tmp_path / "BTCUSDT_5m.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("[]")
    with pytest.raises(MarketDataError, match="failed after 1 attempts"):
        load_binance_klines(cache_dir=tmp_path, retries=0, now_ms=900000,
                            fetcher=lambda _: (_ for _ in ()).throw(URLError("offline")))
    assert cache.read_text() == "[]"


@pytest.mark.parametrize("kwargs", [
    {"symbol": "BTC/USDT"}, {"interval": "13m"}, {"limit": 0}, {"limit": 1501},
])
def test_request_parameters_are_validated(kwargs):
    with pytest.raises(ValueError):
        load_binance_klines(fetcher=lambda _: [], **kwargs)


from email.message import Message
from urllib.error import HTTPError


def _http_error(code, headers=None):
    return HTTPError("https://fapi.binance.com/fapi/v1/klines", code,
                     "mock HTTP error", headers or Message(), None)


def test_permanent_binance_http_451_fails_fast_without_retry_or_sleep(tmp_path):
    calls = []
    sleeps = []

    def fetch(_):
        calls.append(1)
        raise _http_error(451)

    with pytest.raises(MarketDataError, match="failed after 1 attempt"):
        load_binance_klines(cache_dir=tmp_path, retries=3, fetcher=fetch,
                            sleep=sleeps.append)
    assert len(calls) == 1
    assert sleeps == []


def test_rate_limit_honors_short_retry_after_then_succeeds(tmp_path):
    calls = []
    sleeps = []
    headers = Message()
    headers["Retry-After"] = "2"

    def fetch(_):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, headers)
        return [row(0, 299999), row(300000, 599999)]

    candles = load_binance_klines(cache_dir=tmp_path, retries=1, now_ms=700000,
                                  fetcher=fetch, sleep=sleeps.append)
    assert len(candles) == 2
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_long_rate_limit_delay_defers_to_next_worker_poll(tmp_path):
    calls = []
    sleeps = []
    headers = Message()
    headers["Retry-After"] = "120"

    def fetch(_):
        calls.append(1)
        raise _http_error(429, headers)

    with pytest.raises(MarketDataError, match="failed after 1 attempt"):
        load_binance_klines(cache_dir=tmp_path, retries=3, fetcher=fetch,
                            sleep=sleeps.append)
    assert len(calls) == 1
    assert sleeps == []
