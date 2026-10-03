import time

import numpy as np
import pandas as pd
from helpers import ensure, ensure_equal

from services.ensemble import ensemble_forecast, forecast_path


def _series_frame(days=420, drift=0.0015, sigma=0.012, seed=7):
    index = pd.date_range("2023-01-01", periods=days, freq="D")
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, sigma, days)
    close = 100 * np.exp(np.cumsum(returns))
    volume = pd.Series(
        1_000_000 * (1 + rng.normal(0.0, 0.08, days)).clip(min=0.2), index=index
    )
    market = 200 * np.exp(np.cumsum(rng.normal(drift * 0.75, sigma * 0.8, days)))
    return (
        pd.Series(close, index=index, name="close"),
        pd.Series(volume, index=index, name="volume"),
        pd.Series(market, index=index, name="market_close"),
    )


def _strip_cached_at(result):
    clone = dict(result)
    clone.pop("cached_at", None)
    return clone


def test_ensemble_forecast_returns_unavailable_for_short_history():
    short = pd.Series(
        np.linspace(100.0, 120.0, 80), index=pd.date_range("2024-01-01", periods=80)
    )
    result = ensemble_forecast(short)
    ensure_equal(result["available"], False)
    ensure("120" in result["message"])


def test_ensemble_forecast_is_deterministic_and_fast_enough():
    close, volume, market = _series_frame(days=520, seed=11)
    started = time.perf_counter()
    first = ensemble_forecast(close, volume=volume, market_close=market)
    elapsed = time.perf_counter() - started
    second = ensemble_forecast(close, volume=volume, market_close=market)

    ensure_equal(first["available"], True)
    ensure(elapsed < 20.0, f"Forecasting took too long: {elapsed:.2f}s")
    ensure_equal(_strip_cached_at(first), _strip_cached_at(second))
    ensure_equal(sorted(first["horizons"].keys()), [1, 7, 30, 90])


def test_horizon_outputs_have_ordered_intervals_and_bounded_metrics():
    close, volume, market = _series_frame(days=460, seed=3)
    result = ensemble_forecast(close, volume=volume, market_close=market)
    ensure_equal(result["available"], True)

    for horizon, payload in result["horizons"].items():
        ensure_equal(payload["horizon"], horizon)
        ensure(
            payload["lower_pct"] <= payload["median_return_pct"] <= payload["upper_pct"]
        )
        ensure(
            payload["lower_price"] <= payload["median_price"] <= payload["upper_price"]
        )
        ensure(0.0 <= payload["directional_accuracy"] <= 100.0)
        ensure(0.0 <= payload["agreement"] <= 1.0)
        ensure(len(payload["backtest"]) <= 60)
        ensure("baseline" in payload["models"])
        ensure("ridge" in payload["models"])
        ensure("gbr" in payload["models"])

    path = forecast_path(result, 30)
    ensure_equal(path["available"], True)
    ensure_equal(len(path["dates"]), 31)
    ensure_equal(len(path["median"]), 31)
    ensure(
        all(
            path["lower"][i] <= path["median"][i] <= path["upper"][i]
            for i in range(len(path["dates"]))
        )
    )


def test_random_walk_and_nan_inputs_do_not_raise():
    close, volume, market = _series_frame(days=360, drift=0.0, sigma=0.02, seed=21)
    close.iloc[10:14] = np.nan
    close.iloc[200] = np.nan
    volume.iloc[30] = np.nan
    market.iloc[35:37] = np.nan

    result = ensemble_forecast(close, volume=volume, market_close=market)
    ensure(result["available"] in (True, False))
    if result["available"]:
        ensure_equal(result["last_date"], close.dropna().index[-1].date().isoformat())
        ensure(result["horizons"])
        for payload in result["horizons"].values():
            ensure(payload["mape"] is None or payload["mape"] >= 0.0)


def test_forecast_path_handles_missing_horizon_safely():
    close, _, _ = _series_frame(days=240, seed=9)
    result = ensemble_forecast(close, horizons=(7,))
    path = forecast_path(result, 30)
    ensure_equal(path["available"], False)
