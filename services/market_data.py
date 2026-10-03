"""Batch market data helpers (overview, history, multi-symbol closes)."""

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
OVERVIEW_TTL_SECONDS = 60
HISTORY_TTL_SECONDS = 900
DELAYED_WARNING = "Data may be delayed up to 15 minutes"

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

# period label -> (yfinance period, interval, calendar days to keep or None)
PERIOD_CONFIG = {
    "1D": ("1d", "5m", None),
    "5D": ("5d", "30m", None),
    "1M": ("2y", "1d", 30),
    "6M": ("2y", "1d", 182),
    "1Y": ("2y", "1d", 365),
    "5Y": ("5y", "1wk", None),
}


def all_symbols() -> List[str]:
    return list(ASSET_NAMES)


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def download_prices(symbols: Sequence[str], period: str, interval: str):
    """Single batched yfinance request for many symbols."""
    return yf.download(
        tickers=list(symbols),
        period=period,
        interval=interval,
        group_by="ticker",
        progress=False,
        threads=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def extract_frame(raw, symbol: str) -> pd.DataFrame:
    """Return the OHLCV frame of one symbol from a batch download result."""
    if raw is None or getattr(raw, "empty", True):
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if symbol in raw.columns.get_level_values(0):
            frame = raw[symbol]
        elif symbol in raw.columns.get_level_values(1):
            frame = raw.xs(symbol, axis=1, level=1)
        else:
            return pd.DataFrame()
    else:
        frame = raw
    if "Close" not in frame.columns:
        return pd.DataFrame()
    frame = frame.dropna(subset=["Close"]).copy()
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_localize(None)
    return frame


def _failed_asset(symbol: str, name: str) -> Dict:
    return {
        "symbol": symbol,
        "name": name,
        "price": None,
        "change": None,
        "change_pct": None,
        "sparkline": [],
        "error": True,
    }


def _asset_snapshot(raw, symbol: str, name: str) -> Dict:
    frame = extract_frame(raw, symbol)
    closes = [float(v) for v in frame["Close"].tolist()] if not frame.empty else []
    if not closes:
        raise ValueError("no price data")
    price = closes[-1]
    previous = closes[-2] if len(closes) > 1 else price
    change = price - previous
    change_pct = (change / previous * 100) if previous else 0.0
    return {
        "symbol": symbol,
        "name": name,
        "price": round(price, 4),
        "change": round(change, 4),
        "change_pct": round(change_pct, 2),
        "sparkline": [round(v, 4) for v in closes[-5:]],
    }


def build_overview(downloader: Optional[Callable] = None) -> Dict:
    """Overview of all known assets; failing assets never break the response."""
    downloader = downloader or download_prices
    try:
        raw = downloader(all_symbols(), "5d", "1d")
    except Exception as exc:
        logger.error("Batch overview download failed: %s", exc)
        raw = None

    overview: Dict = {}
    for category, assets in ASSET_CATALOG.items():
        items = []
        for symbol, name in assets:
            try:
                items.append(_asset_snapshot(raw, symbol, name))
            except Exception as exc:
                logger.warning("Overview data unavailable for %s: %s", symbol, exc)
                items.append(_failed_asset(symbol, name))
        overview[category] = items
    overview["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    overview["delayed_warning"] = DELAYED_WARNING
    return overview


def fetch_closes(
    symbols: Sequence[str], period: str = "1y", interval: str = "1d"
) -> pd.DataFrame:
    """Close prices of many symbols from one batch request (failed ones omitted)."""
    symbols = list(dict.fromkeys(symbols))
    try:
        raw = download_prices(symbols, period, interval)
    except Exception as exc:
        logger.error("Batch close download failed: %s", exc)
        return pd.DataFrame()
    columns = {}
    for symbol in symbols:
        frame = extract_frame(raw, symbol)
        if not frame.empty:
            columns[symbol] = frame["Close"].astype(float)
    return pd.DataFrame(columns)


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


def fetch_history_frame(
    symbol: str, period: str = "2y", interval: str = "1d", cache=None
) -> pd.DataFrame:
    """Lower-case OHLCV frame for one symbol, cached for 15 minutes."""
    symbol = normalize_symbol(symbol)
    key = f"hist:{symbol}:{period}:{interval}"
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return records_to_frame(cached)

    frame = pd.DataFrame()
    try:
        frame = extract_frame(download_prices([symbol], period, interval), symbol)
    except Exception as exc:
        logger.warning("Batch history failed for %s: %s", symbol, exc)
    if frame.empty:
        try:
            history = yf.Ticker(symbol).history(
                period=period, interval=interval, timeout=REQUEST_TIMEOUT_SECONDS
            )
            frame = extract_frame(history, symbol)
        except Exception as exc:
            logger.error("History unavailable for %s: %s", symbol, exc)
    if frame.empty:
        return pd.DataFrame()

    records = _frame_to_records(frame)
    if cache is not None:
        cache.set(key, records, HISTORY_TTL_SECONDS)
    return records_to_frame(records)
