import numpy as np
import pandas as pd
from helpers import ensure, ensure_equal

import dashboard
from routes import api as api_module
from services import forecast_engine, market_data
from services.forecast_engine import StockForecastEngine


def _batch_frame(symbols, days=260, skip=()):
    index = pd.date_range("2024-01-01", periods=days, freq="D")
    rng = np.random.default_rng(1)
    columns = {}
    for n, symbol in enumerate(symbols):
        if symbol in skip:
            continue
        close = pd.Series(
            100 + n + np.cumsum(rng.normal(0.2, 1.0, days)), index=index
        ).abs()
        for field, series in (
            ("Open", close),
            ("High", close * 1.01),
            ("Low", close * 0.99),
            ("Close", close),
            ("Volume", pd.Series(1000.0, index=index)),
        ):
            columns[(symbol, field)] = series
    return pd.DataFrame(columns)


def _fake_download(skip=(), calls=None):
    def download(symbols, period, interval):
        if calls is not None:
            calls.append((list(symbols), period, interval))
        return _batch_frame(symbols, skip=skip)

    return download


def test_overview_survives_one_failing_asset_and_caches(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        market_data, "download_prices", _fake_download(skip=("SI=F",), calls=calls)
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
    ensure(gold["price"] is not None)
    ensure("delayed_warning" in body and "timestamp" in body)
    ensure_equal(second.get_json(), body)
    # a single batched request for every symbol, cached afterwards
    ensure_equal(len(calls), 1)
    ensure_equal(calls[0][1:], ("5d", "1d"))
    ensure_equal(len(calls[0][0]), len(market_data.all_symbols()))


def test_overview_total_download_failure_returns_placeholders(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(market_data, "download_prices", boom)
    response = client.get("/api/market/overview")
    ensure_equal(response.status_code, 200)
    ensure(all(a["price"] is None for a in response.get_json()["stocks"]))


def test_compare_uses_single_batch_fetch(client, monkeypatch):
    calls = []
    monkeypatch.setattr(market_data, "download_prices", _fake_download(calls=calls))
    response = client.get("/api/market/compare?symbols=AAPL,MSFT,BTC-USD")
    ensure_equal(response.status_code, 200)
    body = response.get_json()
    ensure_equal(body["symbols"], ["AAPL", "MSFT", "BTC-USD"])
    ensure_equal(body["beta"]["AAPL"], 1.0)
    ensure_equal(len(body["correlation"]["matrix"]), 3)
    ensure_equal(body["normalized"]["AAPL"][0], 100.0)
    ensure_equal(len(calls), 1)
    ensure_equal(client.get("/api/market/compare?symbols=AAPL").status_code, 400)


def test_forecast_on_demand_is_recursive_cached_and_has_insight(client, monkeypatch):
    monkeypatch.setattr(market_data, "download_prices", _fake_download())
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
        market_data,
        "download_prices",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cache not used")),
    )
    ensure_equal(client.get("/api/stocks/AAPL/forecast?days=10").status_code, 200)


def test_forecast_reports_failure_when_no_data(client, monkeypatch):
    monkeypatch.setattr(market_data, "download_prices", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(
        market_data.yf,
        "Ticker",
        lambda symbol: type("T", (), {"history": lambda self, **k: pd.DataFrame()})(),
    )
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
    monkeypatch.setattr(market_data, "download_prices", _fake_download())
    history = client.get("/api/market/history/AAPL?period=1M")
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
    monkeypatch.setattr(market_data, "download_prices", _fake_download())
    monkeypatch.setattr(forecast_engine, "Prophet", None)
    with app.app_context():
        result = StockForecastEngine().generate_all_forecasts("AAPL", 7)
    ensure_equal(len(result["arima"]), 7)
    ensure(result["metrics"]["arima"] is not None)
    ensure(result["metrics"]["ensemble"] is not None)
    ensure_equal(forecast_engine.mape([0, 0], [1, 1]), None)
