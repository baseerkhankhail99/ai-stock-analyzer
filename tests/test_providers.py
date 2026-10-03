import pandas as pd
import pytest
import requests
from helpers import ensure, ensure_equal

import dashboard
from services import market_data, market_service, providers
from services.providers import base, chain, crypto, equities

COINGECKO_MARKETS = [
    {
        "id": "bitcoin",
        "current_price": 65000.5,
        "price_change_24h": 500.0,
        "price_change_percentage_24h": 0.77,
        "last_updated": "2026-10-03T08:00:00Z",
        "sparkline_in_7d": {"price": [float(i) for i in range(168)]},
    },
    {"id": "unknown-coin", "current_price": 1},
]
COINGECKO_CHART = {
    "prices": [[1700000000000, 100.0], [1700086400000, 110.0], [1700172800000, 105.0]],
    "total_volumes": [[1700000000000, 5.0], [1700086400000, 6.0]],
}
STOOQ_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-09-30,10,11,9,10.5,1000\n"
    "2026-10-01,10.5,12,10,11.5,2000\n"
)


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class StubProvider(base.Provider):
    def __init__(self, name, behaviour):
        self.name = name
        self.label = name
        self.behaviour = behaviour
        self.calls = 0

    def get_history(self, symbol, days=730):
        self.calls += 1
        return self.behaviour()


