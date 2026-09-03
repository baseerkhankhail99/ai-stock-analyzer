import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from prophet import Prophet
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.optimizers import Adam
import warnings
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple, List
from models import db, Forecast, StockPrice, Stock

warnings.filterwarnings('ignore')
logger = logging.getLogger(__name__)

class StockForecastEngine:
    """Multi-model forecasting engine for stock price prediction"""
    
    def __init__(self, lookback_days: int = 60):
        self.lookback_days = lookback_days
        self.scaler = MinMaxScaler(feature_range=(0, 1))
    
    def get_historical_data(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Retrieve historical data from database"""
        try:
            stock = Stock.query.filter_by(symbol=symbol).first()
            if not stock:
                logger.warning(f"Stock {symbol} not found")
                return pd.DataFrame()
            
            prices = StockPrice.query.filter_by(stock_id=stock.id).filter(
                StockPrice.date >= datetime.utcnow() - timedelta(days=days)
            ).order_by(StockPrice.date).all()
            
            if not prices:
                return pd.DataFrame()
            
            data = pd.DataFrame([{
                'date': p.date,
                'close': p.close_price,
                'open': p.open_price,
                'high': p.high_price,
                'low': p.low_price,
                'volume': p.volume
            } for p in prices])
            
            return data.set_index('date')
        except Exception as e:
            logger.error(f"Error retrieving historical data for {symbol}: {str(e)}")
            return pd.DataFrame()
    
    def forecast_prophet(self, symbol: str, forecast_days: int = 30) -> List[Dict]:
        """Prophet-based forecasting"""
        try:
            data = self.get_historical_data(symbol, days=365)
            if data.empty:
                return []
            
            df = pd.DataFrame({
                'ds': data.index,
                'y': data['close'].values
            })
            
            model = Prophet(yearly_seasonality=True, daily_seasonality=False)
            model.fit(df)
            
            future = model.make_future_dataframe(periods=forecast_days)
            forecast = model.predict(future)
            
            results = []
            for idx, row in forecast.tail(forecast_days).iterrows():
                results.append({
                    'symbol': symbol,
                    'forecast_date': row['ds'],
                    'predicted_price': float(row['yhat']),
                    'lower_bound': float(row['yhat_lower']),
                    'upper_bound': float(row['yhat_upper']),
                    'model_type': 'prophet',
                    'confidence_score': 0.85
                })
            
            return results
        except Exception as e:
            logger.error(f"Prophet forecasting error for {symbol}: {str(e)}")
            return []
    
    def forecast_lstm(self, symbol: str, forecast_days: int = 30) -> List[Dict]:
        """LSTM neural network forecasting"""
        try:
            data = self.get_historical_data(symbol, days=365)
            if data.empty or len(data) < self.lookback_days:
                return []
            
            prices = data['close'].values.reshape(-1, 1)
            scaled_prices = self.scaler.fit_transform(prices)
            
            # Prepare training data
            X, y = [], []
            for i in range(len(scaled_prices) - self.lookback_days):
                X.append(scaled_prices[i:i + self.lookback_days])
                y.append(scaled_prices[i + self.lookback_days])
            
            X, y = np.array(X), np.array(y)
            
            # Build LSTM model
            model = Sequential([
                LSTM(50, activation='relu', input_shape=(self.lookback_days, 1)),
                Dropout(0.2),
                Dense(25, activation='relu'),
                Dropout(0.2),
                Dense(1)
            ])
            
            model.compile(optimizer=Adam(learning_rate=0.001), loss='mse')
            model.fit(X, y, epochs=20, batch_size=32, verbose=0)
            
            # Generate forecasts
            last_sequence = scaled_prices[-self.lookback_days:].reshape(1, self.lookback_days, 1)
            results = []
            
            for i in range(forecast_days):
                next_pred = model.predict(last_sequence, verbose=0)
                results.append({
                    'symbol': symbol,
                    'forecast_date': datetime.utcnow() + timedelta(days=i+1),
                    'predicted_price': float(self.scaler.inverse_transform(next_pred)[0][0]),
                    'lower_bound': None,
                    'upper_bound': None,
                    'model_type': 'lstm',
                    'confidence_score': 0.80
                })
                
                last_sequence = np.append(last_sequence[:, 1:, :], next_pred.reshape(1, 1, 1), axis=1)
            
            return results
        except Exception as e:
            logger.error(f"LSTM forecasting error for {symbol}: {str(e)}")
            return []
    
    def forecast_ensemble(self, symbol: str, forecast_days: int = 30) -> List[Dict]:
        """Random Forest + Gradient Boosting ensemble forecasting"""
        try:
            data = self.get_historical_data(symbol, days=365)
            if data.empty:
                return []
            
            # Feature engineering
            data['returns'] = data['close'].pct_change()
            data['volatility'] = data['returns'].rolling(window=20).std()
            data['sma_20'] = data['close'].rolling(window=20).mean()
            data['sma_50'] = data['close'].rolling(window=50).mean()
            data.dropna(inplace=True)
            
            X = data[['returns', 'volatility', 'sma_20', 'sma_50']].values
            y = data['close'].values
            
            # Train models
            rf_model = RandomForestRegressor(n_estimators=100, random_state=42)
            gb_model = GradientBoostingRegressor(n_estimators=100, random_state=42)
            
            rf_model.fit(X, y)
            gb_model.fit(X, y)
            
            results = []
            last_row = X[-1]
            
            for i in range(forecast_days):
                rf_pred = rf_model.predict([last_row])[0]
                gb_pred = gb_model.predict([last_row])[0]
                ensemble_pred = (rf_pred + gb_pred) / 2
                
                results.append({
                    'symbol': symbol,
                    'forecast_date': datetime.utcnow() + timedelta(days=i+1),
                    'predicted_price': float(ensemble_pred),
                    'lower_bound': float(ensemble_pred * 0.95),
                    'upper_bound': float(ensemble_pred * 1.05),
                    'model_type': 'ensemble',
                    'confidence_score': 0.82
                })
            
            return results
        except Exception as e:
            logger.error(f"Ensemble forecasting error for {symbol}: {str(e)}")
            return []
    
    def generate_all_forecasts(self, symbol: str, forecast_days: int = 30) -> Dict:
        """Generate forecasts using all models"""
        try:
            prophet_forecast = self.forecast_prophet(symbol, forecast_days)
            lstm_forecast = self.forecast_lstm(symbol, forecast_days)
            ensemble_forecast = self.forecast_ensemble(symbol, forecast_days)
            
            # Store forecasts in database
            stock = Stock.query.filter_by(symbol=symbol).first()
            if stock:
                for forecast_list in [prophet_forecast, lstm_forecast, ensemble_forecast]:
                    for f in forecast_list:
                        forecast_record = Forecast(
                            stock_id=stock.id,
                            forecast_date=f['forecast_date'],
                            predicted_price=f['predicted_price'],
                            lower_bound=f['lower_bound'],
                            upper_bound=f['upper_bound'],
                            model_type=f['model_type'],
                            confidence_score=f['confidence_score']
                        )
                        db.session.add(forecast_record)
                db.session.commit()
            
            return {
                'symbol': symbol,
                'prophet': prophet_forecast,
                'lstm': lstm_forecast,
                'ensemble': ensemble_forecast
            }
        except Exception as e:
            logger.error(f"Error generating all forecasts for {symbol}: {str(e)}")
            return {}
