"""Strictly demo-only Bybit V5 REST client.

This module cannot target Bybit production or testnet endpoints. It is isolated
from the paper worker and is not called automatically. Credentials must be
created in Bybit Demo Trading mode and supplied via environment variables.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEMO_BASE_URL = "https://api-demo.bybit.com"
RECV_WINDOW_MS = "5000"


class BybitDemoError(RuntimeError):
    """Raised for demo API configuration, transport, or response failures."""


def _credentials() -> tuple[str, str]:
    key = os.getenv("BYBIT_DEMO_API_KEY", "").strip()
    secret = os.getenv("BYBIT_DEMO_API_SECRET", "").strip()
    if not key or not secret:
        raise BybitDemoError(
            "Bybit demo credentials are not configured. Set BYBIT_DEMO_API_KEY "
            "and BYBIT_DEMO_API_SECRET using Demo Trading API keys only."
        )
    return key, secret


def _signature(secret: str, timestamp: str, api_key: str, recv_window: str,
               payload: str) -> str:
    pre_sign = timestamp + api_key + recv_window + payload
    return hmac.new(secret.encode(), pre_sign.encode(), hashlib.sha256).hexdigest()


def _request(method: str, path: str, *, params: dict[str, Any] | None = None,
             body: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
    """Send a signed request exclusively to Bybit's Demo Trading API host."""
    method = method.upper()
    if method not in {"GET", "POST"}:
        raise BybitDemoError("Unsupported HTTP method")
    if not path.startswith("/v5/") or "://" in path:
        raise BybitDemoError("Only relative Bybit V5 demo API paths are allowed")
    key, secret = _credentials()
    query = urlencode(sorted((params or {}).items()))
    payload = query if method == "GET" else json.dumps(body or {}, separators=(",", ":"))
    timestamp = str(int(time.time() * 1000))
    signature = _signature(secret, timestamp, key, RECV_WINDOW_MS, payload)
    url = DEMO_BASE_URL + path + (("?" + query) if method == "GET" and query else "")
    headers = {
        "X-BAPI-API-KEY": key,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW_MS,
        "Content-Type": "application/json",
    }
    data = payload.encode() if method == "POST" else None
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(1_000_000)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise BybitDemoError(f"Bybit demo API transport failed: {exc}") from exc
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BybitDemoError("Bybit demo API returned invalid JSON") from exc
    if not isinstance(result, dict) or result.get("retCode") != 0:
        detail = result.get("retMsg", "unexpected response") if isinstance(result, dict) else "unexpected response"
        raise BybitDemoError(f"Bybit demo API rejected request: {detail}")
    return result


def get_wallet_balance(account_type: str = "UNIFIED") -> dict[str, Any]:
    """Read demo wallet balance; this method never places orders."""
    if account_type not in {"UNIFIED", "CONTRACT"}:
        raise ValueError("account_type must be UNIFIED or CONTRACT")
    return _request("GET", "/v5/account/wallet-balance", params={"accountType": account_type})


def get_positions(category: str = "linear", symbol: str | None = None) -> dict[str, Any]:
    """Read demo derivatives positions; this method never places orders."""
    if category not in {"linear", "inverse"}:
        raise ValueError("category must be linear or inverse")
    params: dict[str, Any] = {"category": category}
    if symbol:
        normalized = symbol.upper()
        if not normalized.endswith("USDT") or not normalized[:-4].isalnum():
            raise ValueError("symbol must be a USDT perpetual symbol such as BTCUSDT")
        params["symbol"] = normalized
    return _request("GET", "/v5/position/list", params=params)


def place_demo_order(*, symbol: str, side: str, qty: str, order_type: str = "Market",
                     category: str = "linear", reduce_only: bool = False) -> dict[str, Any]:
    """Place a simulated order on the hard-coded Bybit Demo Trading endpoint only.

    This is not wired into the worker by default. It requires the explicit
    PAPERBOT_ENABLE_BYBIT_DEMO_ORDERS=true guard as a second safety check.
    """
    if os.getenv("PAPERBOT_ENABLE_BYBIT_DEMO_ORDERS", "").lower() != "true":
        raise BybitDemoError(
            "Demo order placement is disabled. Set PAPERBOT_ENABLE_BYBIT_DEMO_ORDERS=true "
            "only after configuring Bybit Demo Trading credentials."
        )
    normalized_symbol = symbol.upper()
    if not normalized_symbol.endswith("USDT") or not normalized_symbol[:-4].isalnum():
        raise ValueError("symbol must be a USDT perpetual symbol such as BTCUSDT")
    if side not in {"Buy", "Sell"}:
        raise ValueError("side must be Buy or Sell")
    if order_type not in {"Market", "Limit"}:
        raise ValueError("order_type must be Market or Limit")
    if category != "linear":
        raise ValueError("only USDT linear demo orders are supported")
    if not isinstance(qty, str) or not qty or len(qty) > 32:
        raise ValueError("qty must be a non-empty decimal string")
    body = {
        "category": "linear",
        "symbol": normalized_symbol,
        "side": side,
        "orderType": order_type,
        "qty": qty,
        "timeInForce": "IOC" if order_type == "Market" else "GTC",
        "reduceOnly": bool(reduce_only),
    }
    return _request("POST", "/v5/order/create", body=body)
