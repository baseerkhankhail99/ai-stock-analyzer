# AI Stock Analyzer

AI-powered stock and cryptocurrency analysis platform with market data retrieval, technical indicators, forecasting, analytics, and an interactive Dash dashboard.

> **Disclaimer:** This project is for educational and research purposes only. It is not financial advice. Forecasts and trading signals can be inaccurate; always perform your own research before making investment decisions.

## Features

- Flask REST API for market data and analysis
- Interactive Dash/Plotly dashboard
- Historical price and volume visualization
- Forecasting support with Prophet, statistical models, and machine-learning tooling
- Technical indicators including SMA, EMA, RSI, MACD, Bollinger Bands, and ATR
- Trading-signal aggregation
- Performance analytics such as volatility, Sharpe ratio, returns, and drawdown
- Stock and cryptocurrency symbol configuration
- PostgreSQL persistence
- Redis caching and Celery worker support
- Docker and Docker Compose development/deployment setup

## Project Structure

```text
.
├── app.py                  # Flask application entry point
├── config.py               # Environment-based application configuration
├── dashboard.py            # Dash/Plotly dashboard
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container image definition
├── docker-compose.yml      # PostgreSQL, Redis, API, dashboard, and worker services
├── .env.template           # Environment variable template
├── logs/                   # Runtime logs (created as needed)
└── tests/                  # Test suite, when present
```

## Requirements

### Local development

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- API credentials for the data providers you intend to use

### Docker

- Docker Engine
- Docker Compose v2

## Quick Start with Docker

1. Clone the repository and enter the project directory.

   ```bash
   git clone https://github.com/baseerkhankhail99/ai-stock-analyzer.git
   cd ai-stock-analyzer
   ```

2. Create a local environment file from the template.

   ```bash
   cp .env.template .env
   ```

3. Edit `.env` and add your API keys. Do not commit `.env` or real credentials.

4. Build and start the services.

   ```bash
   docker compose up --build
   ```

5. Open the services:

   - Flask API: `http://localhost:5000`
   - Dash dashboard: `http://localhost:8050`
   - PostgreSQL: `localhost:5432`
   - Redis: `localhost:6379`

6. Stop the services when finished.

   ```bash
   docker compose down
   ```

   Add `-v` only when you intentionally want to remove persisted database and Redis volumes:

   ```bash
   docker compose down -v
   ```

## Local Setup

1. Create and activate a virtual environment.

   ```bash
   python -m venv .venv
   source .venv/bin/activate       # macOS/Linux
   # .venv\\Scripts\\activate    # Windows PowerShell
   ```

2. Install dependencies.

   ```bash
   pip install -r requirements.txt
   ```

3. Create `.env` and configure the database, Redis instance, and provider credentials.

4. Start the Flask API.

   ```bash
   python app.py
   ```

5. In another terminal, start the dashboard.

   ```bash
   python dashboard.py
   ```

## Configuration

Configuration is loaded from environment variables through `config.py`. Common settings include:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Flask session and signing key |
| `DATABASE_URL` | PostgreSQL or test database connection string |
| `REDIS_URL` | Redis connection URL |
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage provider credential |
| `FINNHUB_API_KEY` | Finnhub provider credential |
| `POLYGON_API_KEY` | Polygon provider credential |
| `RAPID_API_KEY` | RapidAPI credential, when applicable |
| `STOCK_SYMBOLS` | Comma-separated stock symbols |
| `CRYPTO_SYMBOLS` | Comma-separated cryptocurrency symbols |
| `FORECAST_DAYS` | Forecast horizon in days |
| `LOOKBACK_DAYS` | Historical lookback window |
| `CORS_ORIGINS` | Comma-separated allowed origins |
| `LOG_LEVEL` | Application logging level |
| `DASH_PORT` | Dashboard port, when supported by the launcher |

Use strong, unique secrets in production and keep credentials outside source control.

## API Usage

The API is intended to expose endpoints for health checks, current prices, historical data, forecasts, technical signals, analytics, and symbol comparison. Example requests:

```bash
curl http://localhost:5000/api/health
curl http://localhost:5000/api/stocks/AAPL/price
curl "http://localhost:5000/api/stocks/AAPL/history?days=365"
curl http://localhost:5000/api/stocks/AAPL/analytics
curl http://localhost:5000/api/stocks/AAPL/signals
curl "http://localhost:5000/api/stocks/compare?symbol1=AAPL&symbol2=MSFT"
```

Check the route definitions in `app.py` for the authoritative endpoint list and response schemas.

## Dashboard

The dashboard provides tabs for:

- **Price Chart:** interactive historical candlestick data
- **Forecast:** model forecasts and confidence intervals
- **Technical Analysis:** aggregated signals, RSI, and MACD views
- **Analytics:** performance and risk statistics
- **Compare:** comparison metrics for two symbols

The dashboard expects the Flask API to be available at `http://localhost:5000/api` by default. If the API is hosted elsewhere, update the dashboard API base URL before deployment.

## Background Jobs

The Compose configuration includes a Celery worker for asynchronous jobs. Start it with:

```bash
docker compose up celery_worker
```

Ensure the referenced Celery application/module is present and configured before enabling production workers.

## Testing and Code Quality

Run tests with:

```bash
pytest
pytest --cov=.
```

Run formatting and lint checks with:

```bash
black .
isort .
flake8 .
pylint *.py
```

## Production Notes

- Replace all default passwords and secret values.
- Store API keys in a secrets manager or deployment platform secret store.
- Restrict `CORS_ORIGINS` to trusted domains.
- Use TLS termination through a reverse proxy or load balancer.
- Do not expose PostgreSQL or Redis publicly unless protected by network policy and authentication.
- Review provider rate limits and cache market data appropriately.
- Add migrations and run database initialization as part of deployment.
- Monitor API errors, background jobs, provider failures, and forecast quality.
- Pin and regularly audit dependencies before upgrading production images.

## Troubleshooting

### Database connection errors

Verify that PostgreSQL is running and that `DATABASE_URL` uses the correct host. In Docker Compose, the database hostname is `postgres`, not `localhost`.

### Redis connection errors

Verify that Redis is running and that the application uses `redis` as the hostname inside Compose networks.

### Missing market data

Check provider API keys, symbol formatting, provider rate limits, and the application logs under `logs/`.

### Dashboard cannot reach the API

Confirm the Flask API is running on port 5000 and that the dashboard's API base URL matches the environment where it is deployed.

## License

Add the project's license here before distributing or deploying it commercially.

# CI/CD Test

