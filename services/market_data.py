"""Market data service: overview, history and closes with resilient caching.

Data comes from the provider chain in ``services.providers``. Every successful
result is also kept as a long-lived "last known good" snapshot (memory cache and
database) that is served with ``stale: true`` when all providers fail.
"""

import logging
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

import pandas as pd
from flask import current_app, has_app_context

from services import providers
from services.cache import get_app_cache
from services.providers.equities import ETF_PROXIES
from services.snapshots import load_snapshot, save_snapshot
from services.tick_store import freshness, latency_for, tick_store

logger = logging.getLogger(__name__)

OVERVIEW_TTL_SECONDS = 60
OVERVIEW_FAILURE_TTL_SECONDS = 30
HISTORY_TTL_SECONDS = 900
HISTORY_FAILURE_TTL_SECONDS = 60
MOMENTUM_TTL_SECONDS = 900
STALE_TTL_SECONDS = 7 * 24 * 3600
HISTORY_DAYS = 730
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^.=\-]{1,15}$")
MAX_WORKERS = 6
MIN_MOMENTUM_ASSETS = 3
DELAYED_WARNING = (
    "Some prices come from free sources that are delayed or end-of-day; "
    "check the badge on each asset."
)
SPARKLINE_POINTS = 30
MAX_TICK_AGE_SECONDS = 6 * 3600
OVERVIEW_KEY = "market:overview"
OVERVIEW_LKG_KEY = "market:overview:lkg"

ASSET_CATALOG: Dict[str, List[tuple]] = {
    "commodities": [
        ("GC=F", "Gold"),
        ("SI=F", "Silver"),
        ("CL=F", "Crude Oil"),
        ("BZ=F", "Brent Oil"),
        ("NG=F", "Natural Gas"),
        ("HG=F", "Copper"),
    ],
    "cryptocurrencies": [
        ("BTC-USD", "Bitcoin"),
        ("ETH-USD", "Ethereum"),
        ("DOGE-USD", "Dogecoin"),
        ("SOL-USD", "Solana"),
        ("XRP-USD", "XRP"),
        ("BNB-USD", "Binance Coin"),
        ("ADA-USD", "Cardano"),
    ],
    "stocks": [
        ("AAPL", "Apple"),
        ("MSFT", "Microsoft"),
        ("GOOGL", "Alphabet"),
        ("AMZN", "Amazon"),
        ("TSLA", "Tesla"),
        ("NVDA", "NVIDIA"),
        ("META", "Meta"),
    ],
    "indices": [
        ("^GSPC", "S&P 500"),
        ("^IXIC", "Nasdaq"),
        ("^DJI", "Dow Jones"),
    ],
}

ASSET_NAMES: Dict[str, str] = {
    symbol: name for assets in ASSET_CATALOG.values() for symbol, name in assets
}

# period label -> calendar days of the shared daily history to show (None = all)
PERIOD_CONFIG = {"1M": 30, "3M": 91, "6M": 182, "1Y": 365, "2Y": None}

LOCK_STRIPES = 64
_locks = [threading.Lock() for _ in range(LOCK_STRIPES)]


def _lock_for(key: str) -> threading.Lock:
    """Bounded pool of locks (striped by key) so memory cannot grow per symbol."""
    return _locks[zlib.crc32(key.encode()) % LOCK_STRIPES]


def is_valid_symbol(symbol: str) -> bool:
    return bool(SYMBOL_PATTERN.match(symbol or ""))


