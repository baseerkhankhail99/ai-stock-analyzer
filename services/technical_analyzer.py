import pandas as pd
import numpy as np
from ta import trend, momentum, volatility, volume
import logging
from datetime import datetime, timedelta
from typing import Dict
from models import db, Stock, StockPrice, TechnicalIndicator

logger = logging.getLogger(__name__)

class TechnicalAnalyzer:
    """Calculate and store technical indicators"""
    
    def __init__(self):
        pass
    
    def calculate_indicators(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Calculate all technical indicators"""
        try:
            stock = Stock.query.filter_by(symbol=symbol).first()
            if not stock:
                return pd.DataFrame()
            
            prices = StockPrice.query.filter_by(stock_id=stock.id).filter(
                StockPrice.date >= datetime.utcnow() - timedelta(days=days)
            ).order_by(StockPrice.date).all()
            
            if len(prices) < 200:  # Need at least 200 days for full indicators
                return pd.DataFrame()
            
            df = pd.DataFrame([{
                'date': p.date,
                'open': p.open_price,
                'high': p.high_price,
                'low': p.low_price,
                'close': p.close_price,
                'volume': p.volume
            } for p in prices])
            
            df.set_index('date', inplace=True)
            
            # Moving Averages
            df['sma_20'] = trend.sma_indicator(df['close'], window=20)
            df['sma_50'] = trend.sma_indicator(df['close'], window=50)
            df['sma_200'] = trend.sma_indicator(df['close'], window=200)
            df['ema_12'] = trend.ema_indicator(df['close'], window=12)
            df['ema_26'] = trend.ema_indicator(df['close'], window=26)
            
            # Momentum Indicators
            df['rsi'] = momentum.rsi(df['close'], window=14)
            df['macd'] = trend.macd_diff(df['close'])
            df['macd_signal'] = trend.macd_signal(df['close'])
            df['macd_hist'] = df['macd'] - df['macd_signal']
            
            # Bollinger Bands
            bb_high = volatility.bollinger_hband(df['close'], window=20, window_dev=2)
            bb_low = volatility.bollinger_lband(df['close'], window=20, window_dev=2)
            bb_mid = volatility.bollinger_mavg(df['close'], window=20)
            df['bollinger_upper'] = bb_high
            df['bollinger_middle'] = bb_mid
            df['bollinger_lower'] = bb_low
            
            # Average True Range
            df['atr'] = volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
            
            # Volume Indicators
            df['obv'] = volume.on_balance_volume(df['close'], df['volume'])
            df['volume_sma'] = volume.sma_ease_of_movement(df['high'], df['low'], df['close'], df['volume'])
            
            return df
        except Exception as e:
            logger.error(f"Error calculating indicators for {symbol}: {str(e)}")
            return pd.DataFrame()
    
    def store_indicators(self, symbol: str) -> bool:
        """Calculate and store indicators in database"""
        try:
            df = self.calculate_indicators(symbol)
            if df.empty:
                return False
            
            stock = Stock.query.filter_by(symbol=symbol).first()
            
            for idx, row in df.iterrows():
                existing = TechnicalIndicator.query.filter_by(
                    stock_id=stock.id,
                    date=idx
                ).first()
                
                if not existing:
                    indicator = TechnicalIndicator(
                        stock_id=stock.id,
                        date=idx,
                        sma_20=float(row['sma_20']) if pd.notna(row['sma_20']) else None,
                        sma_50=float(row['sma_50']) if pd.notna(row['sma_50']) else None,
                        sma_200=float(row['sma_200']) if pd.notna(row['sma_200']) else None,
                        ema_12=float(row['ema_12']) if pd.notna(row['ema_12']) else None,
                        ema_26=float(row['ema_26']) if pd.notna(row['ema_26']) else None,
                        rsi=float(row['rsi']) if pd.notna(row['rsi']) else None,
                        macd=float(row['macd']) if pd.notna(row['macd']) else None,
                        macd_signal=float(row['macd_signal']) if pd.notna(row['macd_signal']) else None,
                        macd_hist=float(row['macd_hist']) if pd.notna(row['macd_hist']) else None,
                        bollinger_upper=float(row['bollinger_upper']) if pd.notna(row['bollinger_upper']) else None,
                        bollinger_middle=float(row['bollinger_middle']) if pd.notna(row['bollinger_middle']) else None,
                        bollinger_lower=float(row['bollinger_lower']) if pd.notna(row['bollinger_lower']) else None,
                        atr=float(row['atr']) if pd.notna(row['atr']) else None,
                        obv=float(row['obv']) if pd.notna(row['obv']) else None,
                        volume_sma=float(row['volume_sma']) if pd.notna(row['volume_sma']) else None,
                    )
                    db.session.add(indicator)
            
            db.session.commit()
            logger.info(f"Stored technical indicators for {symbol}")
            return True
        except Exception as e:
            logger.error(f"Error storing indicators for {symbol}: {str(e)}")
            db.session.rollback()
            return False
    
    def get_signal(self, symbol: str) -> Dict:
        """Generate trading signals based on indicators"""
        try:
            df = self.calculate_indicators(symbol)
            if df.empty:
                return {'signal': 'HOLD', 'strength': 0}
            
            latest = df.iloc[-1]
            signals = []
            
            # RSI signals
            if latest['rsi'] < 30:
                signals.append({'type': 'RSI', 'signal': 'BUY', 'strength': 0.9})
            elif latest['rsi'] > 70:
                signals.append({'type': 'RSI', 'signal': 'SELL', 'strength': 0.9})
            
            # MACD signals
            if latest['macd_hist'] > 0 and df.iloc[-2]['macd_hist'] <= 0:
                signals.append({'type': 'MACD', 'signal': 'BUY', 'strength': 0.8})
            elif latest['macd_hist'] < 0 and df.iloc[-2]['macd_hist'] >= 0:
                signals.append({'type': 'MACD', 'signal': 'SELL', 'strength': 0.8})
            
            # Moving Average signals
            if latest['close'] > latest['sma_20'] > latest['sma_50'] > latest['sma_200']:
                signals.append({'type': 'MA', 'signal': 'BUY', 'strength': 0.85})
            elif latest['close'] < latest['sma_20'] < latest['sma_50'] < latest['sma_200']:
                signals.append({'type': 'MA', 'signal': 'SELL', 'strength': 0.85})
            
            # Bollinger Bands signals
            if latest['close'] < latest['bollinger_lower']:
                signals.append({'type': 'BB', 'signal': 'BUY', 'strength': 0.7})
            elif latest['close'] > latest['bollinger_upper']:
                signals.append({'type': 'BB', 'signal': 'SELL', 'strength': 0.7})
            
            # Aggregate signals
            buy_signals = [s for s in signals if s['signal'] == 'BUY']
            sell_signals = [s for s in signals if s['signal'] == 'SELL']
            
            if len(buy_signals) > len(sell_signals):
                avg_strength = np.mean([s['strength'] for s in buy_signals])
                return {'signal': 'BUY', 'strength': avg_strength, 'signals': signals}
            elif len(sell_signals) > len(buy_signals):
                avg_strength = np.mean([s['strength'] for s in sell_signals])
                return {'signal': 'SELL', 'strength': avg_strength, 'signals': signals}
            else:
                return {'signal': 'HOLD', 'strength': 0.5, 'signals': signals}
        except Exception as e:
            logger.error(f"Error generating signals for {symbol}: {str(e)}")
            return {'signal': 'HOLD', 'strength': 0}
