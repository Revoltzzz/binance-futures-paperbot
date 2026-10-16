"""Strictly simulated-trading OKX REST client.

OKX uses its REST host for both production and Demo Trading. Every request in
this module carries x-simulated-trading: 1, credentials are read only from
OKX_DEMO_* variables, and order placement has an additional disabled-by-default
environment guard. This module is not automatically called by the worker.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEMO_BASE_URL = "https://www.okx.com"


class OKXDemoError(RuntimeError):
    """Raised for OKX Demo Trading configuration, transport, or response failures."""


def _credentials() -> tuple[str, str, str]:
    key = os.getenv("OKX_DEMO_API_KEY", "").strip()
    secret = os.getenv("OKX_DEMO_API_SECRET", "").strip()
    passphrase = os.getenv("OKX_DEMO_API_PASSPHRASE", "").strip()
    if not key or not secret or not passphrase:
        raise OKXDemoError(
            "OKX demo credentials are not configured. Set OKX_DEMO_API_KEY, "
            "OKX_DEMO_API_SECRET, and OKX_DEMO_API_PASSPHRASE using Demo Trading keys only."
        )
    return key, secret, passphrase


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _request(method: str, path: str, *, params: dict[str, Any] | None = None,
             body: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
    """Sign an OKX REST request and force its simulated-trading header."""
    method = method.upper()
    if method not in {"GET", "POST"}:
        raise OKXDemoError("Unsupported HTTP method")
    if not path.startswith("/api/v5/") or "://" in path or "\n" in path:
        raise OKXDemoError("Only relative OKX V5 API paths are allowed")
    key, secret, passphrase = _credentials()
    query = urlencode(sorted((params or {}).items()))
    request_path = path + (("?" + query) if method == "GET" and query else "")
    body_text = json.dumps(body or {}, separators=(",", ":")) if method == "POST" else ""
    timestamp = _timestamp()
    prehash = timestamp + method + request_path + body_text
    signature = base64.b64encode(
        hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "OK-ACCESS-KEY": key,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "x-simulated-trading": "1",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = Request(
        DEMO_BASE_URL + request_path,
        data=body_text.encode() if method == "POST" else None,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(1_000_000)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise OKXDemoError(f"OKX demo API transport failed: {exc}") from exc
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OKXDemoError("OKX demo API returned invalid JSON") from exc
    if not isinstance(result, dict) or result.get("code") != "0":
        detail = result.get("msg", "unexpected response") if isinstance(result, dict) else "unexpected response"
        raise OKXDemoError(f"OKX demo API rejected request: {detail}")
    return result


def check_public_connectivity(timeout: float = 5.0) -> dict[str, Any]:
    """Check the OKX public API host without credentials or order permissions."""
    request = Request(
        DEMO_BASE_URL + "/api/v5/public/time",
        headers={"Accept": "application/json", "x-simulated-trading": "1"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(100_000)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise OKXDemoError(f"OKX API connectivity failed: {exc}") from exc
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OKXDemoError("OKX API returned invalid JSON") from exc
    if not isinstance(result, dict) or result.get("code") != "0":
        detail = result.get("msg", "unexpected response") if isinstance(result, dict) else "unexpected response"
        raise OKXDemoError(f"OKX API connectivity response error: {detail}")
    return {"reachable": True, "endpoint": DEMO_BASE_URL, "authenticated": False}


def get_balance() -> dict[str, Any]:
    """Read the authenticated demo trading account balance."""
    return _request("GET", "/api/v5/account/balance", params={"ccy": "USDT"})


def get_positions(inst_type: str = "SWAP") -> dict[str, Any]:
    """Read positions from the demo trading account."""
    if inst_type not in {"SWAP", "FUTURES"}:
        raise ValueError("inst_type must be SWAP or FUTURES")
    return _request("GET", "/api/v5/account/positions", params={"instType": inst_type})


def place_demo_order(*, symbol: str, side: str, qty: str, order_type: str = "market",
                     price: str | None = None, td_mode: str = "isolated",
                     reduce_only: bool = False) -> dict[str, Any]:
    """Place a demo-only USDT perpetual order; disabled unless explicitly enabled."""
    if os.getenv("PAPERBOT_ENABLE_OKX_DEMO_ORDERS", "").lower() != "true":
        raise OKXDemoError(
            "OKX demo order placement is disabled. Set PAPERBOT_ENABLE_OKX_DEMO_ORDERS=true "
            "only after validating Demo Trading credentials."
        )
    normalized_symbol = symbol.upper()
    instruments = {"BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP"}
    if normalized_symbol not in instruments:
        raise ValueError("symbol must be BTCUSDT or ETHUSDT")
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if order_type not in {"market", "limit"}:
        raise ValueError("order_type must be market or limit")
    if td_mode not in {"isolated", "cross"}:
        raise ValueError("td_mode must be isolated or cross")
    if not isinstance(qty, str) or not qty or len(qty) > 32:
        raise ValueError("qty must be a non-empty decimal string")
    if order_type == "limit" and (not isinstance(price, str) or not price or len(price) > 32):
        raise ValueError("limit orders require a non-empty decimal price string")
    if order_type == "market" and price is not None:
        raise ValueError("price must be omitted for market orders")
    body: dict[str, Any] = {
        "instId": instruments[normalized_symbol],
        "tdMode": td_mode,
        "side": side,
        "ordType": order_type,
        "sz": qty,
        "reduceOnly": bool(reduce_only),
    }
    if order_type == "limit":
        body["px"] = price
    return _request("POST", "/api/v5/trade/order", body=body)
