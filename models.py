from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import enum

db = SQLAlchemy()

class Stock(db.Model):
    """Stock model"""
    __tablename__ = 'stocks'
    
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    sector = db.Column(db.String(100))
    industry = db.Column(db.String(100))
    market_cap = db.Column(db.BigInteger)
    pe_ratio = db.Column(db.Float)
    dividend_yield = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    prices = db.relationship('StockPrice', backref='stock', lazy='dynamic', cascade='all, delete-orphan')
    forecasts = db.relationship('Forecast', backref='stock', lazy='dynamic', cascade='all, delete-orphan')
    alerts = db.relationship('Alert', backref='stock', lazy='dynamic', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Stock {self.symbol}>'

class StockPrice(db.Model):
    """Stock price model for storing historical prices"""
    __tablename__ = 'stock_prices'
    
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False, index=True)
    open_price = db.Column(db.Float, nullable=False)
    high_price = db.Column(db.Float, nullable=False)
    low_price = db.Column(db.Float, nullable=False)
    close_price = db.Column(db.Float, nullable=False)
    volume = db.Column(db.BigInteger)
    adj_close = db.Column(db.Float)
    date = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('stock_id', 'date', name='unique_stock_date'),)
    
    def __repr__(self):
        return f'<StockPrice {self.stock_id} on {self.date}>'

class Forecast(db.Model):
    """Stock price forecast model"""
    __tablename__ = 'forecasts'
    
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False, index=True)
    forecast_date = db.Column(db.DateTime, nullable=False, index=True)
    predicted_price = db.Column(db.Float, nullable=False)
    lower_bound = db.Column(db.Float)  # 95% confidence interval lower
    upper_bound = db.Column(db.Float)  # 95% confidence interval upper
    model_type = db.Column(db.String(50), nullable=False)  # prophet, lstm, ensemble, etc.
    confidence_score = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Forecast {self.stock_id} on {self.forecast_date}>'

class TechnicalIndicator(db.Model):
    """Technical indicators model"""
    __tablename__ = 'technical_indicators'
    
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False, index=True)
    date = db.Column(db.DateTime, nullable=False, index=True)
    
    # Moving averages
    sma_20 = db.Column(db.Float)
    sma_50 = db.Column(db.Float)
    sma_200 = db.Column(db.Float)
    ema_12 = db.Column(db.Float)
    ema_26 = db.Column(db.Float)
    
    # Momentum indicators
    rsi = db.Column(db.Float)  # Relative Strength Index
    macd = db.Column(db.Float)  # MACD line
    macd_signal = db.Column(db.Float)
    macd_hist = db.Column(db.Float)
    
    # Volatility
    bollinger_upper = db.Column(db.Float)
    bollinger_middle = db.Column(db.Float)
    bollinger_lower = db.Column(db.Float)
    atr = db.Column(db.Float)  # Average True Range
    
    # Volume
    obv = db.Column(db.Float)  # On Balance Volume
    volume_sma = db.Column(db.Float)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<TechnicalIndicator {self.stock_id} on {self.date}>'

class StockComparison(db.Model):
    """Stock correlation and comparison data"""
    __tablename__ = 'stock_comparisons'
    
    id = db.Column(db.Integer, primary_key=True)
    stock1_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False, index=True)
    stock2_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False, index=True)
    correlation = db.Column(db.Float)  # Correlation coefficient
    beta = db.Column(db.Float)  # Beta relative to market
    performance_ratio = db.Column(db.Float)  # Performance comparison
    volatility_ratio = db.Column(db.Float)  # Volatility comparison
    period_days = db.Column(db.Integer, default=30)
    calculated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<StockComparison {self.stock1_id} vs {self.stock2_id}>'

class Alert(db.Model):
    """Price alerts model"""
    __tablename__ = 'alerts'
    
    class AlertType(enum.Enum):
        PRICE_ABOVE = "price_above"
        PRICE_BELOW = "price_below"
        PERCENTAGE_CHANGE = "percentage_change"
        TECHNICAL_SIGNAL = "technical_signal"
        FORECAST_CHANGE = "forecast_change"
    
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False, index=True)
    alert_type = db.Column(db.Enum(AlertType), nullable=False)
    threshold_value = db.Column(db.Float, nullable=False)
    is_triggered = db.Column(db.Boolean, default=False)
    triggered_at = db.Column(db.DateTime)
    triggered_price = db.Column(db.Float)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Alert {self.stock_id} - {self.alert_type.value}>'

class AnalyticsReport(db.Model):
    """Analytics reports model"""
    __tablename__ = 'analytics_reports'
    
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False, index=True)
    
    # Performance metrics
    daily_return = db.Column(db.Float)
    weekly_return = db.Column(db.Float)
    monthly_return = db.Column(db.Float)
    yearly_return = db.Column(db.Float)
    
    # Risk metrics
    volatility_daily = db.Column(db.Float)
    volatility_annual = db.Column(db.Float)
    sharpe_ratio = db.Column(db.Float)
    max_drawdown = db.Column(db.Float)
    
    # Price targets
    target_price_12m = db.Column(db.Float)
    upside_potential = db.Column(db.Float)
    recommendation = db.Column(db.String(50))  # Buy, Hold, Sell
    
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<AnalyticsReport {self.stock_id}>'
