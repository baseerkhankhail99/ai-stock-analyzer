"""In-process payload builders shared by the REST API and the dashboard.

Each function returns plain dicts (and an HTTP-like status where relevant) so
the dashboard can call them directly instead of making HTTP requests to itself.
"""

import json
import logging
from datetime import timedelta
from typing import Dict, Optional, Sequence, Tuple

from services import market_analysis, market_data, providers
from services.cache import get_app_cache
from services.forecast_engine import StockForecastEngine

logger = logging.getLogger(__name__)

FORECAST_CACHE_TTL_SECONDS = 600
HEALTH_CACHE_TTL_SECONDS = 60
MAX_FORECAST_DAYS = 90
MAX_COMPARE_SYMBOLS = 5
MIN_INDICATOR_ROWS = 30
COMPARE_PERIODS = ("1M", "6M", "1Y", "2Y")
MOMENTUM_UNAVAILABLE = {
    "available": False,
    "message": "Market momentum is not available yet. It is built from data "
    "that is already loaded; try again shortly.",
}

forecast_engine = StockForecastEngine()


def overview_payload() -> Dict:
    return market_data.get_overview()


def momentum_payload() -> Dict:
    """Momentum from already-fetched data; never raises, never needs a download."""
    cache = get_app_cache()
    cached = cache.get("market:momentum")
    if cached is not None:
        return cached
    try:
        closes = market_data.cached_closes(cache)
        summary = (
            market_analysis.momentum_summary(closes)
            if closes.shape[1] >= market_data.MIN_MOMENTUM_ASSETS
            else {}
        )
        if not summary:
            overview = cache.get(market_data.OVERVIEW_KEY) or cache.get(
                market_data.OVERVIEW_LKG_KEY
            )
            summary = market_analysis.momentum_from_overview(overview or {})
    except Exception as exc:
        logger.error("Momentum computation failed: %s", exc, exc_info=True)
        summary = {}
    if not summary:
        return dict(MOMENTUM_UNAVAILABLE)
    summary["available"] = True
    cache.set("market:momentum", summary, market_data.MOMENTUM_TTL_SECONDS)
    return summary


def history_payload(symbol: str, label: str = "1Y") -> Tuple[Dict, int]:
    """OHLCV plus indicators for a period label (see PERIOD_CONFIG)."""
    symbol = market_data.normalize_symbol(symbol)
    if not market_data.is_valid_symbol(symbol):
        return {"error": "Invalid symbol"}, 400
    label = (label or "1Y").upper()
    if label not in market_data.PERIOD_CONFIG:
        return {"error": "Invalid period"}, 400
    result = market_data.fetch_history(symbol)
    frame = result["frame"]
    if frame.empty:
        return {"error": "No data found"}, 404
    if len(frame) >= MIN_INDICATOR_ROWS:
        frame = market_analysis.add_indicators(frame)
    keep_days = market_data.PERIOD_CONFIG[label]
    if keep_days:
        frame = frame[frame.index >= frame.index[-1] - timedelta(days=keep_days)]
    has_volume = bool((frame["volume"] > 0).any())
    frame = frame.reset_index().rename(columns={"index": "date"})
    frame["date"] = frame["date"].map(lambda ts: ts.isoformat())
    records = json.loads(frame.to_json(orient="records"))
    body = {
        "symbol": symbol,
        "period": label,
        "data": records,
        "source": result["source"],
        "stale": result["stale"],
        "as_of": result["as_of"],
        "has_volume": has_volume,
        "interval": "1d",
    }
    if result["stale"]:
        body["message"] = market_data.age_message(result["as_of"])
    return body, 200


def analysis_payload(symbol: str) -> Tuple[Dict, int]:
    symbol = market_data.normalize_symbol(symbol)
    if not market_data.is_valid_symbol(symbol):
        return {"error": "Invalid symbol"}, 400
    result = market_data.fetch_history(symbol)
    frame = result["frame"]
    if frame.empty:
        return {"error": "No data found"}, 404
    analytics = market_analysis.analytics_metrics(frame["close"])
    if not analytics:
        return {"error": "Not enough data"}, 404
    return (
        {
            "symbol": symbol,
            "signal": market_analysis.trading_signal(frame),
            "analytics": analytics,
            "source": result["source"],
            "stale": result["stale"],
            "as_of": result["as_of"],
        },
        200,
    )


def compare_payload(symbols: Sequence[str], label: str = "1Y") -> Tuple[Dict, int]:
    symbols = list(dict.fromkeys(market_data.normalize_symbol(s) for s in symbols if s))
    if len(symbols) < 2 or len(symbols) > MAX_COMPARE_SYMBOLS:
        return {"error": f"Provide 2-{MAX_COMPARE_SYMBOLS} symbols"}, 400
    if not all(market_data.is_valid_symbol(s) for s in symbols):
        return {"error": "Invalid symbol"}, 400
    label = (label or "1Y").upper()
    if label not in COMPARE_PERIODS:
        return {"error": "Invalid period"}, 400
    cache = get_app_cache()
    cache_key = f"compare:{','.join(symbols)}:{label}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, 200
    closes = market_data.fetch_closes(symbols, market_data.PERIOD_CONFIG[label], cache)
    if symbols[0] not in closes.columns or closes.shape[1] < 2:
        return {"error": "Unable to compare the requested assets"}, 502
    result = market_analysis.compare_assets(closes, symbols[0])
    result["missing"] = [s for s in symbols if s not in closes.columns]
    cache.set(cache_key, result, market_data.HISTORY_TTL_SECONDS)
    return result, 200


def _jsonable(data):
    return json.loads(
        json.dumps(
            data, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)
        )
    )


def forecast_payload(symbol: str, days: Optional[int] = 30) -> Tuple[Dict, int]:
    """On-demand forecast with AI insight (cached 10 minutes)."""
    symbol = market_data.normalize_symbol(symbol)
    if not market_data.is_valid_symbol(symbol):
        return {"error": "Invalid symbol"}, 400
    days = max(1, min(int(days or 30), MAX_FORECAST_DAYS))
    cache = get_app_cache()
    cache_key = f"forecast:{symbol}:{days}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, 200
    forecast_data = forecast_engine.generate_all_forecasts(symbol, days)
    if not forecast_data or not any(
        forecast_data.get(m) for m in ("ensemble", "arima", "prophet")
    ):
        return {"error": "Unable to compute forecast"}, 502
    history = forecast_engine.get_historical_data(symbol, days=365)
    forecast_data["days"] = days
    forecast_data["insight"] = market_analysis.generate_insight(
        forecast_data, history, days, symbol
    )
    payload = _jsonable(forecast_data)
    cache.set(cache_key, payload, FORECAST_CACHE_TTL_SECONDS)
    return payload, 200


def health_data_payload(include_alpha_vantage: bool = False) -> Dict:
    """Per-provider diagnostics (ok/failed, latency, error); never exposes keys."""
    cache = get_app_cache()
    key = f"health:data:{include_alpha_vantage}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    report = {
        "timestamp": market_data.utc_now_iso(),
        "checks": providers.run_diagnostics(include_alpha_vantage),
        "providers": providers.sources_status(),
    }
    cache.set(key, report, HEALTH_CACHE_TTL_SECONDS)
    return report
