"""Public candle providers for paper research.

Binance USDⓈ-M Futures is primary; Bybit linear futures and OKX USDT swaps are
secondary sources. If all derivatives providers are unavailable, Kraken spot is
a last-resort *spot proxy*, clearly labeled so it is never mistaken for futures.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from .binance_data import INTERVAL_MS, MarketDataError, _request_with_retries, parse_klines, _fetch_json
from .models import Candle

BYBIT_URL = "https://api.bybit.com/v5/market/kline"
BYBIT_INTERVAL = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D",
}
OKX_URL = "https://www.okx.com/api/v5/market/candles"
OKX_INTERVAL = {"5m": "5m", "15m": "15m", "1h": "1H"}
OKX_SYMBOL = {"BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP"}
KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
KRAKEN_INTERVAL = {"5m": 5, "15m": 15, "1h": 60}
KRAKEN_SYMBOL = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD"}
JsonFetcher = Callable[[str], object]


def _validate_provider_candles(candles: object, interval: str, provider: str) -> list[Candle]:
    """Reject empty, malformed, unordered, or gapped data at provider boundaries."""
    if interval not in INTERVAL_MS:
        raise MarketDataError(f"{provider} returned data for unsupported interval: {interval}")
    if not isinstance(candles, list) or not candles:
        raise MarketDataError(f"{provider} returned no candles")
    if not all(isinstance(candle, Candle) for candle in candles):
        raise MarketDataError(f"{provider} returned malformed candle objects")
    duration = INTERVAL_MS[interval]
    for previous, current in zip(candles, candles[1:]):
        difference = current.timestamp - previous.timestamp
        if difference != duration:
            kind = "missing" if difference > duration else "duplicate or out-of-order"
            raise MarketDataError(
                f"{provider} returned {kind} candle timestamps: "
                f"{previous.timestamp}, {current.timestamp}"
            )
    return candles


def load_bybit_klines(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 500, *,
    now_ms: int | None = None, fetcher: JsonFetcher | None = None,
    retries: int = 2, sleep: Callable[[float], None] | None = None) -> list[Candle]:
    """Fetch public Bybit USDT linear-perpetual candles and validate them."""
    import time
    if interval not in BYBIT_INTERVAL:
        raise ValueError(f"Bybit fallback does not support interval: {interval}")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    url = BYBIT_URL + "?" + urlencode({"category": "linear", "symbol": symbol.upper(),
                                      "interval": BYBIT_INTERVAL[interval], "limit": limit})
    payload = _request_with_retries(url, fetcher or _fetch_json, retries, sleep or time.sleep)
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        detail = payload.get("retMsg", "unexpected response") if isinstance(payload, dict) else "unexpected response"
        raise MarketDataError(f"Bybit market-data response error: {detail}")
    result = payload.get("result")
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise MarketDataError("Unexpected Bybit response: missing result.list")
    converted = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise MarketDataError("Malformed Bybit kline row")
        try:
            start = int(row[0])
            converted.append([start, row[1], row[2], row[3], row[4], row[5],
                              start + INTERVAL_MS[interval] - 1])
        except (TypeError, ValueError, OverflowError, IndexError) as exc:
            raise MarketDataError("Invalid Bybit kline row") from exc
    return parse_klines(converted, interval, now_ms=now_ms)


def load_okx_swap_klines(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 500, *,
    now_ms: int | None = None, fetcher: JsonFetcher | None = None,
    retries: int = 2, sleep: Callable[[float], None] | None = None) -> list[Candle]:
    """Fetch public OKX USDT-margined perpetual-swap candles (no API key required)."""
    import time
    symbol = symbol.upper()
    if symbol not in OKX_SYMBOL:
        raise ValueError(f"OKX swap fallback does not support {symbol}")
    if interval not in OKX_INTERVAL:
        raise ValueError(f"OKX swap fallback does not support interval: {interval}")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    request = fetcher or _fetch_json
    pause = sleep or time.sleep
    rows_by_timestamp: dict[int, list] = {}
    cursor: int | None = None
    while len(rows_by_timestamp) < limit:
        page_limit = min(300, limit - len(rows_by_timestamp))
        params = {"instId": OKX_SYMBOL[symbol], "bar": OKX_INTERVAL[interval], "limit": page_limit}
        if cursor is not None:
            params["after"] = cursor
        url = OKX_URL + "?" + urlencode(params)
        payload = _request_with_retries(url, request, retries, pause)
        if not isinstance(payload, dict) or payload.get("code") != "0":
            detail = payload.get("msg", "unexpected response") if isinstance(payload, dict) else "unexpected response"
            raise MarketDataError(f"OKX swap market-data response error: {detail}")
        rows = payload.get("data")
        if not isinstance(rows, list) or not rows:
            if rows_by_timestamp:
                break
            raise MarketDataError("Unexpected OKX response: missing data candles")
        page_timestamps: list[int] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise MarketDataError("Malformed OKX swap candle row")
            try:
                start = int(row[0])
            except (TypeError, ValueError, OverflowError, IndexError) as exc:
                raise MarketDataError("Invalid OKX swap candle timestamp") from exc
            rows_by_timestamp[start] = list(row)
            page_timestamps.append(start)
        oldest = min(page_timestamps)
        if cursor is not None and oldest >= cursor:
            raise MarketDataError("OKX pagination made no progress")
        cursor = oldest
        if len(rows) < page_limit:
            break
    converted = []
    for start in sorted(rows_by_timestamp)[-limit:]:
        row = rows_by_timestamp[start]
        converted.append([start, row[1], row[2], row[3], row[4], row[5],
                          start + INTERVAL_MS[interval] - 1])
    return parse_klines(converted, interval, now_ms=now_ms)


def load_kraken_spot_klines(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 500, *,
    now_ms: int | None = None, fetcher: JsonFetcher | None = None,
    retries: int = 2, sleep: Callable[[float], None] | None = None) -> list[Candle]:
    """Fetch Kraken spot OHLC as an explicitly non-futures fallback proxy."""
    import time
    symbol = symbol.upper()
    if symbol not in KRAKEN_SYMBOL:
        raise ValueError(f"Kraken spot fallback does not support {symbol}")
    if interval not in KRAKEN_INTERVAL:
        raise ValueError(f"Kraken spot fallback does not support interval: {interval}")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    url = KRAKEN_URL + "?" + urlencode({"pair": KRAKEN_SYMBOL[symbol],
                                        "interval": KRAKEN_INTERVAL[interval]})
    payload = _request_with_retries(url, fetcher or _fetch_json, retries, sleep or time.sleep)
    if not isinstance(payload, dict) or payload.get("error") != []:
        detail = payload.get("error", "unexpected response") if isinstance(payload, dict) else "unexpected response"
        raise MarketDataError(f"Kraken spot market-data response error: {detail}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise MarketDataError("Unexpected Kraken response: missing result")
    pair_key = next((key for key in result if key != "last"), None)
    rows = result.get(pair_key) if pair_key else None
    if not isinstance(rows, list):
        raise MarketDataError("Unexpected Kraken response: missing OHLC rows")
    converted = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 7:
            raise MarketDataError("Malformed Kraken OHLC row")
        try:
            start = int(float(row[0]) * 1000)
            converted.append([start, row[1], row[2], row[3], row[4], row[6],
                              start + INTERVAL_MS[interval] - 1])
        except (TypeError, ValueError, OverflowError, IndexError) as exc:
            raise MarketDataError("Invalid Kraken OHLC row") from exc
    return parse_klines(converted, interval, now_ms=now_ms)[-limit:]


def _write_cache(cache_dir: str | Path, provider: str, symbol: str,
                 interval: str, candles: list[Candle]) -> None:
    path = Path(cache_dir) / provider / f"{symbol.upper()}_{interval}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([[c.timestamp, c.open, c.high, c.low, c.close, c.volume]
                                for c in candles]), encoding="utf-8")


def load_public_klines(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 500,
    cache_dir: str | Path = ".cache/market", *,
    binance_loader: Callable[..., list[Candle]] | None = None,
    bybit_loader: Callable[..., list[Candle]] | None = None,
    okx_loader: Callable[..., list[Candle]] | None = None,
    kraken_loader: Callable[..., list[Candle]] | None = None,
    diagnostics: list[str] | None = None) -> tuple[list[Candle], str]:
    """Try public candle providers in order and optionally expose fallback diagnostics."""

    from .binance_data import load_binance_klines
    errors: list[str] = []
    providers = [
        ("Binance USDⓈ-M Futures", "binance",
         lambda: (binance_loader or load_binance_klines)(
             symbol=symbol, interval=interval, limit=limit, cache_dir=Path(cache_dir) / "binance")),
        ("Bybit USDT linear perpetual (fallback)", "bybit",
         lambda: (bybit_loader or load_bybit_klines)(symbol=symbol, interval=interval, limit=limit)),
        ("OKX USDT perpetual swap (fallback)", "okx",
         lambda: (okx_loader or load_okx_swap_klines)(symbol=symbol, interval=interval, limit=limit)),
        ("Kraken spot proxy (NOT futures data)", "kraken",
         lambda: (kraken_loader or load_kraken_spot_klines)(symbol=symbol, interval=interval, limit=limit)),
    ]
    for label, cache_provider, get_data in providers:
        try:
            candles = _validate_provider_candles(get_data(), interval, label)
            _write_cache(cache_dir, cache_provider, symbol, interval, candles)
            return candles, label
        except (MarketDataError, OSError, ValueError) as exc:
            detail = f"{label}: {type(exc).__name__}: {exc}"
            errors.append(detail)
            if diagnostics is not None:
                diagnostics.append(detail)
    raise MarketDataError("All public market-data providers failed. " + " | ".join(errors))