def _frame():
    index = pd.date_range("2026-01-01", periods=3, name="Date")
    return pd.DataFrame(
        {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": [1.0, 2.0, 3.0], "Volume": 0.0},
        index=index,
    )


def _raise(exc):
    def behaviour():
        raise exc

    return behaviour


def _use_chain(monkeypatch, *stubs):
    monkeypatch.setattr(chain, "history_chain", lambda symbol: list(stubs))


def test_chain_falls_through_on_error_and_empty_results(monkeypatch):
    raising = StubProvider("a", _raise(base.ProviderError("boom")))
    empty = StubProvider("b", lambda: chain.empty_frame())
    working = StubProvider("c", _frame)
    _use_chain(monkeypatch, raising, empty, working)

    frame, source = chain.get_history("AAPL")

    ensure_equal(source, "c")
    ensure_equal(len(frame), 3)
    ensure_equal((raising.calls, empty.calls, working.calls), (1, 1, 1))


def test_chain_returns_empty_when_every_provider_fails(monkeypatch):
    _use_chain(monkeypatch, StubProvider("a", _raise(RuntimeError("x"))))
    frame, source = chain.get_history("AAPL")
    ensure(frame.empty)
    ensure_equal(source, "")


def test_circuit_breaker_skips_rate_limited_provider(monkeypatch):
    limited = StubProvider("a", _raise(base.RateLimited("HTTP 429")))
    working = StubProvider("b", _frame)
    _use_chain(monkeypatch, limited, working)

    chain.get_history("AAPL")
    chain.get_history("AAPL")

    ensure_equal(limited.calls, 1)
    ensure_equal(working.calls, 2)
    ensure(base.breaker.is_open("a"))
    ensure_equal(chain.attempt(limited, "get_history", "AAPL")[1]["skipped"], True)


def test_circuit_breaker_expires():
    now = [0.0]
    local = base.CircuitBreaker(cooldown=300, clock=lambda: now[0])
    local.trip("x")
    ensure(local.is_open("x"))
    now[0] = 301
    ensure(not local.is_open("x"))


def test_http_get_maps_status_codes_and_retries_only_5xx(monkeypatch):
    calls = []
    responses = [FakeResponse(503), FakeResponse(200, {"ok": 1})]

    def fake_get(url, **kwargs):
        calls.append(kwargs["timeout"])
        return responses.pop(0)

    monkeypatch.setattr(base.requests, "get", fake_get)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    ensure_equal(base.http_get("http://x").json(), {"ok": 1})
    ensure_equal(calls, [base.HTTP_TIMEOUT_SECONDS] * 2)
    ensure(base.HTTP_TIMEOUT_SECONDS <= 8)

    monkeypatch.setattr(base.requests, "get", lambda *a, **k: FakeResponse(429))
    with pytest.raises(base.RateLimited):
        base.http_get("http://x")

    def secret_failure(*args, **kwargs):
        raise requests.ConnectionError("https://x?apikey=SECRET")

    monkeypatch.setattr(base.requests, "get", secret_failure)
    with pytest.raises(base.ProviderError) as info:
        base.http_get("http://x")
    ensure("SECRET" not in str(info.value))


def test_coingecko_parsing():
    quotes = crypto.parse_markets(COINGECKO_MARKETS)
    ensure_equal(list(quotes), ["BTC-USD"])
    btc = quotes["BTC-USD"]
    ensure_equal(btc["price"], 65000.5)
    ensure_equal(btc["change_pct"], 0.77)
    ensure_equal(btc["sparkline"], [71.0, 95.0, 119.0, 143.0, 167.0])
    ensure_equal(len(btc["sparkline"]), 5)

    frame = crypto.parse_market_chart(COINGECKO_CHART)
    ensure_equal(list(frame.columns), base.OHLCV_COLUMNS)
    ensure_equal(frame["Close"].tolist(), [100.0, 110.0, 105.0])
    ensure_equal(frame["Open"].tolist(), [100.0, 100.0, 110.0])
    ensure_equal(frame["Volume"].tolist(), [5.0, 6.0, 0.0])
    with pytest.raises(base.NoData):
        crypto.parse_market_chart({"prices": []})


def test_coingecko_overview_is_one_call_with_optional_key(monkeypatch):
    seen = []

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.append((url, params, headers))
        return FakeResponse(200, COINGECKO_MARKETS)

    monkeypatch.setattr(base.requests, "get", fake_get)
    monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
    quotes, _ = chain.attempt(
        chain.coingecko, "get_markets", ["BTC-USD", "ETH-USD", "DOGE-USD"]
    )
    ensure_equal(list(quotes), ["BTC-USD"])
    ensure_equal(len(seen), 1)
    ensure_equal(seen[0][1]["ids"], "bitcoin,ethereum,dogecoin")
    ensure("x-cg-demo-api-key" not in seen[0][2])

    monkeypatch.setenv("COINGECKO_API_KEY", "demo")
    chain.coingecko.get_markets(["BTC-USD"])
    ensure_equal(seen[-1][2]["x-cg-demo-api-key"], "demo")


def test_stooq_parsing_and_symbol_map():
    frame = equities.parse_stooq_csv(STOOQ_CSV)
    ensure_equal(frame["Close"].tolist(), [10.5, 11.5])
    ensure_equal(frame["Volume"].tolist(), [1000.0, 2000.0])
    for bad in ("No data", "", "<!DOCTYPE html><html></html>", "Date,Open\n"):
        with pytest.raises(base.NoData):
            equities.parse_stooq_csv(bad)
    ensure_equal(equities.stooq_symbol("AAPL"), "aapl.us")
    ensure_equal(equities.stooq_symbol("^GSPC"), "^spx")
    ensure_equal(equities.stooq_symbol("GC=F"), "xauusd")
    ensure_equal(equities.stooq_symbol("BZ=F"), "lco.f")
    ensure_equal(equities.stooq_symbol("BTC-USD"), None)


def test_finnhub_quote_and_premium_candles(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")

    def quote_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/quote"):
            ensure_equal(headers["X-Finnhub-Token"], "k")
            return FakeResponse(200, {"c": 190.5, "d": 1.5, "dp": 0.8, "t": 1700000000})
        return FakeResponse(403)

    monkeypatch.setattr(base.requests, "get", quote_get)
    quote, source = chain.get_quote("AAPL", overview=True)
    ensure_equal((quote["price"], source), (190.5, "finnhub"))
    # candles are premium: falls back (here: no other provider answers) without
    # tripping the quote endpoint
    with pytest.raises(base.NoData):
        chain.finnhub.get_history("AAPL")
    ensure(not base.breaker.is_open("finnhub"))
    monkeypatch.delenv("FINNHUB_API_KEY")
    ensure(not chain.finnhub.available())


def test_yahoo_chart_and_alpha_vantage_parsing():
    chart = {
        "chart": {
            "result": [
                {
                    "timestamp": [1700000000, 1700086400],
                    "indicators": {
                        "quote": [
                            {
                                "open": [1, 2],
                                "high": [2, 3],
                                "low": [0.5, 1],
                                "close": [1.5, None],
                                "volume": [10, 20],
                            }
                        ]
                    },
                }
            ]
        }
    }
    ensure_equal(len(equities.parse_yahoo_chart(chart)), 1)
    with pytest.raises(base.NoData):
        equities.parse_yahoo_chart({"chart": {"result": None}})
    series = {
        "Time Series (Daily)": {
            "2026-10-01": {
                "1. open": "1",
                "2. high": "2",
                "3. low": "0.5",
                "4. close": "1.5",
                "5. volume": "10",
            }
        }
    }
    ensure_equal(equities.parse_alpha_vantage_daily(series)["Close"].tolist(), [1.5])
    with pytest.raises(base.RateLimited):
        equities.parse_alpha_vantage_daily({"Information": "limit"})


def test_overview_serves_stale_snapshot_when_all_providers_fail(client, monkeypatch):
    def quotes(symbols, *args, **kwargs):
        return {
            s: ({"price": 10.0, "change": 1.0, "change_pct": 1.0, "as_of": "d"}, "x")
            for s in symbols
        }

    monkeypatch.setattr(providers, "fetch_quotes", quotes)
    fresh = client.get("/api/market/overview").get_json()
    ensure_equal(fresh["stale"], False)

    app_cache = client.application.extensions["app_cache"]
    app_cache._memory._data.pop(market_data.OVERVIEW_KEY)
    monkeypatch.setattr(providers, "fetch_quotes", lambda symbols, *a, **k: {})
    stale = client.get("/api/market/overview").get_json()

    ensure_equal(stale["stale"], True)
    ensure_equal(stale["as_of"], fresh["timestamp"])
    ensure_equal(stale["stocks"][0]["price"], 10.0)
    ensure("live source unavailable" in market_data.age_message(stale["as_of"]))
    ensure_equal(stale["sources_status"]["used"], {})


def test_partial_failure_reuses_last_known_asset_as_stale(client, monkeypatch):
    def quotes(skip):
        def fetch(symbols, *args, **kwargs):
            return {
                s: ({"price": 10.0, "change": 0.0, "change_pct": 0.0}, "x")
                for s in symbols
                if s not in skip
            }

        return fetch

    monkeypatch.setattr(providers, "fetch_quotes", quotes(()))
    client.get("/api/market/overview")
    client.application.extensions["app_cache"]._memory._data.pop(
        market_data.OVERVIEW_KEY
    )
    monkeypatch.setattr(providers, "fetch_quotes", quotes(("AAPL",)))
    body = client.get("/api/market/overview").get_json()
    apple = next(a for a in body["stocks"] if a["symbol"] == "AAPL")
    ensure_equal((apple["price"], apple["stale"]), (10.0, True))
    ensure_equal(body["stale"], False)


def test_history_serves_stale_copy_when_providers_fail(client, monkeypatch):
    monkeypatch.setattr(providers, "get_history", lambda s, d=730: (_frame(), "stooq"))
    first = client.get("/api/market/history/AAPL?period=1M").get_json()
    ensure_equal(first["stale"], False)
    ensure_equal(first["has_volume"], False)

    cache = client.application.extensions["app_cache"]
    cache._memory._data.pop("hist:AAPL")
    monkeypatch.setattr(
        providers, "get_history", lambda s, d=730: (chain.empty_frame(), "")
    )
    second = client.get("/api/market/history/AAPL?period=1M").get_json()
    ensure_equal(second["stale"], True)
    ensure("live source unavailable" in second["message"])


def test_invalid_symbols_are_rejected_before_any_provider_call(client, monkeypatch):
    monkeypatch.setattr(
        providers,
        "get_history",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no provider call")),
    )
    ensure_equal(client.get("/api/market/history/A%20B%24%25").status_code, 400)
    ensure_equal(client.get("/api/market/analysis/" + "X" * 40).status_code, 400)
    ensure_equal(
        client.get("/api/market/compare?symbols=AAPL,bad$sym").status_code, 400
    )
    ensure_equal(client.get("/api/stocks/bad$sym/forecast").status_code, 400)


def test_momentum_returns_200_unavailable_instead_of_502(client):
    response = client.get("/api/market/momentum")
    ensure_equal(response.status_code, 200)
    body = response.get_json()
    ensure_equal(body["available"], False)
    ensure(body["message"])


def test_momentum_uses_overview_without_downloads(client, monkeypatch):
    def quotes(symbols, *args, **kwargs):
        return {
            s: ({"price": 1.0, "change": 1.0, "change_pct": 1.0}, "x") for s in symbols
        }

    monkeypatch.setattr(providers, "fetch_quotes", quotes)
    monkeypatch.setattr(
        providers,
        "get_history",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no download")),
    )
    client.get("/api/market/overview")
    body = client.get("/api/market/momentum").get_json()
    ensure_equal(body["available"], True)
    ensure_equal(body["label"], "Bullish")


def test_health_data_reports_each_provider_without_secrets(client, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "super-secret-key")
    monkeypatch.setattr(
        base.requests, "get", lambda *a, **k: FakeResponse(200, None, "No data")
    )
    body = client.get("/api/health/data").get_json()
    ensure(set(body["checks"]) == {"stock", "crypto", "commodity", "index"})
    stock = {p["provider"]: p for p in body["checks"]["stock"]["providers"]}
    ensure("stooq" in stock and "yahoo" in stock)
    ensure_equal(stock["alphavantage"]["skipped"], True)
    ensure(all("latency_ms" in p and "ok" in p for p in stock.values()))
    ensure("super-secret-key" not in str(body))


def test_settings_callbacks_update_stores():
    saved = dashboard.save_settings(120_000, "1Y", 60, ["sma", "bb"])
    ensure_equal(
        saved,
        {
            "refresh_ms": 120_000,
            "chart_range": "1Y",
            "forecast_days": 60,
            "overlays": ["sma", "bb"],
        },
    )
    ensure_equal(dashboard.apply_refresh_setting(saved), (120_000, False))
    ensure_equal(dashboard.apply_refresh_setting({"refresh_ms": 0})[1], True)
    ensure_equal(dashboard.overlays_for_chart(saved), ["sma_20", "sma_50", "bb"])
    ensure_equal(dashboard.overlays_for_chart({"overlays": []}), [])
    ensure_equal(
        dashboard.load_settings_controls(1, saved), (120_000, "1Y", 60, ["sma", "bb"])
    )
    # invalid or missing stored values fall back to the defaults
    ensure_equal(
        dashboard.normalize_settings({"refresh_ms": 5}), dashboard.DEFAULT_SETTINGS
    )
    ensure_equal(dashboard.normalize_settings(None), dashboard.DEFAULT_SETTINGS)
    ensure_equal(dashboard.chart_tab(saved).children[0].value, "1Y")


def test_dashboard_footer_and_stale_banner():
    overview = {
        "as_of": "2026-10-03T12:11:00Z",
        "sources_status": {"used": {"coingecko": 7, "stooq": 14}},
    }
    ensure_equal(
        dashboard.data_sources_line(overview),
        "Prices: CoinGecko, Stooq • Updated 12:11 UTC",
    )
    stale = {"stale": True, "as_of": "2026-10-03T12:11:00Z", "stocks": []}
    ensure("live source unavailable" in str(dashboard.build_home_grid(stale)))


def test_forecast_and_analysis_do_not_import_yfinance_directly():
    import inspect

    from services import forecast_engine, market_analysis

    for module in (forecast_engine, market_analysis, market_service):
        ensure("import yfinance" not in inspect.getsource(module))
