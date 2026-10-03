import os
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv()

DEFAULT_SQLITE_URL = "sqlite:///stock_analyzer.db"
LOCAL_DB_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def normalize_database_url(url):
    """Return a SQLAlchemy 2 compatible database URL.

    - Falls back to a local SQLite file when no URL is provided.
    - Rewrites the legacy ``postgres://`` scheme to ``postgresql://``.
    - Adds ``sslmode=require`` for remote PostgreSQL hosts (Neon, Supabase)
      unless the URL already specifies an ``sslmode``.
    """
    if not url or not url.strip():
        return DEFAULT_SQLITE_URL

    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    parts = urlsplit(url)
    if parts.scheme.startswith("postgresql"):
        query = parse_qs(parts.query, keep_blank_values=True)
        if "sslmode" not in query and (parts.hostname or "") not in LOCAL_DB_HOSTS:
            query["sslmode"] = ["require"]
            url = urlunsplit(parts._replace(query=urlencode(query, doseq=True)))

    return url


def build_engine_options(database_url):
    """Small connection pool for free-tier PostgreSQL; none for SQLite."""
    if database_url.startswith("sqlite"):
        return {}
    return {
        "pool_size": 3,
        "max_overflow": 2,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }


class Config:
    """Base configuration"""

    # Flask
    # Must be set via env in production (auth.init_auth refuses weak keys there)
    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    CSRF_ENABLED = True
    DEBUG = False
    TESTING = False

    # Database
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "postgresql://stock_user:stock_password@localhost:5432/stock_analyzer",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "pool_recycle": 3600,
    }

    # Redis
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # CORS
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

    # API Keys
    ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
    FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
    POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
    RAPID_API_KEY = os.getenv("RAPID_API_KEY")

    # Stock symbols to track
    STOCK_SYMBOLS = [
        "AAPL",
        "MSFT",
        "GOOGL",
        "AMZN",
        "TSLA",
        "META",
        "NVDA",
        "JPM",
        "V",
        "JNJ",
        "WMT",
        "PG",
        "BAC",
        "XOM",
        "CVX",
    ]

    # Crypto symbols
    CRYPTO_SYMBOLS = [
        "BTC-USD",
        "ETH-USD",
        "BNB-USD",
        "XRP-USD",
        "ADA-USD",
        "SOL-USD",
        "DOGE-USD",
        "DOT-USD",
    ]

    # Forecasting
    FORECAST_DAYS = int(os.getenv("FORECAST_DAYS", 30))
    LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", 60))

    # Technical Analysis
    SMA_PERIODS = [20, 50, 200]
    EMA_PERIODS = [12, 26]
    RSI_PERIOD = 14
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    BOLLINGER_PERIOD = 20
    BOLLINGER_STD = 2
    ATR_PERIOD = 14

    # Caching
    CACHE_DEFAULT_TIMEOUT = 300
    CACHE_REDIS_URL = os.getenv("REDIS_URL") or None

    # Session
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_REFRESH_EACH_REQUEST = True

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Batch processing
    BATCH_SIZE = int(os.getenv("BATCH_SIZE", 100))
    MAX_WORKERS = int(os.getenv("MAX_WORKERS", 4))


class DevelopmentConfig(Config):
    """Development configuration"""

    DEBUG = True
    TESTING = False
    LOG_LEVEL = "DEBUG"


class ProductionConfig(Config):
    """Production configuration"""

    DEBUG = False
    TESTING = False
    LOG_LEVEL = "INFO"


class TestingConfig(Config):
    """Testing configuration"""

    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    REDIS_URL = None
    CACHE_REDIS_URL = None
    CSRF_ENABLED = False
    SECRET_KEY = "testing-secret-key-not-for-production"


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
