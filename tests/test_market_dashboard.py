import numpy as np
import pandas as pd
from helpers import ensure, ensure_equal

import dashboard
from routes import api as api_module
from services import forecast_engine, market_data, providers
from services.forecast_engine import StockForecastEngine


def _history_frame(symbol, days=260):
    index = pd.date_range("2024-01-01", periods=days, freq="D", name="Date")
    rng = np.random.default_rng(sum(map(ord, symbol)))
    close = pd.Series(100 + np.cumsum(rng.normal(0.2, 1.0, days)), index=index).abs()
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1000.0,
        }
    )


def _fake_history(skip=(), calls=None):
    def get_history(symbol, days=730):
        if calls is not None:
            calls.append(symbol)
        if symbol in skip:
            return pd.DataFrame(), ""
        return _history_frame(symbol), "stooq"

    return get_history


def _fake_quotes(skip=(), calls=None):
    def fetch_quotes(symbols, *args, **kwargs):
        if calls is not None:
            calls.append(list(symbols))
        return {
            s: (
                {
                    "price": 100.0 + n,
                    "change": 1.0,
                    "change_pct": 1.0,
                    "sparkline": [1, 2, 3, 4, 5],
                    "as_of": "2024-01-01",
                },
                "stooq",
            )
            for n, s in enumerate(symbols)
            if s not in skip
        }

    return fetch_quotes


def test_overview_survives_one_failing_asset_and_caches(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        providers, "fetch_quotes", _fake_quotes(skip=("SI=F",), calls=calls)
    )

    first = client.get("/api/market/overview")
    second = client.get("/api/market/overview")

    ensure_equal(first.status_code, 200)
    body = first.get_json()
    ensure_equal(len(body["commodities"]), 6)
    ensure_equal(len(body["cryptocurrencies"]), 7)
    ensure_equal(len(body["stocks"]), 7)
    ensure_equal(len(body["indices"]), 3)
    silver = body["commodities"][1]
    ensure_equal(silver["symbol"], "SI=F")
    ensure_equal(silver["price"], None)
    ensure(silver["error"])
    gold = body["commodities"][0]
    ensure_equal(len(gold["sparkline"]), 5)
    ensure_equal(gold["source"], "stooq")
    ensure(gold["price"] is not None and gold["as_of"])
    ensure("delayed_warning" in body and "timestamp" in body)
    ensure_equal(body["stale"], False)
    ensure_equal(body["sources_status"]["used"], {"stooq": 22})
    ensure_equal(body["sources_status"]["failed"], ["SI=F"])
    ensure_equal(second.get_json(), body)
    # one concurrent fetch for every symbol, cached afterwards
    ensure_equal(len(calls), 1)
    ensure_equal(len(calls[0]), len(market_data.all_symbols()))


