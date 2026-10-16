"""Public Binance USDⓈ-M Futures kline downloader.

Only unauthenticated market-data GET requests are made here. This module cannot
place orders. Network access is injectable so the whole downloader can be tested
offline with deterministic fixtures.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Candle

BASE_URL = "https://fapi.binance.com/fapi/v1/klines"
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000,
    "1w": 604_800_000,
}
JsonFetcher = Callable[[str], object]


class MarketDataError(RuntimeError):
    """Raised when public market data cannot be safely retrieved or validated."""


def _fetch_json(url: str, timeout: float = 10.0) -> object:
    request = Request(url, headers={"User-Agent": "binance-futures-paperbot/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_with_retries(url: str, fetcher: JsonFetcher, retries: int,
                          sleep: Callable[[float], None]) -> object:
    """Retry transient failures, but fail fast on permanent HTTP errors.

    Binance rate limits (429) may provide Retry-After in seconds. We honor
    short delays; for long delays, fail this cycle and let the worker's normal
    polling schedule retry rather than blocking a worker for minutes.
    """
    last_error: Exception | None = None
    attempts = 0
    for attempt in range(retries + 1):
        try:
            return fetcher(url)
        except HTTPError as exc:
            detail = ""
            try:
                # Keep enough of the provider response to diagnose policy or
                # gateway blocks, without allowing an unbounded error payload
                # into worker logs.
                raw_body = exc.read(1024)
                if raw_body:
                    detail = raw_body.decode("utf-8", errors="replace").strip()
                    detail = " ".join(detail.split())[:500]
            except (OSError, ValueError):
                detail = ""
            last_error = RuntimeError(
                f"HTTP Error {exc.code} {exc.reason}"
                + (f": {detail}" if detail else "")
            )
            attempts = attempt + 1
            # Invalid requests, access restrictions and IP bans will not be
            # fixed by retrying the same request. Never attempt to bypass them.
            if exc.code not in {408, 425, 429} and not 500 <= exc.code <= 599:
                break
            if attempt >= retries:
                break
            delay = min(0.5 * (2 ** attempt), 4.0)
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    try:
                        requested_delay = max(0.0, float(retry_after))
                    except (TypeError, ValueError):
                        requested_delay = delay
                    if requested_delay > 60:
                        break
                    delay = requested_delay
            sleep(delay)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            attempts = attempt + 1
            if attempt < retries:
                sleep(min(0.5 * (2 ** attempt), 4.0))
    raise MarketDataError(
        f"Public market-data request failed after {attempts or retries + 1} attempts: {last_error}"
    ) from last_error


def parse_klines(payload: object, interval: str, now_ms: int | None = None) -> list[Candle]:
    """Parse, validate, sort and remove the still-open candle from a Binance response."""
    if interval not in INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")
    if not isinstance(payload, list):
        raise MarketDataError("Unexpected Binance response: expected a list of klines")
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    duration = INTERVAL_MS[interval]
    parsed: list[tuple[int, int, Candle]] = []
    seen: set[int] = set()
    for row in payload:
        if not isinstance(row, (list, tuple)) or len(row) < 7:
            raise MarketDataError("Malformed kline row: expected at least 7 fields")
        try:
            open_time = int(row[0])
            open_, high, low, close, volume = map(float, (row[1], row[2], row[3], row[4], row[5]))
            close_time = int(row[6])
            candle = Candle(open_time, open_, high, low, close, volume)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MarketDataError(f"Invalid kline row at timestamp {row[0]!r}") from exc
        if open_time in seen:
            raise MarketDataError(f"Duplicate candle timestamp: {open_time}")
        seen.add(open_time)
        if open_time < 0 or close_time < open_time:
            raise MarketDataError(f"Invalid candle timestamps at {open_time}")
        # Fixed-duration intraday/daily bars are Unix-epoch aligned. Binance's
        # calendar-anchored 3d and 1w bars use exchange-specific boundaries.
        if interval not in {"3d", "1w"} and open_time % duration != 0:
            raise MarketDataError(
                f"Candle open timestamp is not aligned to interval {interval} at {open_time}"
            )
        expected_close_time = open_time + duration - 1
        if close_time != expected_close_time:
            raise MarketDataError(
                f"Candle close timestamp does not match interval {interval} at {open_time}"
            )
        if close_time < now:
            parsed.append((open_time, close_time, candle))

    parsed.sort(key=lambda item: item[0])
    candles = [item[2] for item in parsed]
    for previous, current in zip(candles, candles[1:]):
        difference = current.timestamp - previous.timestamp
        if difference != duration:
            kind = "missing" if difference > duration else "out-of-order/overlapping"
            raise MarketDataError(f"{kind.capitalize()} candle interval between {previous.timestamp} and {current.timestamp}")
    if not candles:
        raise MarketDataError("No completed candles returned by Binance")
    return candles


def load_binance_klines(
    symbol: str = "BTCUSDT",
    interval: str = "5m",
    limit: int = 500,
    cache_dir: str | Path = ".cache/binance",
    retries: int = 2,
    *,
    now_ms: int | None = None,
    fetcher: JsonFetcher | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Candle]:
    """Download completed public USDⓈ-M Futures klines and persist a local cache."""
    symbol = symbol.upper()
    if not re.fullmatch(r"[A-Z0-9]{5,30}", symbol):
        raise ValueError("symbol must contain only letters and digits (e.g. BTCUSDT)")
    if interval not in INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1500:
        raise ValueError("limit must be an integer between 1 and 1500")
    if not isinstance(retries, int) or retries < 0 or retries > 5:
        raise ValueError("retries must be between 0 and 5")

    url = BASE_URL + "?" + urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    payload = _request_with_retries(url, fetcher or _fetch_json, retries, sleep)
    candles = parse_klines(payload, interval, now_ms=now_ms)

    cache_path = Path(cache_dir) / f"{symbol}_{interval}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_payload = [
        [c.timestamp, str(c.open), str(c.high), str(c.low), str(c.close), str(c.volume),
         c.timestamp + INTERVAL_MS[interval] - 1]
        for c in candles
    ]
    temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(cache_payload), encoding="utf-8")
    temp_path.replace(cache_path)
    return candles
