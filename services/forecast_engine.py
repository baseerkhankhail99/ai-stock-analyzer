import logging
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.preprocessing import MinMaxScaler

from models import Forecast, Stock, StockPrice, db
from services import market_data

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

try:
    from prophet import Prophet
except Exception:  # Prophet is optional (heavy dependency)
    Prophet = None
    logger.info("Prophet not installed; Prophet forecasts are disabled")


def _load_keras():
    """Lazily import TensorFlow/Keras; return None if unavailable."""
    try:
        from tensorflow.keras.layers import LSTM, Dense, Dropout
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.optimizers import Adam
    except Exception:
        return None
    return Sequential, LSTM, Dense, Dropout, Adam


FEATURE_COLUMNS = ["ret_1", "ret_5", "vol_20", "dist_sma20", "dist_sma50"]
MIN_HISTORY = 90
BACKTEST_DAYS = 30


def build_features(close: pd.Series) -> pd.DataFrame:
    """Scale-free features derived from a close series."""
    ret1 = close.pct_change()
    return pd.DataFrame(
        {
            "ret_1": ret1,
            "ret_5": close.pct_change(5),
            "vol_20": ret1.rolling(20).std(),
            "dist_sma20": close / close.rolling(20).mean() - 1,
            "dist_sma50": close / close.rolling(50).mean() - 1,
        }
    )


def fit_return_models(close: pd.Series):
    """Fit RF + GB models that predict the next-day return."""
    data = build_features(close).join(close.pct_change().shift(-1).rename("target"))
    data = data.dropna()
    if len(data) < 30:
        return None
    X, y = data[FEATURE_COLUMNS].values, data["target"].values
    rf = RandomForestRegressor(n_estimators=50, max_depth=6, random_state=42)
    gb = GradientBoostingRegressor(n_estimators=50, max_depth=3, random_state=42)
    rf.fit(X, y)
    gb.fit(X, y)
    return rf, gb


def recursive_forecast(models, close: pd.Series, steps: int) -> pd.DataFrame:
    """Multi-step forecast: each predicted close is fed back into the features."""
    rf, gb = models
    history = [float(v) for v in close.tail(150)]
    sigma = float(close.pct_change().tail(60).std() or 0.0)
    last_date = pd.Timestamp(close.index[-1])
    rows = []
    for step in range(1, steps + 1):
        feats = build_features(pd.Series(history)).iloc[-1][FEATURE_COLUMNS]
        x = np.nan_to_num(feats.values.astype(float)).reshape(1, -1)
        ret = float(np.clip((rf.predict(x)[0] + gb.predict(x)[0]) / 2, -0.2, 0.2))
        history.append(history[-1] * (1 + ret))
        width = 1.96 * sigma * np.sqrt(step)
        rows.append(
            {
                "date": (last_date + timedelta(days=step)).to_pydatetime(),
                "pred": history[-1],
                "lower": history[-1] * float(np.exp(-width)),
                "upper": history[-1] * float(np.exp(width)),
            }
        )
    return pd.DataFrame(rows)


def mape(actual, predicted):
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    mask = actual != 0
    if not mask.any():
        return None
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100)


def ensemble_projection(close: pd.Series, steps: int):
    """Return (forecast frame, backtest MAPE) for the recursive ensemble."""
    models = fit_return_models(close)
    if models is None:
        return None, None
    frame = recursive_forecast(models, close, steps)
    error = None
    if len(close) >= MIN_HISTORY + BACKTEST_DAYS:
        train, test = close.iloc[:-BACKTEST_DAYS], close.iloc[-BACKTEST_DAYS:]
        bt_models = fit_return_models(train)
        if bt_models is not None:
            bt = recursive_forecast(bt_models, train, BACKTEST_DAYS)
            error = mape(test.values, bt["pred"].values)
    return frame, error


def _arima_fit_forecast(close: pd.Series, steps: int):
    from statsmodels.tsa.arima.model import ARIMA

    logs = np.log(close.tail(250).values.astype(float))
    fitted = ARIMA(logs, order=(1, 1, 1), trend="t").fit()
    result = fitted.get_forecast(steps)
    mean = np.exp(np.asarray(result.predicted_mean))
    bands = np.exp(np.asarray(result.conf_int(alpha=0.05)))
    return mean, bands