def all_symbols() -> List[str]:
    return list(ASSET_NAMES)


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_message(as_of: Optional[str]) -> str:
    """Human readable notice for stale data."""
    try:
        then = datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        minutes = max(0, int((datetime.now(timezone.utc) - then).total_seconds() // 60))
        age = (
            f"{minutes} minutes ago" if minutes < 120 else f"{minutes // 60} hours ago"
        )
    except (TypeError, ValueError):
        age = "earlier"
    return f"Showing data from {age} — live source unavailable"


# ------------------------------------------------------------------ overview


def _failed_asset(symbol: str, name: str) -> Dict:
    return {
        "symbol": symbol,
        "name": name,
        "price": None,
        "change": None,
        "change_pct": None,
        "sparkline": [],
        "source": None,
        "error": True,
    }


def _asset_from_quote(symbol: str, name: str, quote: Dict, source: str) -> Dict:
    price = round(float(quote["price"]), 4)
    change = round(float(quote.get("change") or 0.0), 4)
    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "change": change,
        "prev_close": round(price - change, 4),
        "change_pct": round(float(quote.get("change_pct") or 0.0), 2),
        "sparkline": [round(float(v), 4) for v in quote.get("sparkline") or []],
        "source": source,
        "as_of": quote.get("as_of") or "",
        "ts": quote.get("ts"),
        "derived_from_history": bool(quote.get("derived_from_history")),
    }


def _history_closes(cache, symbol: str) -> List[float]:
    """Closes from already cached history (fresh or last-known-good); no network."""
    entry = cache.get(f"hist:{symbol}") or cache.get(f"hist:lkg:{symbol}")
    records = (entry or {}).get("records") or []
    return [float(r["close"]) for r in records if r.get("close") is not None]


def _entry_source(cache, symbol: str) -> str:
    entry = cache.get(f"hist:{symbol}") or cache.get(f"hist:lkg:{symbol}") or {}
    return entry.get("source") or "history"


def quote_from_closes(closes: List[float]) -> Optional[Dict]:
    """Price, change and 30-point sparkline from closes (last vs previous)."""
    if not closes:
        return None
    price = closes[-1]
    previous = closes[-2] if len(closes) > 1 else price
    change = price - previous
    return {
        "price": price,
        "change": change,
        "change_pct": (change / previous * 100) if previous else 0.0,
        "sparkline": closes[-SPARKLINE_POINTS:],
        "as_of": "",
        "ts": None,
        "derived_from_history": True,
    }


def apply_tick(item: Dict, tick: Optional[Dict]) -> Dict:
    """Overlay a newer live tick on an overview item (change vs prev close)."""
    if not tick or item.get("error") and not item.get("prev_close"):
        return item
    if item.get("ts") and tick["ts"] <= item["ts"]:
        return item
    if time.time() - tick["ts"] > MAX_TICK_AGE_SECONDS:
        return item
    prev = item.get("prev_close")
    updated = {**item, "price": round(tick["price"], 4), "ts": tick["ts"]}
    updated["source"] = tick["source"]
    if prev:
        updated["change"] = round(tick["price"] - prev, 4)
        updated["change_pct"] = round((tick["price"] - prev) / prev * 100, 2)
    return updated


def decorate_asset(item: Dict) -> Dict:
    """Add the freshness badge (computed from the tick timestamp, at read time)."""
    if item.get("price") is None:
        return item
    return {**item, "freshness": freshness(item.get("ts"), item.get("source"))}


def _build_proxies(proxies: Dict) -> Dict[str, Dict]:
    """Labelled live ETF proxies; never presented as the underlying price."""
    result = {}
    for symbol, (etf, label) in ETF_PROXIES.items():
        if etf not in proxies:
            continue
        quote, source = proxies[etf]
        result[symbol] = {
            "etf": etf,
            "label": label,
            "price": round(float(quote["price"]), 4),
            "change_pct": round(float(quote.get("change_pct") or 0.0), 2),
            "source": source,
            "ts": quote.get("ts"),
        }
    return result


def build_overview(
    fetcher: Optional[Callable] = None, proxy_fetcher: Optional[Callable] = None
) -> Dict:
    """Overview of all known assets; failing assets never break the response."""
    fetcher = fetcher or providers.fetch_quotes
    proxy_fetcher = proxy_fetcher or providers.fetch_proxy_quotes
    try:
        quotes = fetcher(all_symbols())
    except Exception as exc:
        logger.error("Overview fetch failed: %s", type(exc).__name__)
        quotes = {}
    try:
        proxies = proxy_fetcher()
    except Exception as exc:
        logger.warning("Proxy quotes failed: %s", type(exc).__name__)
        proxies = {}

    cache = get_app_cache()
    overview: Dict = {}
    used: Dict[str, int] = {}
    failed: List[str] = []
    for category, assets in ASSET_CATALOG.items():
        items = []
        for symbol, name in assets:
            item = None
            try:
                quote, source = quotes[symbol]
                item = _asset_from_quote(symbol, name, quote, source)
            except (KeyError, TypeError, ValueError):
                item = None
            closes = _history_closes(cache, symbol)
            if item is None and closes:
                # a quote provider failed but history is cached: never blank
                quote = quote_from_closes(closes)
                item = _asset_from_quote(
                    symbol, name, quote, _entry_source(cache, symbol)
                )
            elif item is not None and len(item["sparkline"]) < 2 and closes:
                item["sparkline"] = [round(v, 4) for v in closes[-SPARKLINE_POINTS:]]
            if item is None:
                logger.debug("Overview data unavailable for %s", symbol)
                items.append(_failed_asset(symbol, name))
                failed.append(symbol)
                continue
            if item["source"] in ("history",) or item.get("derived_from_history"):
                item["ts"] = None
            items.append(item)
            used[item["source"]] = used.get(item["source"], 0) + 1
        overview[category] = items
    overview["proxies"] = _build_proxies(proxies or {})
    overview["timestamp"] = utc_now_iso()
    overview["as_of"] = overview["timestamp"]
    overview["stale"] = False
    overview["delayed_warning"] = DELAYED_WARNING
    overview["sources_status"] = {
        "used": used,
        "failed": failed,
        "providers": providers.sources_status(),
        "reasons": providers.failure_reasons(),
    }
    return overview


def _iter_assets(overview: Dict):
    for category in ASSET_CATALOG:
        for item in overview.get(category) or []:
            yield category, item


def _has_prices(overview: Optional[Dict]) -> bool:
    return bool(overview) and any(
        not item.get("error") for _, item in _iter_assets(overview)
    )


def _merge_stale(overview: Dict, previous: Optional[Dict]) -> None:
    """Fill failed assets from the last known good overview (flagged stale)."""
    if not _has_prices(previous):
        return
    old = {item["symbol"]: item for _, item in _iter_assets(previous)}
    for category in ASSET_CATALOG:
        merged = []
        for item in overview[category]:
            earlier = old.get(item["symbol"])
            if item.get("error") and earlier and not earlier.get("error"):
                item = {**earlier, "stale": True}
            merged.append(item)
        overview[category] = merged


def live_overview(overview: Dict) -> Dict:
    """Overview with the latest ticks applied and freshness badges attached.

    Cheap (memory only): used by the 5 second dashboard refresh.
    """
    result = dict(overview)
    for category in ASSET_CATALOG:
        result[category] = [
            decorate_asset(apply_tick(item, tick_store.get(item["symbol"])))
            for item in overview.get(category) or []
        ]
    delayed = any(
        item.get("freshness", {}).get("state") in ("delayed", "eod")
        for _, item in _iter_assets(result)
    )
    result["any_delayed"] = delayed
    return result


def get_overview(
    cache=None, fetcher: Optional[Callable] = None, force: bool = False
) -> Dict:
    """Overview with 60s caching and a stale last-known-good fallback."""
    cache = cache or get_app_cache()
    cached = None if force else cache.get(OVERVIEW_KEY)
    if cached is not None:
        return cached
    with _lock_for("overview"):
        cached = None if force else cache.get(OVERVIEW_KEY)
        if cached is not None:
            return cached
        overview = build_overview(fetcher)
        previous = cache.get(OVERVIEW_LKG_KEY) or load_snapshot("overview")
        if _has_prices(overview):
            _merge_stale(overview, previous)
            cache.set(OVERVIEW_LKG_KEY, overview, STALE_TTL_SECONDS)
            save_snapshot("overview", overview)
            cache.set(OVERVIEW_KEY, overview, OVERVIEW_TTL_SECONDS)
            return overview
        if _has_prices(previous):
            overview = {
                **previous,
                "stale": True,
                "as_of": previous.get("timestamp"),
                "sources_status": overview["sources_status"],
            }
        cache.set(OVERVIEW_KEY, overview, OVERVIEW_FAILURE_TTL_SECONDS)
        return overview


# ------------------------------------------------------------------- history


def _frame_to_records(frame: pd.DataFrame) -> List[Dict]:
    records = []
    for ts, row in frame.iterrows():
        records.append(
            {
                "date": pd.Timestamp(ts).isoformat(),
                "open": float(row.get("Open", row["Close"])),
                "high": float(row.get("High", row["Close"])),
                "low": float(row.get("Low", row["Close"])),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0) or 0),
            }
        )
    return records


