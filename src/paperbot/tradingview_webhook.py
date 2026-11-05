"""Receive-only TradingView webhook for analyst notifications."""
from __future__ import annotations

import hmac
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_BODY_BYTES = 16_384
ALLOWED_SIGNALS = {"LONG", "SHORT", "NO TRADE"}
SYMBOL_RE = re.compile(r"^[A-Z0-9._!:-]{1,40}$")


class AlertValidationError(ValueError):
    pass


class AlertAuthenticationError(PermissionError):
    pass


def normalize_alert(payload: dict[str, Any], configured_secret: str) -> dict[str, Any]:
    """Validate alert fields and never retain the shared passphrase."""
    if not configured_secret or len(configured_secret) < 24:
        raise RuntimeError("TRADINGVIEW_WEBHOOK_SECRET must be configured (24+ characters)")
    supplied = str(payload.get("passphrase", ""))
    if not hmac.compare_digest(supplied, configured_secret):
        raise AlertAuthenticationError("Invalid webhook passphrase")
    symbol = str(payload.get("ticker") or payload.get("symbol") or "").upper().strip()
    if not SYMBOL_RE.fullmatch(symbol):
        raise AlertValidationError("Missing or invalid ticker/symbol")
    signal = str(payload.get("signal") or "").upper().strip()
    if signal not in ALLOWED_SIGNALS:
        raise AlertValidationError("signal must be LONG, SHORT, or NO TRADE")
    try:
        close = float(payload.get("close", payload.get("price")))
    except (TypeError, ValueError):
        raise AlertValidationError("close/price must be numeric") from None
    if not math.isfinite(close) or close <= 0:
        raise AlertValidationError("close/price must be a positive finite number")
    return {
        "symbol": symbol,
        "signal": signal,
        "close": close,
        "interval": str(payload.get("interval") or "")[:12],
        "exchange": str(payload.get("exchange") or "")[:32],
        "event_time": str(payload.get("time") or payload.get("timenow") or "")[:64],
        "strategy": str(payload.get("strategy") or "PaperBot AI Analyst")[:80],
    }


def _database_path() -> Path:
    return Path(os.getenv("TRADINGVIEW_ALERTS_DB", "/tmp/paperbot_tradingview_alerts.sqlite3"))


def _save_alert(alert: dict[str, Any]) -> int:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS tradingview_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT NOT NULL, symbol TEXT NOT NULL, signal TEXT NOT NULL,
                close REAL NOT NULL, interval TEXT NOT NULL, exchange_name TEXT NOT NULL,
                event_time TEXT NOT NULL, strategy TEXT NOT NULL
            )"""
        )
        cursor = connection.execute(
            """INSERT INTO tradingview_alerts
               (received_at, symbol, signal, close, interval, exchange_name, event_time, strategy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(), alert["symbol"], alert["signal"],
             alert["close"], alert["interval"], alert["exchange"],
             alert["event_time"], alert["strategy"]),
        )
        connection.execute(
            "DELETE FROM tradingview_alerts WHERE id NOT IN "
            "(SELECT id FROM tradingview_alerts ORDER BY id DESC LIMIT 1000)"
        )
        return int(cursor.lastrowid)


def _read_alerts(limit: int = 100) -> list[dict[str, Any]]:
    path = _database_path()
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT id, received_at, symbol, signal, close, interval,
                      exchange_name AS exchange, event_time, strategy
               FROM tradingview_alerts ORDER BY id DESC LIMIT ?""",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    return [dict(row) for row in rows]


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "PaperBotTradingViewWebhook/1.0"

    def _json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            configured = len(os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "")) >= 24
            self._json(200 if configured else 503, {
                "status": "ok" if configured else "needs_secret",
                "webhook_configured": configured,
                "execution": "disabled",
            })
            return
        if path != "/alerts":
            self._json(404, {"error": "not_found"})
            return
        secret = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "")
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + secret if secret else ""
        if not secret or not hmac.compare_digest(supplied, expected):
            self._json(401, {"error": "unauthorized"})
            return
        self._json(200, {"alerts": _read_alerts()})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/tradingview/webhook":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid_content_length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "payload_size_invalid"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise AlertValidationError("JSON body must be an object")
            alert = normalize_alert(payload, os.getenv("TRADINGVIEW_WEBHOOK_SECRET", ""))
        except RuntimeError as exc:
            self._json(503, {"error": str(exc)})
            return
        except AlertAuthenticationError:
            self._json(401, {"error": "invalid_passphrase"})
            return
        except (UnicodeDecodeError, json.JSONDecodeError, AlertValidationError) as exc:
            self._json(400, {"error": str(exc)})
            return
        alert_id = _save_alert(alert)
        self._json(200, {"accepted": True, "alert_id": alert_id, "execution": "disabled"})

    def log_message(self, fmt: str, *args: Any) -> None:
        # Request bodies and passphrases are intentionally never logged.
        print(f"TradingView webhook: {self.address_string()} - {fmt % args}", flush=True)


def main() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), WebhookHandler)
    print(f"TradingView receive-only webhook listening on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
