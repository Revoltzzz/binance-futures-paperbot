import json

import pytest

from paperbot import okx_demo


def test_okx_demo_client_requires_demo_credentials(monkeypatch):
    for key in ("OKX_DEMO_API_KEY", "OKX_DEMO_API_SECRET", "OKX_DEMO_API_PASSPHRASE"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(okx_demo.OKXDemoError, match="demo credentials"):
        okx_demo.get_balance()


def test_okx_signed_request_forces_simulated_trading_header(monkeypatch):
    monkeypatch.setenv("OKX_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("OKX_DEMO_API_SECRET", "demo-secret")
    monkeypatch.setenv("OKX_DEMO_API_PASSPHRASE", "demo-passphrase")
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return json.dumps({"code": "0", "data": []}).encode()

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = request.headers
        seen["method"] = request.get_method()
        return Response()

    monkeypatch.setattr(okx_demo, "urlopen", fake_urlopen)
    okx_demo.get_balance()
    headers = {key.lower(): value for key, value in seen["headers"].items()}
    assert seen["url"].startswith("https://www.okx.com/api/v5/account/balance?")
    assert headers["x-simulated-trading"] == "1"
    assert seen["method"] == "GET"


def test_okx_demo_orders_are_disabled_by_default(monkeypatch):
    monkeypatch.setenv("OKX_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("OKX_DEMO_API_SECRET", "demo-secret")
    monkeypatch.setenv("OKX_DEMO_API_PASSPHRASE", "demo-passphrase")
    monkeypatch.delenv("PAPERBOT_ENABLE_OKX_DEMO_ORDERS", raising=False)
    with pytest.raises(okx_demo.OKXDemoError, match="placement is disabled"):
        okx_demo.place_demo_order(symbol="BTCUSDT", side="buy", qty="0.001")


def test_okx_demo_order_is_marked_simulated(monkeypatch):
    monkeypatch.setenv("OKX_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("OKX_DEMO_API_SECRET", "demo-secret")
    monkeypatch.setenv("OKX_DEMO_API_PASSPHRASE", "demo-passphrase")
    monkeypatch.setenv("PAPERBOT_ENABLE_OKX_DEMO_ORDERS", "true")
    seen = {}

    def fake_request(method, path, **kwargs):
        seen.update(method=method, path=path, body=kwargs["body"])
        return {"code": "0", "data": [{"ordId": "demo-order"}]}

    monkeypatch.setattr(okx_demo, "_request", fake_request)
    okx_demo.place_demo_order(symbol="ETHUSDT", side="buy", qty="0.01")
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v5/trade/order"
    assert seen["body"]["instId"] == "ETH-USDT-SWAP"


def test_okx_limit_order_requires_price(monkeypatch):
    monkeypatch.setenv("OKX_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("OKX_DEMO_API_SECRET", "demo-secret")
    monkeypatch.setenv("OKX_DEMO_API_PASSPHRASE", "demo-passphrase")
    monkeypatch.setenv("PAPERBOT_ENABLE_OKX_DEMO_ORDERS", "true")
    with pytest.raises(ValueError, match="require a non-empty decimal price"):
        okx_demo.place_demo_order(
            symbol="BTCUSDT", side="buy", qty="0.001", order_type="limit"
        )


def test_okx_public_connectivity_probe_needs_no_credentials(monkeypatch):
    for key in ("OKX_DEMO_API_KEY", "OKX_DEMO_API_SECRET", "OKX_DEMO_API_PASSPHRASE"):
        monkeypatch.delenv(key, raising=False)
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return json.dumps({"code": "0", "data": [{"ts": "1"}]}).encode()

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return Response()

    monkeypatch.setattr(okx_demo, "urlopen", fake_urlopen)
    result = okx_demo.check_public_connectivity()
    assert result["reachable"] is True
    assert seen["url"] == "https://www.okx.com/api/v5/public/time"
