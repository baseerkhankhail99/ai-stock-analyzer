import sys

import pandas as pd
from helpers import ensure, ensure_equal

from config import TestingConfig
from routes import api as api_module
from services import forecast_engine
from services.forecast_engine import StockForecastEngine


def test_health_endpoint(client):
    response = client.get("/api/health")
    ensure_equal(response.status_code, 200)
    ensure_equal(response.get_json()["status"], "healthy")


def test_root_redirects_to_dashboard_and_dashboard_is_served(client):
    response = client.get("/")
    ensure_equal(response.status_code, 302)
    ensure(response.headers["Location"].endswith("/dashboard/"))
    ensure_equal(client.get("/dashboard/").status_code, 200)


def test_price_endpoint_uses_mocked_fetcher_and_caches(client, monkeypatch):
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        return {"symbol": symbol, "price": 123.45}

    monkeypatch.setattr(api_module.data_fetcher, "fetch_real_time_price", fake_fetch)

    first = client.get("/api/stocks/CACHEME/price")
    second = client.get("/api/stocks/CACHEME/price")

    ensure_equal(first.status_code, 200)
    ensure_equal(first.get_json()["price"], 123.45)
    ensure_equal(second.get_json()["price"], 123.45)
    ensure_equal(calls, ["CACHEME"])


def test_price_endpoint_returns_404_when_no_data(client, monkeypatch):
    monkeypatch.setattr(
        api_module.data_fetcher, "fetch_real_time_price", lambda symbol: {}
    )
    ensure_equal(client.get("/api/stocks/NOPE/price").status_code, 404)


def test_input_validation_returns_400(client):
    ensure_equal(client.get("/api/stocks/compare").status_code, 400)
    ensure_equal(client.get("/api/stocks/compare?symbol1=AAPL").status_code, 400)
    ensure_equal(client.post("/api/stocks/sync", data="not json").status_code, 400)
    ensure_equal(client.post("/api/stocks/sync", json={}).status_code, 400)
    ensure_equal(
        client.post(
            "/api/stocks/compare-all", json={"symbols": ["A"], "days": "x"}
        ).status_code,
        400,
    )


def test_unknown_route_returns_json_404(client):
    response = client.get("/api/does-not-exist")
    ensure_equal(response.status_code, 404)
    ensure_equal(response.get_json(), {"error": "Not found"})


def test_app_uses_memory_cache_without_redis(app):
    with app.app_context():
        ensure_equal(api_module.get_cache().backend, "memory")


def test_app_starts_with_unreachable_redis(monkeypatch):
    from app import create_app

    monkeypatch.setattr(TestingConfig, "REDIS_URL", "redis://127.0.0.1:1/0")
    flask_app = create_app("testing")
    with flask_app.app_context():
        ensure_equal(api_module.get_cache().backend, "memory")
    response = flask_app.test_client().get("/api/health")
    ensure_equal(response.status_code, 200)


def _fake_history(days=200):
    index = pd.date_range("2023-01-01", periods=days, freq="D")
    close = pd.Series(range(100, 100 + days), index=index, dtype=float)
    return pd.DataFrame(
        {
            "close": close,
            "open": close,
            "high": close,
            "low": close,
            "volume": 1000,
        }
    )


def test_generate_all_forecasts_without_tensorflow_or_prophet(app, monkeypatch):
    monkeypatch.setitem(sys.modules, "tensorflow", None)
    monkeypatch.setattr(forecast_engine, "Prophet", None)
    engine = StockForecastEngine()
    monkeypatch.setattr(
        engine, "get_historical_data", lambda symbol, days=365: _fake_history()
    )

    with app.app_context():
        result = engine.generate_all_forecasts("TEST", 5)

    ensure_equal(result["lstm"], [])
    ensure_equal(result["prophet"], [])
    ensure_equal(len(result["ensemble"]), 5)
