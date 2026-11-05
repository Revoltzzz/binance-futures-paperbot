from __future__ import annotations

import pytest

from paperbot.tradingview_webhook import (
    AlertAuthenticationError,
    AlertValidationError,
    normalize_alert,
)

SECRET = "local-test-secret-with-at-least-24-characters"


def test_normalize_alert_accepts_long_without_returning_passphrase():
    result = normalize_alert({
        "passphrase": SECRET, "ticker": "SOLUSDT.P", "exchange": "OKX",
        "interval": "5", "close": "180.25", "signal": "LONG",
        "time": "2026-11-06T10:00:00Z",
    }, SECRET)
    assert result["symbol"] == "SOLUSDT.P"
    assert result["signal"] == "LONG"
    assert result["close"] == 180.25
    assert "passphrase" not in result


def test_normalize_alert_rejects_wrong_secret():
    with pytest.raises(AlertAuthenticationError):
        normalize_alert({"passphrase": "wrong", "ticker": "BTCUSDT.P",
                         "close": 100000, "signal": "LONG"}, SECRET)


@pytest.mark.parametrize("signal", ["BUY", "SELL", "FLAT", ""])
def test_normalize_alert_rejects_unexpected_signal(signal):
    with pytest.raises(AlertValidationError):
        normalize_alert({"passphrase": SECRET, "ticker": "BTCUSDT.P",
                         "close": 100000, "signal": signal}, SECRET)


@pytest.mark.parametrize("close", [0, -1, "nan", "not-a-price", None])
def test_normalize_alert_rejects_invalid_prices(close):
    with pytest.raises(AlertValidationError):
        normalize_alert({"passphrase": SECRET, "ticker": "BTCUSDT.P",
                         "close": close, "signal": "LONG"}, SECRET)


def test_normalize_alert_requires_configured_secret():
    with pytest.raises(RuntimeError):
        normalize_alert({"passphrase": SECRET, "ticker": "BTCUSDT.P",
                         "close": 100000, "signal": "LONG"}, "")
