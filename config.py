import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base configuration"""
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'postgresql://localhost/stock_analyzer')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Redis
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    
    # API Keys
    ALPHA_VANTAGE_API_KEY = os.getenv('ALPHA_VANTAGE_API_KEY')
    FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY')
    RAPID_API_KEY = os.getenv('RAPID_API_KEY')
    
    # Stock Configuration
    STOCK_SYMBOLS = os.getenv('STOCK_SYMBOLS', 'NVDA,TTWO,EA,NTES,RBLX,SONY,BTC-USD').split(',')
    
    # Forecasting
    FORECAST_DAYS = int(os.getenv('FORECAST_DAYS', 30))
    FORECAST_INTERVAL = os.getenv('FORECAST_INTERVAL', 'daily')
    
    # Cache
    CACHE_TIMEOUT = 300  # 5 minutes
    
    # Server
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', 5000))
    
    # CORS
    CORS_ORIGINS = ['http://localhost:3000', 'http://localhost:5000']

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False

class TestingConfig(Config):
    """Testing configuration"""
    DEBUG = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
