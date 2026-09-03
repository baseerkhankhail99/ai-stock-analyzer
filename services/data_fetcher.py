import yfinance as yf
import requests
from alpha_vantage.timeseries import TimeSeries
from datetime import datetime, timedelta
import pandas as pd
import logging
from typing import List, Dict, Optional
from models import db, Stock, StockPrice
import os

logger = logging.getLogger(__name__)

class StockDataFetcher:
    """Fetch real-time and historical stock data"""
    
    def __init__(self):
        self.alpha_vantage_key = os.getenv('ALPHA_VANTAGE_API_KEY')
        self.finnhub_key = os.getenv('FINNHUB_API_KEY')
        
    def fetch_real_time_price(self, symbol: str) -> Dict:
        """Fetch real-time stock price using yfinance"""
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period='1d')
            info = ticker.info
            
            if data.empty:
                logger.warning(f"No data found for {symbol}")
                return {}
            
            latest = data.iloc[-1]
            return {
                'symbol': symbol,
                'price': float(latest['Close']),
                'open': float(latest['Open']),
                'high': float(latest['High']),
                'low': float(latest['Low']),
                'volume': int(latest['Volume']),
                'change': info.get('regularMarketChange', 0),
                'change_percent': info.get('regularMarketChangePercent', 0),
                'timestamp': datetime.now(),
                'market_cap': info.get('marketCap'),
                'pe_ratio': info.get('trailingPE'),
                'dividend_yield': info.get('dividendYield'),
            }
        except Exception as e:
            logger.error(f"Error fetching real-time price for {symbol}: {str(e)}")
            return {}
    
    def fetch_historical_data(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Fetch historical data"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            data = yf.download(symbol, start=start_date, end=end_date, progress=False)
            return data
        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {str(e)}")
            return pd.DataFrame()
    
    def store_stock_prices(self, symbol: str, dataframe: pd.DataFrame) -> bool:
        """Store historical prices in database"""
        try:
            stock = Stock.query.filter_by(symbol=symbol).first()
            if not stock:
                stock = Stock(symbol=symbol, name=symbol)
                db.session.add(stock)
                db.session.commit()
            
            for idx, row in dataframe.iterrows():
                price_record = StockPrice.query.filter_by(
                    stock_id=stock.id,
                    date=idx
                ).first()
                
                if not price_record:
                    price_record = StockPrice(
                        stock_id=stock.id,
                        open_price=float(row['Open']),
                        high_price=float(row['High']),
                        low_price=float(row['Low']),
                        close_price=float(row['Close']),
                        volume=int(row['Volume']),
                        adj_close=float(row['Adj Close']) if 'Adj Close' in row else float(row['Close']),
                        date=idx
                    )
                    db.session.add(price_record)
            
            db.session.commit()
            logger.info(f"Stored {len(dataframe)} price records for {symbol}")
            return True
        except Exception as e:
            logger.error(f"Error storing prices for {symbol}: {str(e)}")
            db.session.rollback()
            return False
    
    def fetch_and_store_all_stocks(self, symbols: List[str]):
        """Fetch and store data for all stocks"""
        for symbol in symbols:
            logger.info(f"Fetching data for {symbol}")
            historical_data = self.fetch_historical_data(symbol)
            if not historical_data.empty:
                self.store_stock_prices(symbol, historical_data)
    
    def get_stock_info(self, symbol: str) -> Dict:
        """Get detailed stock information"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            return {
                'symbol': symbol,
                'name': info.get('longName', symbol),
                'sector': info.get('sector'),
                'industry': info.get('industry'),
                'market_cap': info.get('marketCap'),
                'pe_ratio': info.get('trailingPE'),
                'dividend_yield': info.get('dividendYield'),
                'beta': info.get('beta'),
                '52_week_high': info.get('fiftyTwoWeekHigh'),
                '52_week_low': info.get('fiftyTwoWeekLow'),
                'avg_volume': info.get('averageVolume'),
                'description': info.get('longBusinessSummary'),
            }
        except Exception as e:
            logger.error(f"Error fetching stock info for {symbol}: {str(e)}")
            return {}

class CryptoDataFetcher:
    """Fetch cryptocurrency data"""
    
    def fetch_real_time_crypto(self, symbol: str = 'BTC-USD') -> Dict:
        """Fetch real-time crypto price"""
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period='1d')
            
            if data.empty:
                return {}
            
            latest = data.iloc[-1]
            return {
                'symbol': symbol,
                'price': float(latest['Close']),
                'open': float(latest['Open']),
                'high': float(latest['High']),
                'low': float(latest['Low']),
                'volume': int(latest['Volume']),
                'timestamp': datetime.now(),
            }
        except Exception as e:
            logger.error(f"Error fetching crypto data for {symbol}: {str(e)}")
            return {}
    
    def fetch_historical_crypto(self, symbol: str = 'BTC-USD', days: int = 365) -> pd.DataFrame:
        """Fetch historical crypto data"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            data = yf.download(symbol, start=start_date, end=end_date, progress=False)
            return data
        except Exception as e:
            logger.error(f"Error fetching historical crypto data for {symbol}: {str(e)}")
            return pd.DataFrame()