def arima_projection(close: pd.Series, steps: int):
    """Return (forecast frame, backtest MAPE) for ARIMA(1,1,1) on log prices."""
    mean, bands = _arima_fit_forecast(close, steps)
    last_date = pd.Timestamp(close.index[-1])
    frame = pd.DataFrame(
        {
            "date": [
                (last_date + timedelta(days=i + 1)).to_pydatetime()
                for i in range(steps)
            ],
            "pred": mean,
            "lower": bands[:, 0],
            "upper": bands[:, 1],
        }
    )
    error = None
    if len(close) >= MIN_HISTORY + BACKTEST_DAYS:
        train, test = close.iloc[:-BACKTEST_DAYS], close.iloc[-BACKTEST_DAYS:]
        bt_mean, _ = _arima_fit_forecast(train, BACKTEST_DAYS)
        error = mape(test.values, bt_mean)
    return frame, error


class StockForecastEngine:
    """Multi-model forecasting engine for stock price prediction"""

    def __init__(self, lookback_days: int = 60):
        self.lookback_days = lookback_days
        self.scaler = MinMaxScaler(feature_range=(0, 1))

    def get_historical_data(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Historical data from the database, falling back to live market data"""
        data = self._load_from_db(symbol, days)
        if data.empty:
            data = self._load_remote(symbol)
        return data

    def _load_remote(self, symbol: str) -> pd.DataFrame:
        try:
            data = market_data.fetch_history_frame(symbol)
            if data.empty:
                return data
            data.index.name = "date"
            return data[["close", "open", "high", "low", "volume"]]
        except Exception as e:
            logger.error(f"Error fetching remote data for {symbol}: {str(e)}")
            return pd.DataFrame()

    def _load_from_db(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Retrieve historical data from database"""
        try:
            stock = Stock.query.filter_by(symbol=symbol).first()
            if not stock:
                logger.warning(f"Stock {symbol} not found")
                return pd.DataFrame()

            prices = (
                StockPrice.query.filter_by(stock_id=stock.id)
                .filter(StockPrice.date >= datetime.utcnow() - timedelta(days=days))
                .order_by(StockPrice.date)
                .all()
            )

            if not prices:
                return pd.DataFrame()

            data = pd.DataFrame(
                [
                    {
                        "date": p.date,
                        "close": p.close_price,
                        "open": p.open_price,
                        "high": p.high_price,
                        "low": p.low_price,
                        "volume": p.volume,
                    }
                    for p in prices
                ]
            )

            return data.set_index("date")
        except Exception as e:
            logger.error(f"Error retrieving historical data for {symbol}: {str(e)}")
            return pd.DataFrame()

    def forecast_prophet(self, symbol: str, forecast_days: int = 30) -> List[Dict]:
        """Prophet-based forecasting"""
        if Prophet is None:
            logger.warning("Prophet is not installed; skipping Prophet forecast")
            return []
        try:
            data = self.get_historical_data(symbol, days=365)
            if data.empty:
                return []

            df = pd.DataFrame({"ds": data.index, "y": data["close"].values})

            model = Prophet(yearly_seasonality=True, daily_seasonality=False)
            model.fit(df)

            future = model.make_future_dataframe(periods=forecast_days)
            forecast = model.predict(future)

            results = []
            for idx, row in forecast.tail(forecast_days).iterrows():
                results.append(
                    {
                        "symbol": symbol,
                        "forecast_date": row["ds"],
                        "predicted_price": float(row["yhat"]),
                        "lower_bound": float(row["yhat_lower"]),
                        "upper_bound": float(row["yhat_upper"]),
                        "model_type": "prophet",
                        "confidence_score": 0.85,
                    }
                )

            return results
        except Exception as e:
            logger.error(f"Prophet forecasting error for {symbol}: {str(e)}")
            return []

    def forecast_lstm(self, symbol: str, forecast_days: int = 30) -> List[Dict]:
        """LSTM neural network forecasting"""
        keras = _load_keras()
        if keras is None:
            logger.warning("TensorFlow is not installed; skipping LSTM forecast")
            return []
        Sequential, LSTM, Dense, Dropout, Adam = keras
        try:
            data = self.get_historical_data(symbol, days=365)
            if data.empty or len(data) < self.lookback_days:
                return []

            prices = data["close"].values.reshape(-1, 1)
            scaled_prices = self.scaler.fit_transform(prices)

            # Prepare training data
            X, y = [], []
            for i in range(len(scaled_prices) - self.lookback_days):
                X.append(scaled_prices[i : i + self.lookback_days])
                y.append(scaled_prices[i + self.lookback_days])

            X, y = np.array(X), np.array(y)

            # Build LSTM model
            model = Sequential(
                [
                    LSTM(50, activation="relu", input_shape=(self.lookback_days, 1)),
                    Dropout(0.2),
                    Dense(25, activation="relu"),
                    Dropout(0.2),
                    Dense(1),
                ]
            )

            model.compile(optimizer=Adam(learning_rate=0.001), loss="mse")
            model.fit(X, y, epochs=20, batch_size=32, verbose=0)

            # Generate forecasts
            last_sequence = scaled_prices[-self.lookback_days :].reshape(
                1, self.lookback_days, 1
            )
            results = []

            for i in range(forecast_days):
                next_pred = model.predict(last_sequence, verbose=0)
                results.append(
                    {
                        "symbol": symbol,
                        "forecast_date": datetime.utcnow() + timedelta(days=i + 1),
                        "predicted_price": float(
                            self.scaler.inverse_transform(next_pred)[0][0]
                        ),
                        "lower_bound": None,
                        "upper_bound": None,
                        "model_type": "lstm",
                        "confidence_score": 0.80,
                    }
                )

                last_sequence = np.append(
                    last_sequence[:, 1:, :], next_pred.reshape(1, 1, 1), axis=1
                )

            return results
        except Exception as e:
            logger.error(f"LSTM forecasting error for {symbol}: {str(e)}")
            return []

    def _records(self, symbol, frame, model_type, error, confidence) -> List[Dict]:
        return [
            {
                "symbol": symbol,
                "forecast_date": row.date,
                "predicted_price": float(row.pred),
                "lower_bound": float(row.lower),
                "upper_bound": float(row.upper),
                "model_type": model_type,
                "confidence_score": confidence,
                "mape": error,
            }
            for row in frame.itertuples()
        ]

    def _close_series(self, symbol: str) -> pd.Series:
        data = self.get_historical_data(symbol, days=365)
        if data.empty or "close" not in data:
            return pd.Series(dtype=float)
        return data["close"].astype(float).dropna()

    def forecast_ensemble(self, symbol: str, forecast_days: int = 30) -> List[Dict]:
        """Random Forest + Gradient Boosting recursive multi-step forecasting"""
        try:
            close = self._close_series(symbol)
            if len(close) < MIN_HISTORY:
                return []
            frame, error = ensemble_projection(close, forecast_days)
            if frame is None:
                return []
            return self._records(symbol, frame, "ensemble", error, 0.82)
        except Exception as e:
            logger.error(f"Ensemble forecasting error for {symbol}: {str(e)}")
            return []

    def forecast_arima(self, symbol: str, forecast_days: int = 30) -> List[Dict]:
        """ARIMA forecasting on log prices"""
        try:
            close = self._close_series(symbol)
            if len(close) < MIN_HISTORY:
                return []
            frame, error = arima_projection(close, forecast_days)
            return self._records(symbol, frame, "arima", error, 0.75)
        except Exception as e:
            logger.error(f"ARIMA forecasting error for {symbol}: {str(e)}")
            return []

    def generate_all_forecasts(self, symbol: str, forecast_days: int = 30) -> Dict:
        """Generate forecasts using all models"""
        try:
            prophet_forecast = self.forecast_prophet(symbol, forecast_days)
            lstm_forecast = self.forecast_lstm(symbol, forecast_days)
            ensemble_forecast = self.forecast_ensemble(symbol, forecast_days)
            arima_forecast = self.forecast_arima(symbol, forecast_days)

            # Store forecasts in database
            stock = Stock.query.filter_by(symbol=symbol).first()
            if stock:
                for forecast_list in [
                    prophet_forecast,
                    lstm_forecast,
                    ensemble_forecast,
                    arima_forecast,
                ]:
                    for f in forecast_list:
                        forecast_record = Forecast(
                            stock_id=stock.id,
                            forecast_date=f["forecast_date"],
                            predicted_price=f["predicted_price"],
                            lower_bound=f["lower_bound"],
                            upper_bound=f["upper_bound"],
                            model_type=f["model_type"],
                            confidence_score=f["confidence_score"],
                        )
                        db.session.add(forecast_record)
                db.session.commit()

            return {
                "symbol": symbol,
                "prophet": prophet_forecast,
                "lstm": lstm_forecast,
                "ensemble": ensemble_forecast,
                "arima": arima_forecast,
                "metrics": {
                    name: forecast_list[0].get("mape")
                    for name, forecast_list in (
                        ("ensemble", ensemble_forecast),
                        ("arima", arima_forecast),
                    )
                    if forecast_list
                },
            }
        except Exception as e:
            logger.error(f"Error generating all forecasts for {symbol}: {str(e)}")
            return {}