def test_overview_total_failure_returns_placeholders(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("providers down")

    monkeypatch.setattr(providers, "fetch_quotes", boom)
    response = client.get("/api/market/overview")
    ensure_equal(response.status_code, 200)
    ensure(all(a["price"] is None for a in response.get_json()["stocks"]))


def test_compare_uses_single_batch_fetch(client, monkeypatch):
    calls = []
    monkeypatch.setattr(providers, "get_history", _fake_history(calls=calls))
    response = client.get("/api/market/compare?symbols=AAPL,MSFT,BTC-USD")
    ensure_equal(response.status_code, 200)
    body = response.get_json()
    ensure_equal(body["symbols"], ["AAPL", "MSFT", "BTC-USD"])
    ensure_equal(body["beta"]["AAPL"], 1.0)
    ensure_equal(len(body["correlation"]["matrix"]), 3)
    ensure_equal(body["normalized"]["AAPL"][0], 100.0)
    ensure_equal(sorted(calls), ["AAPL", "BTC-USD", "MSFT"])
    ensure_equal(client.get("/api/market/compare?symbols=AAPL").status_code, 400)


def test_forecast_on_demand_is_recursive_cached_and_has_insight(client, monkeypatch):
    monkeypatch.setattr(providers, "get_history", _fake_history())
    monkeypatch.setattr(forecast_engine, "Prophet", None)

    first = client.get("/api/stocks/AAPL/forecast?days=10")
    ensure_equal(first.status_code, 200)
    body = first.get_json()
    ensure_equal(len(body["ensemble"]), 10)
    ensure(body["ensemble"][0]["mape"] is not None)
    ensure("Not financial advice" in body["insight"])
    prices = [p["predicted_price"] for p in body["ensemble"]]
    ensure(len(set(prices)) > 1, "recursive forecast must not repeat one value")

    monkeypatch.setattr(
        providers,
        "get_history",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cache not used")),
    )
    ensure_equal(client.get("/api/stocks/AAPL/forecast?days=10").status_code, 200)


def test_forecast_reports_failure_when_no_data(client, monkeypatch):
    monkeypatch.setattr(providers, "get_history", _fake_history(skip=("NOPE",)))
    response = client.get("/api/stocks/NOPE/forecast?days=7")
    ensure_equal(response.status_code, 502)
    ensure_equal(response.get_json()["error"], "Unable to compute forecast")


def test_recursive_forecast_feeds_predictions_back():
    index = pd.date_range("2024-01-01", periods=200, freq="D")
    close = pd.Series(
        100 + np.cumsum(np.random.default_rng(3).normal(0, 1, 200)), index=index
    )
    models = forecast_engine.fit_return_models(close)
    frame = forecast_engine.recursive_forecast(models, close, 15)
    ensure_equal(len(frame), 15)
    ensure(frame["pred"].nunique() > 1)
    ensure((frame["lower"] < frame["pred"]).all())
    ensure((frame["upper"] > frame["pred"]).all())
    ensure(isinstance(StockForecastEngine().lookback_days, int))


def test_history_and_analysis_endpoints(client, monkeypatch):
    monkeypatch.setattr(providers, "get_history", _fake_history())
    history = client.get("/api/market/history/AAPL?period=1M")
    ensure_equal(history.get_json()["source"], "stooq")
    ensure_equal(history.status_code, 200)
    row = history.get_json()["data"][-1]
    ensure("rsi" in row and "sma_20" in row)
    ensure_equal(client.get("/api/market/history/AAPL?period=9Y").status_code, 400)

    analysis = client.get("/api/market/analysis/AAPL").get_json()
    ensure(analysis["signal"]["signal"] in ("BUY", "HOLD", "SELL"))
    ensure("sharpe_ratio" in analysis["analytics"]["metrics"])


def test_home_cards_show_dash_for_failed_asset_and_green_red_colors():
    failed = market_data._failed_asset("SI=F", "Silver")
    card = dashboard.asset_card(failed, "Last updated —")
    ensure("—" in str(card))
    ensure_equal(dashboard.change_class(1.2), "up")
    ensure_equal(dashboard.change_class(-1.2), "down")
    ensure_equal(dashboard.change_class(0), "flat")
    ensure_equal(dashboard.parse_asset("?asset=btc-usd"), "BTC-USD")
    ensure_equal(dashboard.parse_asset(""), None)


def test_home_page_has_disclaimers(client):
    html = client.get("/dashboard/").get_data(as_text=True)
    ensure("AI Market Analyzer" in html)
    layout = str(dashboard.app.layout)
    ensure("Not financial advice" in layout)


def test_arima_forecast_and_metrics_in_generate_all(app, monkeypatch):
    monkeypatch.setattr(providers, "get_history", _fake_history())
    monkeypatch.setattr(forecast_engine, "Prophet", None)
    with app.app_context():
        result = StockForecastEngine().generate_all_forecasts("AAPL", 7)
    ensure_equal(len(result["arima"]), 7)
    ensure(result["metrics"]["arima"] is not None)
    ensure(result["metrics"]["ensemble"] is not None)
    ensure_equal(forecast_engine.mape([0, 0], [1, 1]), None)
