"""Public candle providers for paper research.

Binance USDⓈ-M Futures is primary; Bybit linear futures is secondary. If both
are blocked, Kraken spot candles are a last-resort *spot proxy*, clearly labeled
so they are never mistaken for futures data.
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
KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"
KRAKEN_INTERVAL = {"5m": 5, "15m": 15, "1h": 60}
KRAKEN_SYMBOL = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD"}
JsonFetcher = Callable[[str], object]


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
    kraken_loader: Callable[..., list[Candle]] | None = None) -> tuple[list[Candle], str]:
    """Try Binance Futures, Bybit Futures, then clearly labeled Kraken spot proxy."""
    from .binance_data import load_binance_klines
    errors: list[str] = []
    providers = [
        ("Binance USDⓈ-M Futures", "binance",
         lambda: (binance_loader or load_binance_klines)(
             symbol=symbol, interval=interval, limit=limit, cache_dir=Path(cache_dir) / "binance")),
        ("Bybit USDT linear perpetual (fallback)", "bybit",
         lambda: (bybit_loader or load_bybit_klines)(symbol=symbol, interval=interval, limit=limit)),
        ("Kraken spot proxy (NOT futures data)", "kraken",
         lambda: (kraken_loader or load_kraken_spot_klines)(symbol=symbol, interval=interval, limit=limit)),
    ]
    for label, cache_provider, get_data in providers:
        try:
            candles = get_data()
            _write_cache(cache_dir, cache_provider, symbol, interval, candles)
            return candles, label
        except (MarketDataError, OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
    raise MarketDataError("All public market-data providers failed. " + " | ".join(errors))
