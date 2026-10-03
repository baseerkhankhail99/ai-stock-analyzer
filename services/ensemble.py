from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.holtwinters import ExponentialSmoothing

MIN_HISTORY = 120
DEFAULT_HORIZONS = (1, 7, 30, 90)
MODEL_ORDER = ("baseline", "ridge", "gbr", "ets")
RANDOM_STATE = 42


def _unavailable(message: str) -> Dict[str, object]:
    return {"available": False, "message": message}


def _to_datetime_index(series: pd.Series, fallback_name: str) -> pd.Series:
    clean = pd.Series(series).copy()
    if clean.empty:
        clean.index = pd.DatetimeIndex([], name=fallback_name)
        return clean
    if not isinstance(clean.index, pd.DatetimeIndex):
        clean.index = pd.date_range(
            end=pd.Timestamp.utcnow().normalize(),
            periods=len(clean),
            freq="D",
            name=fallback_name,
        )
    clean.index = pd.DatetimeIndex(clean.index)
    clean = clean[~clean.index.duplicated(keep="last")]
    return clean.sort_index()


def _sanitize_series(
    series: Optional[pd.Series],
    fallback_name: str,
    index: Optional[pd.DatetimeIndex] = None,
) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float, index=index if index is not None else None)
    clean = _to_datetime_index(series, fallback_name)
    clean = pd.to_numeric(clean, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if index is not None:
        clean = clean.reindex(index).ffill().bfill()
    else:
        clean = clean.ffill().bfill()
    return clean.dropna().astype(float)


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100 - (100 / (1 + rs))


def _build_features(
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    market_close: Optional[pd.Series] = None,
) -> pd.DataFrame:
    log_close = np.log(close.clip(lower=1e-9))
    log_ret_1 = log_close.diff()
    sma_20 = close.rolling(20, min_periods=20).mean()
    sma_50 = close.rolling(50, min_periods=50).mean()
    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema_12 - ema_26
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    macd_hist = (macd - macd_signal) / close.replace(0.0, np.nan)
    rsi = _rsi(close)
    bb_std = close.rolling(20, min_periods=20).std(ddof=0)
    bb_upper = sma_20 + (2 * bb_std)
    bb_lower = sma_20 - (2 * bb_std)
    pct_b = (close - bb_lower) / (bb_upper - bb_lower).replace(0.0, np.nan)
    atr_like_pct = close.pct_change().abs().rolling(14, min_periods=14).mean() * 100.0
    vol_20 = log_ret_1.rolling(20, min_periods=20).std(ddof=0) * np.sqrt(252)
    vol_60 = log_ret_1.rolling(60, min_periods=60).std(ddof=0) * np.sqrt(252)
    rolling_high = close.rolling(252, min_periods=20).max()
    rolling_low = close.rolling(252, min_periods=20).min()

    features = pd.DataFrame(
        {
            "ret_1": close.pct_change(1),
            "ret_5": close.pct_change(5),
            "ret_10": close.pct_change(10),
            "ret_20": close.pct_change(20),
            "dist_sma20": close / sma_20 - 1.0,
            "dist_sma50": close / sma_50 - 1.0,
            "ema_cross": np.sign(ema_12 - ema_26),
            "sma_cross": np.sign(sma_20 - sma_50),
            "rsi": rsi / 100.0,
            "macd_hist": macd_hist,
            "bollinger_pct_b": pct_b,
            "atr_like_pct": atr_like_pct / 100.0,
            "vol_20": vol_20,
            "vol_ratio": vol_20 / vol_60.replace(0.0, np.nan),
            "dist_252_high": close / rolling_high - 1.0,
            "dist_252_low": close / rolling_low - 1.0,
        },
        index=close.index,
    )

    if volume is not None and not volume.empty:
        aligned_volume = _sanitize_series(volume, "volume", close.index)
        features["volume_ratio"] = (
            aligned_volume / aligned_volume.rolling(20, min_periods=20).mean() - 1.0
        )
        features["volume_trend"] = aligned_volume.pct_change(5)

    if market_close is not None and not market_close.empty:
        aligned_market = _sanitize_series(market_close, "market_close", close.index)
        market_log_ret = np.log(aligned_market.clip(lower=1e-9)).diff()
        cov_60 = log_ret_1.rolling(60, min_periods=60).cov(market_log_ret)
        var_60 = market_log_ret.rolling(60, min_periods=60).var(ddof=0)
        features["beta_60"] = cov_60 / var_60.replace(0.0, np.nan)
        features["corr_60"] = log_ret_1.rolling(60, min_periods=60).corr(market_log_ret)
        features["market_ret_5"] = aligned_market.pct_change(5)

    return features.replace([np.inf, -np.inf], np.nan)


def _fold_starts(length: int, horizon: int, max_folds: int = 5) -> List[int]:
    test_size = max(8, min(12, horizon if horizon > 0 else 8))
    min_train = max(80, int(length * 0.45))
    earliest = min_train + horizon
    latest = length - test_size
    if latest <= earliest:
        return []
    count = min(max_folds, max(2, 1 + (latest - earliest) // test_size))
    starts = np.linspace(earliest, latest, num=count, dtype=int).tolist()
    unique = []
    for start in starts:
        if not unique or start > unique[-1]:
            unique.append(int(start))
    return unique


def _baseline_predict(
    train: pd.DataFrame, test: pd.DataFrame, horizon: int
) -> np.ndarray:
    drift = (
        float(train["target"].tail(min(60, len(train))).mean()) if len(train) else 0.0
    )
    if np.isnan(drift):
        drift = 0.0
    pred = (
        drift
        - (0.35 * test["dist_sma20"].fillna(0.0).to_numpy())
        - (0.15 * test["dist_sma50"].fillna(0.0).to_numpy())
        - (0.10 * (test["rsi"].fillna(0.5).to_numpy() - 0.5))
    )
    return np.clip(pred, -0.35, 0.35)


def _fit_ml_models(x_train: pd.DataFrame, y_train: pd.Series) -> Dict[str, object]:
    models: Dict[str, object] = {}
    models["ridge"] = make_pipeline(
        StandardScaler(), Ridge(alpha=1.0, random_state=RANDOM_STATE)
    )
    models["ridge"].fit(x_train, y_train)
    gbr = GradientBoostingRegressor(
        n_estimators=40,
        max_depth=2,
        learning_rate=0.05,
        subsample=1.0,
        random_state=RANDOM_STATE,
    )
    gbr.fit(x_train, y_train)
    models["gbr"] = gbr
    return models


def _ets_path(log_close: pd.Series, train_pos: int, steps: int) -> Optional[np.ndarray]:
    if steps <= 0:
        return None
    start = max(0, train_pos - 299)
    history = log_close.iloc[start : train_pos + 1]
    if len(history) < 30:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = ExponentialSmoothing(
                history, trend="add", seasonal=None, initialization_method="estimated"
            )
            fitted = model.fit(optimized=True, use_brute=False)
            path = np.asarray(fitted.forecast(steps), dtype=float)
    except Exception:
        return None
    if np.isnan(path).any():
        return None
    return path


def _ets_fold_predictions(
    log_close: pd.Series, train_pos: int, positions: pd.Series, horizon: int
) -> Optional[np.ndarray]:
    max_steps = int(positions.iloc[-1] - train_pos + horizon)
    path = _ets_path(log_close, train_pos, max_steps)
    if path is None:
        return None
    predictions: List[float] = []
    for pos in positions.astype(int).tolist():
        offset = int(pos - train_pos)
        if offset <= 0 or (offset + horizon - 1) >= len(path):
            return None
        start_log = path[offset - 1]
        end_log = path[offset + horizon - 1]
        predictions.append(float(end_log - start_log))
    return np.asarray(predictions, dtype=float)


def _prepare_horizon_data(
    features: pd.DataFrame, close: pd.Series, horizon: int
) -> pd.DataFrame:
    target = np.log(close.shift(-horizon) / close).rename("target")
    data = features.copy()
    data["position"] = np.arange(len(data), dtype=int)
    data = data.join(target)
    return data.dropna()


def _weights_from_errors(
    actual_pct: np.ndarray, model_predictions_pct: Dict[str, np.ndarray]
) -> Dict[str, float]:
    errors = {}
    for name, preds in model_predictions_pct.items():
        if len(preds) != len(actual_pct) or len(preds) == 0:
            continue
        mae = float(np.mean(np.abs(actual_pct - preds)))
        if np.isnan(mae):
            continue
        errors[name] = max(mae, 0.1)
    if not errors:
        return {}
    inv = {name: 1.0 / value for name, value in errors.items()}
    total = sum(inv.values())
    return {name: value / total for name, value in inv.items()}


def _agreement(models_pct: Dict[str, float], weights: Dict[str, float]) -> float:
    active = {
        name: value
        for name, value in models_pct.items()
        if name in weights and value is not None
    }
    if not active:
        return 0.0
    total_weight = sum(weights[name] for name in active)
    if total_weight <= 0:
        return 0.0
    pos_weight = sum(weights[name] for name, value in active.items() if value > 0)
    neg_weight = sum(weights[name] for name, value in active.items() if value < 0)
    sign_share = max(pos_weight, neg_weight) / total_weight
    magnitudes = np.asarray([abs(value) for value in active.values()], dtype=float)
    avg_magnitude = float(np.mean(magnitudes)) if len(magnitudes) else 0.0
    dispersion = (
        float(np.std(magnitudes) / avg_magnitude) if avg_magnitude > 1e-9 else 0.0
    )
    penalty = 1.0 - min(0.5, dispersion * 0.5)
    return round(float(np.clip(sign_share * penalty, 0.0, 1.0)), 4)


def _final_model_predictions(
    data: pd.DataFrame,
    features: pd.DataFrame,
    close: pd.Series,
    horizon: int,
) -> Dict[str, float]:
    if data.empty:
        return {}
    feature_columns = [column for column in features.columns if column in data.columns]
    x_train = data[feature_columns]
    y_train = data["target"]
    x_last = features.iloc[[-1]].ffill().fillna(0.0)
    predictions: Dict[str, float] = {
        "baseline": float(_baseline_predict(data, x_last, horizon)[0])
    }
    try:
        ml_models = _fit_ml_models(x_train, y_train)
        predictions["ridge"] = float(ml_models["ridge"].predict(x_last)[0])
        predictions["gbr"] = float(ml_models["gbr"].predict(x_last)[0])
    except Exception:
        predictions.pop("ridge", None)
        predictions.pop("gbr", None)
    log_close = np.log(close.clip(lower=1e-9))
    ets_path = _ets_path(log_close, len(close) - 1, horizon)
    if ets_path is not None and len(ets_path) >= horizon:
        predictions["ets"] = float(ets_path[horizon - 1] - log_close.iloc[-1])
    return predictions


def _evaluate_horizon(
    close: pd.Series, features: pd.DataFrame, horizon: int
) -> Optional[Dict[str, object]]:
    data = _prepare_horizon_data(features, close, horizon)
    if len(data) < 60:
        return None

    feature_columns = [column for column in features.columns if column in data.columns]
    fold_starts = _fold_starts(len(data), horizon)
    if not fold_starts:
        return None

    actual_log_returns: List[float] = []
    actual_prices: List[float] = []
    dates: List[str] = []
    model_backtests_log: Dict[str, List[float]] = {name: [] for name in MODEL_ORDER}

    log_close = np.log(close.clip(lower=1e-9))
    close_values = close.to_numpy(dtype=float)

    for start in fold_starts:
        test_size = max(8, min(12, horizon if horizon > 0 else 8))
        train = data.iloc[: max(0, start - horizon)]
        test = data.iloc[start : start + test_size]
        if len(train) < 50 or test.empty:
            continue
        x_train = train[feature_columns]
        y_train = train["target"]
        x_test = test[feature_columns]

        fold_predictions: Dict[str, np.ndarray] = {}
        fold_predictions["baseline"] = _baseline_predict(train, x_test, horizon)

        try:
            ml_models = _fit_ml_models(x_train, y_train)
            fold_predictions["ridge"] = np.asarray(
                ml_models["ridge"].predict(x_test), dtype=float
            )
            fold_predictions["gbr"] = np.asarray(
                ml_models["gbr"].predict(x_test), dtype=float
            )
        except Exception:
            fold_predictions.pop("ridge", None)
            fold_predictions.pop("gbr", None)

        ets_preds = _ets_fold_predictions(
            log_close, int(train["position"].iloc[-1]), test["position"], horizon
        )
        if ets_preds is not None:
            fold_predictions["ets"] = ets_preds

        for row_index, (_, row) in enumerate(test.iterrows()):
            pos = int(row["position"])
            actual_log = float(row["target"])
            actual_price = float(close_values[pos + horizon])
            actual_log_returns.append(actual_log)
            actual_prices.append(actual_price)
            dates.append(pd.Timestamp(row.name).date().isoformat())
            for model_name in MODEL_ORDER:
                value = np.nan
                if model_name in fold_predictions and row_index < len(
                    fold_predictions[model_name]
                ):
                    value = float(fold_predictions[model_name][row_index])
                model_backtests_log[model_name].append(value)

    if not actual_log_returns:
        return None

    actual_pct = (np.exp(np.asarray(actual_log_returns)) - 1.0) * 100.0
    model_predictions_pct = {}
    for name, values in model_backtests_log.items():
        preds = np.asarray(values, dtype=float)
        if len(preds) != len(actual_pct) or np.isnan(preds).any():
            continue
        model_predictions_pct[name] = (np.exp(preds) - 1.0) * 100.0

    weights = _weights_from_errors(actual_pct, model_predictions_pct)
    if not weights:
        return None

    ensemble_log = []
    backtest = []
    last_points = min(60, len(actual_pct))
    for idx in range(len(actual_pct)):
        weighted_values = [
            weights[name] * model_backtests_log[name][idx]
            for name in weights
            if not np.isnan(model_backtests_log[name][idx])
        ]
        weight_total = sum(
            weights[name]
            for name in weights
            if not np.isnan(model_backtests_log[name][idx])
        )
        ensemble_pred = (
            float(sum(weighted_values) / weight_total) if weight_total else 0.0
        )
        ensemble_log.append(ensemble_pred)
        if idx >= len(actual_pct) - last_points:
            origin_price = float(close.loc[pd.Timestamp(dates[idx])])
            predicted_price = origin_price * float(np.exp(ensemble_pred))
            backtest.append(
                {
                    "date": dates[idx],
                    "predicted": round(predicted_price, 4),
                    "actual": round(actual_prices[idx], 4),
                }
            )

    ensemble_log_array = np.asarray(ensemble_log, dtype=float)
    ensemble_pct = (np.exp(ensemble_log_array) - 1.0) * 100.0
    residuals_pct = actual_pct - ensemble_pct
    directional_accuracy = float(
        np.mean(np.sign(actual_pct) == np.sign(ensemble_pct)) * 100.0
    )
    actual_prices_array = np.asarray(actual_prices, dtype=float)
    predicted_prices_array = np.asarray(
        [
            float(close.loc[pd.Timestamp(dates[idx])]) * np.exp(ensemble_log_array[idx])
            for idx in range(len(ensemble_log_array))
        ],
        dtype=float,
    )
    valid_mask = actual_prices_array != 0
    mape = (
        float(
            np.mean(
                np.abs(
                    (
                        actual_prices_array[valid_mask]
                        - predicted_prices_array[valid_mask]
                    )
                    / actual_prices_array[valid_mask]
                )
            )
            * 100.0
        )
        if valid_mask.any()
        else None
    )

    final_models_log = _final_model_predictions(data, features, close, horizon)
    if not final_models_log:
        return None
    final_models_pct = {
        name: round((np.exp(value) - 1.0) * 100.0, 4)
        for name, value in final_models_log.items()
    }
    final_weight_total = sum(weights.get(name, 0.0) for name in final_models_log)
    if final_weight_total <= 0:
        final_weight_total = float(len(final_models_log))
        weighted_median_log = sum(final_models_log.values()) / final_weight_total
    else:
        weighted_median_log = (
            sum(
                final_models_log[name] * weights.get(name, 0.0)
                for name in final_models_log
            )
            / final_weight_total
        )
    median_return_pct = float((np.exp(weighted_median_log) - 1.0) * 100.0)

    q10 = float(np.quantile(residuals_pct, 0.10))
    q90 = float(np.quantile(residuals_pct, 0.90))
    lower_pct = median_return_pct + q10
    upper_pct = median_return_pct + q90
    ordered = sorted([lower_pct, median_return_pct, upper_pct])
    lower_pct, median_return_pct, upper_pct = ordered[0], ordered[1], ordered[2]

    last_price = float(close.iloc[-1])
    agreement = _agreement(final_models_pct, weights)
    result = {
        "horizon": int(horizon),
        "median_return_pct": round(median_return_pct, 4),
        "median_price": round(last_price * (1.0 + (median_return_pct / 100.0)), 4),
        "lower_pct": round(lower_pct, 4),
        "upper_pct": round(upper_pct, 4),
        "lower_price": round(last_price * (1.0 + (lower_pct / 100.0)), 4),
        "upper_price": round(last_price * (1.0 + (upper_pct / 100.0)), 4),
        "directional_accuracy": round(
            float(np.clip(directional_accuracy, 0.0, 100.0)), 4
        ),
        "mape": round(mape, 4) if mape is not None else None,
        "agreement": agreement,
        "models": final_models_pct,
        "low_confidence": bool(
            directional_accuracy < 55.0 or agreement < 0.6 or len(backtest) < 20
        ),
        "backtest": backtest,
    }
    return result


def ensemble_forecast(
    close: pd.Series,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    max_train: int = 750,
    volume: Optional[pd.Series] = None,
    market_close: Optional[pd.Series] = None,
) -> Dict[str, object]:
    try:
        clean_close = _sanitize_series(close, "close").tail(max_train)
        if len(clean_close) < MIN_HISTORY:
            return _unavailable("Need at least 120 clean closing prices.")

        features = _build_features(
            clean_close, volume=volume, market_close=market_close
        )
        valid_horizons = []
        for horizon in horizons:
            try:
                value = int(horizon)
            except (TypeError, ValueError):
                continue
            if value > 0 and value not in valid_horizons:
                valid_horizons.append(value)
        if not valid_horizons:
            valid_horizons = list(DEFAULT_HORIZONS)

        horizon_results = {}
        for horizon in valid_horizons:
            horizon_result = _evaluate_horizon(clean_close, features, horizon)
            if horizon_result is not None:
                horizon_results[horizon] = horizon_result

        if not horizon_results:
            return _unavailable(
                "Unable to build stable forecasts from the supplied data."
            )

        return {
            "available": True,
            "last_price": round(float(clean_close.iloc[-1]), 4),
            "last_date": pd.Timestamp(clean_close.index[-1]).date().isoformat(),
            "horizons": horizon_results,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return _unavailable(f"Forecasting failed safely: {exc}")


def forecast_path(result: Dict[str, object], h: int) -> Dict[str, object]:
    try:
        if not result or not result.get("available"):
            return {"available": False, "message": "Forecast unavailable."}
        horizon = result.get("horizons", {}).get(int(h))
        if horizon is None:
            return {"available": False, "message": "Horizon unavailable."}

        last_price = float(result["last_price"])
        last_date = pd.Timestamp(result["last_date"])
        steps = max(1, int(h))
        median_returns = np.linspace(
            0.0, float(horizon["median_return_pct"]), steps + 1
        )
        lower_returns = np.linspace(0.0, float(horizon["lower_pct"]), steps + 1)
        upper_returns = np.linspace(0.0, float(horizon["upper_pct"]), steps + 1)
        dates = [
            (last_date + timedelta(days=offset)).date().isoformat()
            for offset in range(steps + 1)
        ]
        return {
            "available": True,
            "dates": dates,
            "median": [
                round(last_price * (1.0 + value / 100.0), 4) for value in median_returns
            ],
            "lower": [
                round(last_price * (1.0 + value / 100.0), 4) for value in lower_returns
            ],
            "upper": [
                round(last_price * (1.0 + value / 100.0), 4) for value in upper_returns
            ],
        }
    except Exception as exc:
        return {"available": False, "message": f"Path generation failed safely: {exc}"}
