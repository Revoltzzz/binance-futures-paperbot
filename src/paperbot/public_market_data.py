"""Public candle providers for paper research.

Binance USDⓈ-M Futures is primary. Bybit public linear-perpetual candles are a
fallback when Binance is regionally unavailable. The provider is returned so
the UI never presents fallback data as Binance data.
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
JsonFetcher = Callable[[str], object]


def load_bybit_klines(
    symbol: str = "BTCUSDT",
    interval: str = "5m",
    limit: int = 500,
    *,
    now_ms: int | None = None,
    fetcher: JsonFetcher | None = None,
    retries: int = 2,
    sleep: Callable[[float], None] | None = None,
) -> list[Candle]:
    """Fetch public Bybit USDT linear-perpetual candles and validate them."""
    import time

    if interval not in BYBIT_INTERVAL:
        raise ValueError(f"Bybit fallback does not support interval: {interval}")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    url = BYBIT_URL + "?" + urlencode({
        "category": "linear", "symbol": symbol.upper(),
        "interval": BYBIT_INTERVAL[interval], "limit": limit,
    })
    sleeper = sleep or time.sleep
    payload = _request_with_retries(url, fetcher or _fetch_json, retries, sleeper)
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
            close_time = start + INTERVAL_MS[interval] - 1
            converted.append([start, row[1], row[2], row[3], row[4], row[5], close_time])
        except (TypeError, ValueError, OverflowError, IndexError) as exc:
            raise MarketDataError("Invalid Bybit kline row") from exc
    return parse_klines(converted, interval, now_ms=now_ms)


def load_public_klines(
    symbol: str = "BTCUSDT",
    interval: str = "5m",
    limit: int = 500,
    cache_dir: str | Path = ".cache/market",
    *,
    binance_loader: Callable[..., list[Candle]] | None = None,
    bybit_loader: Callable[..., list[Candle]] | None = None,
) -> tuple[list[Candle], str]:
    """Try Binance Futures first, then Bybit linear futures; return candles and source."""
    from .binance_data import load_binance_klines

    errors: list[str] = []
    try:
        loader = binance_loader or load_binance_klines
        candles = loader(symbol=symbol, interval=interval, limit=limit,
                         cache_dir=Path(cache_dir) / "binance")
        return candles, "Binance USDⓈ-M Futures"
    except (MarketDataError, OSError, ValueError) as exc:
        errors.append(f"Binance: {exc}")
    try:
        loader = bybit_loader or load_bybit_klines
        candles = loader(symbol=symbol, interval=interval, limit=limit)
        cache_path = Path(cache_dir) / "bybit" / f"{symbol.upper()}_{interval}.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps([
            [c.timestamp, c.open, c.high, c.low, c.close, c.volume] for c in candles
        ]), encoding="utf-8")
        return candles, "Bybit USDT linear perpetual (fallback)"
    except (MarketDataError, OSError, ValueError) as exc:
        errors.append(f"Bybit fallback: {exc}")
    raise MarketDataError("All public market-data providers failed. " + " | ".join(errors))
