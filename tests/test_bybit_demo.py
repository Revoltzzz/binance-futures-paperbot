import json
from urllib.request import Request

import pytest

from paperbot import bybit_demo


def test_request_refuses_missing_demo_credentials(monkeypatch):
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)
    with pytest.raises(bybit_demo.BybitDemoError, match="demo credentials"):
        bybit_demo.get_wallet_balance()


def test_request_uses_only_hard_coded_demo_host(monkeypatch):
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo-secret")
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return json.dumps({"retCode": 0, "result": {"list": []}}).encode()

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["headers"] = request.headers
        return Response()

    monkeypatch.setattr(bybit_demo, "urlopen", fake_urlopen)
    bybit_demo.get_positions(symbol="BTCUSDT")
    assert seen["url"].startswith("https://api-demo.bybit.com/v5/position/list?")
    assert "api.bybit.com/" not in seen["url"]
    assert seen["method"] == "GET"
    assert "x-bapi-api-key" in {key.lower(): value for key, value in seen["headers"].items()}


def test_demo_order_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo-secret")
    monkeypatch.delenv("PAPERBOT_ENABLE_BYBIT_DEMO_ORDERS", raising=False)
    with pytest.raises(bybit_demo.BybitDemoError, match="placement is disabled"):
        bybit_demo.place_demo_order(symbol="BTCUSDT", side="Buy", qty="0.001")


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
def test_demo_order_still_uses_demo_endpoint_when_enabled(monkeypatch, symbol):
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo-secret")
    monkeypatch.setenv("PAPERBOT_ENABLE_BYBIT_DEMO_ORDERS", "true")
    seen = {}

    def fake_request(method, path, **kwargs):
        seen.update(method=method, path=path, body=kwargs["body"])
        return {"retCode": 0, "result": {"orderId": "demo-order"}}

    monkeypatch.setattr(bybit_demo, "_request", fake_request)
    bybit_demo.place_demo_order(symbol=symbol, side="Buy", qty="0.001")
    assert seen["method"] == "POST"
    assert seen["path"] == "/v5/order/create"
    assert seen["body"]["category"] == "linear"
    assert seen["body"]["symbol"] == symbol
