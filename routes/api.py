from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
import logging
from datetime import datetime, timedelta
from models import db, Stock, StockPrice, Forecast, TechnicalIndicator, AnalyticsReport
from services.data_fetcher import StockDataFetcher, CryptoDataFetcher
from services.forecast_engine import StockForecastEngine
from services.technical_analyzer import TechnicalAnalyzer
from services.analytics_engine import AnalyticsEngine

logger = logging.getLogger(__name__)

api = Blueprint('api', __name__, url_prefix='/api')

# Initialize services
data_fetcher = StockDataFetcher()
crypto_fetcher = CryptoDataFetcher()
forecast_engine = StockForecastEngine()
technical_analyzer = TechnicalAnalyzer()
analytics_engine = AnalyticsEngine()

# ============= REAL-TIME DATA ENDPOINTS =============

@api.route('/stocks/<symbol>/price', methods=['GET'])
@cross_origin()
def get_stock_price(symbol):
    """Get real-time stock price"""
    try:
        price_data = data_fetcher.fetch_real_time_price(symbol)
        if price_data:
            return jsonify(price_data), 200
        return jsonify({'error': 'Stock not found'}), 404
    except Exception as e:
        logger.error(f"Error fetching price for {symbol}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@api.route('/crypto/<symbol>/price', methods=['GET'])
@cross_origin()
def get_crypto_price(symbol):
    """Get real-time crypto price"""
    try:
        price_data = crypto_fetcher.fetch_real_time_crypto(symbol)
        if price_data:
            return jsonify(price_data), 200
        return jsonify({'error': 'Crypto not found'}), 404
    except Exception as e:
        logger.error(f"Error fetching crypto price: {str(e)}")
        return jsonify({'error': str(e)}), 500

@api.route('/stocks/<symbol>/info', methods=['GET'])
@cross_origin()
def get_stock_info(symbol):
    """Get detailed stock information"""
    try:
        info = data_fetcher.get_stock_info(symbol)
        if info:
            return jsonify(info), 200
        return jsonify({'error': 'Stock not found'}), 404
    except Exception as e:
        logger.error(f"Error fetching stock info: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ============= HISTORICAL DATA ENDPOINTS =============

@api.route('/stocks/<symbol>/history', methods=['GET'])
@cross_origin()
def get_stock_history(symbol):
    """Get historical stock prices"""
    try:
        days = request.args.get('days', 365, type=int)
        stock = Stock.query.filter_by(symbol=symbol).first()
        
        if not stock:
            return jsonify({'error': 'Stock not found'}), 404
        
        prices = StockPrice.query.filter_by(stock_id=stock.id).filter(
            StockPrice.date >= datetime.utcnow() - timedelta(days=days)
        ).order_by(StockPrice.date).all()
        
        data = [{
            'date': p.date.isoformat(),
            'open': p.open_price,
            'high': p.high_price,
            'low': p.low_price,
            'close': p.close_price,
            'volume': p.volume,
            'adj_close': p.adj_close
        } for p in prices]
        
        return jsonify({'symbol': symbol, 'data': data}), 200
    except Exception as e:
        logger.error(f"Error fetching history: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ============= FORECASTING ENDPOINTS =============

@api.route('/stocks/<symbol>/forecast', methods=['GET'])
@cross_origin()
def get_forecast(symbol):
    """Get stock price forecast"""
    try:
        days = request.args.get('days', 30, type=int)
        forecast_data = forecast_engine.generate_all_forecasts(symbol, days)
        
        if forecast_data:
            return jsonify(forecast_data), 200
        return jsonify({'error': 'Could not generate forecast'}), 500
    except Exception as e:
        logger.error(f"Error generating forecast: {str(e)}")
        return jsonify({'error': str(e)}), 500

@api.route('/stocks/<symbol>/forecasts/stored', methods=['GET'])
@cross_origin()
def get_stored_forecasts(symbol):
    """Get stored forecasts from database"""
    try:
        stock = Stock.query.filter_by(symbol=symbol).first()
        if not stock:
            return jsonify({'error': 'Stock not found'}), 404
        
        forecasts = Forecast.query.filter_by(stock_id=stock.id).order_by(
            Forecast.forecast_date
        ).all()
        
        data = [{
            'date': f.forecast_date.isoformat(),
            'predicted_price': f.predicted_price,
            'lower_bound': f.lower_bound,
            'upper_bound': f.upper_bound,
            'model': f.model_type,
            'confidence': f.confidence_score
        } for f in forecasts]
        
        return jsonify({'symbol': symbol, 'forecasts': data}), 200
    except Exception as e:
        logger.error(f"Error fetching forecasts: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ============= TECHNICAL ANALYSIS ENDPOINTS =============

@api.route('/stocks/<symbol>/indicators', methods=['GET'])
@cross_origin()
def get_technical_indicators(symbol):
    """Get technical indicators"""
    try:
        stock = Stock.query.filter_by(symbol=symbol).first()
        if not stock:
            return jsonify({'error': 'Stock not found'}), 404
        
        indicators = TechnicalIndicator.query.filter_by(stock_id=stock.id).order_by(
            TechnicalIndicator.date.desc()
        ).limit(1).first()
        
        if indicators:
            return jsonify({
                'symbol': symbol,
                'date': indicators.date.isoformat(),
                'sma_20': indicators.sma_20,
                'sma_50': indicators.sma_50,
                'sma_200': indicators.sma_200,
                'rsi': indicators.rsi,
                'macd': indicators.macd,
                'macd_signal': indicators.macd_signal,
                'bollinger_upper': indicators.bollinger_upper,
                'bollinger_lower': indicators.bollinger_lower,
                'atr': indicators.atr,
                'obv': indicators.obv
            }), 200
        
        return jsonify({'error': 'No indicators found'}), 404
    except Exception as e:
        logger.error(f"Error fetching indicators: {str(e)}")
        return jsonify({'error': str(e)}), 500

@api.route('/stocks/<symbol>/signals', methods=['GET'])
@cross_origin()
def get_trading_signals(symbol):
    """Get trading signals"""
    try:
        signals = technical_analyzer.get_signal(symbol)
        return jsonify({'symbol': symbol, 'signal': signals}), 200
    except Exception as e:
        logger.error(f"Error getting signals: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ============= ANALYTICS ENDPOINTS =============

@api.route('/stocks/<symbol>/analytics', methods=['GET'])
@cross_origin()
def get_analytics(symbol):
    """Get analytics report"""
    try:
        analytics = analytics_engine.generate_analytics_report(symbol)
        if analytics:
            return jsonify(analytics), 200
        return jsonify({'error': 'Could not generate analytics'}), 500
    except Exception as e:
        logger.error(f"Error generating analytics: {str(e)}")
        return jsonify({'error': str(e)}), 500

@api.route('/stocks/<symbol>/price-targets', methods=['GET'])
@cross_origin()
def get_price_targets(symbol):
    """Get price targets"""
    try:
        targets = analytics_engine.get_price_targets(symbol)
        if targets:
            return jsonify(targets), 200
        return jsonify({'error': 'Could not calculate price targets'}), 500
    except Exception as e:
        logger.error(f"Error calculating targets: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ============= COMPARISON ENDPOINTS =============

@api.route('/stocks/compare', methods=['GET'])
@cross_origin()
def compare_stocks():
    """Compare two stocks"""
    try:
        symbol1 = request.args.get('symbol1')
        symbol2 = request.args.get('symbol2')
        days = request.args.get('days', 30, type=int)
        
        if not symbol1 or not symbol2:
            return jsonify({'error': 'symbol1 and symbol2 required'}), 400
        
        comparison = analytics_engine.compare_stocks(symbol1, symbol2, days)
        if comparison:
            return jsonify(comparison), 200
        return jsonify({'error': 'Could not compare stocks'}), 500
    except Exception as e:
        logger.error(f"Error comparing stocks: {str(e)}")
        return jsonify({'error': str(e)}), 500

@api.route('/stocks/compare-all', methods=['POST'])
@cross_origin()
def compare_all_stocks():
    """Compare all stocks with each other"""
    try:
        data = request.get_json()
        symbols = data.get('symbols', [])
        days = data.get('days', 30)
        
        if not symbols:
            return jsonify({'error': 'symbols array required'}), 400
        
        comparisons = analytics_engine.compare_all_stocks(symbols, days)
        return jsonify({'comparisons': comparisons}), 200
    except Exception as e:
        logger.error(f"Error in compare all: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ============= DATA MANAGEMENT ENDPOINTS =============

@api.route('/stocks/sync', methods=['POST'])
@cross_origin()
def sync_stock_data():
    """Sync stock data"""
    try:
        data = request.get_json()
        symbols = data.get('symbols', [])
        
        if not symbols:
            return jsonify({'error': 'symbols array required'}), 400
        
        data_fetcher.fetch_and_store_all_stocks(symbols)
        return jsonify({'message': f'Synced {len(symbols)} stocks'}), 200
    except Exception as e:
        logger.error(f"Error syncing data: {str(e)}")
        return jsonify({'error': str(e)}), 500

@api.route('/stocks/indicators/calculate', methods=['POST'])
@cross_origin()
def calculate_indicators():
    """Calculate and store technical indicators"""
    try:
        data = request.get_json()
        symbols = data.get('symbols', [])
        
        if not symbols:
            return jsonify({'error': 'symbols array required'}), 400
        
        results = []
        for symbol in symbols:
            success = technical_analyzer.store_indicators(symbol)
            results.append({'symbol': symbol, 'success': success})
        
        return jsonify({'results': results}), 200
    except Exception as e:
        logger.error(f"Error calculating indicators: {str(e)}")
        return jsonify({'error': str(e)}), 500

@api.route('/stocks/list', methods=['GET'])
@cross_origin()
def get_stocks_list():
    """Get list of all tracked stocks"""
    try:
        stocks = Stock.query.all()
        data = [{
            'symbol': s.symbol,
            'name': s.name,
            'sector': s.sector,
            'market_cap': s.market_cap,
            'pe_ratio': s.pe_ratio
        } for s in stocks]
        
        return jsonify({'stocks': data}), 200
    except Exception as e:
        logger.error(f"Error fetching stocks list: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ============= HEALTH CHECK =============

@api.route('/health', methods=['GET'])
@cross_origin()
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()}), 200