def records_to_frame(records: List[Dict]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def _history_result(entry: Optional[Dict], stale: bool) -> Dict:
    if not entry:
        return {"frame": pd.DataFrame(), "source": None, "stale": False, "as_of": None}
    return {
        "frame": records_to_frame(entry["records"]),
        "source": entry.get("source"),
        "stale": stale,
        "as_of": entry.get("as_of"),
    }


def fetch_history(symbol: str, cache=None, force: bool = False) -> Dict:
    """Shared ~2y daily history of one symbol (cached 15 min, stale fallback).

    Returns {"frame", "source", "stale", "as_of"}; the frame has lower-case
    open/high/low/close/volume columns and is empty when nothing is available.
    """
    symbol = normalize_symbol(symbol)
    cache = cache or get_app_cache()
    key, lkg_key = f"hist:{symbol}", f"hist:lkg:{symbol}"
    cached = None if force else cache.get(key)
    if cached is not None:
        return _history_result(cached, False)
    with _lock_for(key):
        cached = None if force else cache.get(key)
        if cached is not None:
            return _history_result(cached, False)
        if force or cache.get(f"hist:fail:{symbol}") is None:
            frame, source = providers.get_history(symbol, HISTORY_DAYS)
            if not frame.empty:
                entry = {
                    "records": _frame_to_records(frame),
                    "source": source,
                    "as_of": utc_now_iso(),
                }
                cache.set(key, entry, HISTORY_TTL_SECONDS)
                cache.set(lkg_key, entry, STALE_TTL_SECONDS)
                save_snapshot(key, entry)
                return _history_result(entry, False)
            cache.set(f"hist:fail:{symbol}", True, HISTORY_FAILURE_TTL_SECONDS)
        entry = cache.get(lkg_key) or load_snapshot(key)
        return _history_result(entry, True)


def fetch_history_frame(symbol: str, cache=None) -> pd.DataFrame:
    """Lower-case OHLCV frame for one symbol (shared cached history)."""
    return fetch_history(symbol, cache)["frame"]


def fetch_closes(
    symbols: Sequence[str], days: Optional[int] = None, cache=None
) -> pd.DataFrame:
    """Close prices of many symbols from the shared history (failed omitted)."""
    symbols = list(dict.fromkeys(symbols))
    cache = cache or get_app_cache()
    app = current_app._get_current_object() if has_app_context() else None

    def load(symbol: str) -> pd.DataFrame:
        if app is None:
            return fetch_history_frame(symbol, cache)
        with app.app_context():
            return fetch_history_frame(symbol, cache)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        frames = list(pool.map(load, symbols))
    columns = {}
    for symbol, frame in zip(symbols, frames):
        if frame.empty:
            continue
        close = frame["close"].astype(float)
        if days:
            close = close[close.index >= close.index[-1] - pd.Timedelta(days=days)]
        columns[symbol] = close
    return pd.DataFrame(columns)


# ------------------------------------------------------------------ momentum


def cached_closes(cache) -> pd.DataFrame:
    """Closes of assets whose history is already cached (no new downloads)."""
    columns = {}
    for symbol in all_symbols():
        entry = cache.get(f"hist:{symbol}") or cache.get(f"hist:lkg:{symbol}")
        if entry and entry.get("records"):
            columns[symbol] = records_to_frame(entry["records"])["close"]
    return pd.DataFrame(columns)
