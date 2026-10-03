"""Providers for stocks, indices and commodities."""

import io
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Sequence

import pandas as pd

from services.providers.base import (
    BROWSER_USER_AGENT,
    NoData,
    Provider,
    ProviderError,
    RateLimited,
    TokenBucket,
    breaker,
    build_frame,
    http_get,
    is_crypto,
)
from services.providers.base import json_body as _json
from services.providers.base import quote_from_frame

FINNHUB_BASE = "https://finnhub.io/api/v1"
STOOQ_CSV_URL = "https://stooq.com/q/d/l/"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
ALPHA_VANTAGE_DAILY_LIMIT = 20  # free tier allows 25/day; keep a safety margin
FINNHUB_CANDLE_COOLDOWN_SECONDS = 3600
FINNHUB_CALLS_PER_MINUTE = 55  # free tier allows 60
FINNHUB_QUOTE_CACHE_SECONDS = 8
TWELVEDATA_URL = "https://api.twelvedata.com"
TWELVEDATA_CALLS_PER_MINUTE = 7  # free tier allows 8
YAHOO_CALLS_PER_MINUTE = 40
YAHOO_INTRADAY = {"1D": ("1d", "5m"), "5D": ("5d", "15m")}

# Live ETF proxies (via Finnhub) for assets without a free real-time feed.
# They are shown separately and labelled; never as the underlying price.
ETF_PROXIES: Dict[str, tuple] = {
    "^GSPC": ("SPY", "S&P 500 (via SPY, live)"),
    "^IXIC": ("QQQ", "Nasdaq (via QQQ, live)"),
    "^DJI": ("DIA", "Dow Jones (via DIA, live)"),
    "GC=F": ("GLD", "Gold (via GLD, live)"),
    "SI=F": ("SLV", "Silver (via SLV, live)"),
    "CL=F": ("USO", "Crude Oil (via USO, live)"),
}

US_TICKER = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")

# Stooq symbols for non-stock assets (stocks use "<ticker>.us").
STOOQ_SYMBOLS: Dict[str, str] = {
    "^GSPC": "^spx",
    "^IXIC": "^ndq",
    "^DJI": "^dji",
    "GC=F": "xauusd",
    "SI=F": "xagusd",
    "CL=F": "cl.f",
    "BZ=F": "lco.f",
    "NG=F": "ng.f",
    "HG=F": "hg.f",
}


def is_us_ticker(symbol: str) -> bool:
    return bool(US_TICKER.match(symbol or "")) and not is_crypto(symbol)


def _start_date(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=int(days))


# ------------------------------------------------------------------ Finnhub


class FinnhubProvider(Provider):
    name = "finnhub"
    label = "Finnhub"
    freshness = "real-time US quote (free key)"
    latency = "realtime"

    def __init__(self):
        self.limiter = TokenBucket(FINNHUB_CALLS_PER_MINUTE, 10)
        self._quotes: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(os.getenv("FINNHUB_API_KEY"))

    def supports(self, symbol: str) -> bool:
        return is_us_ticker(symbol)

    @staticmethod
    def _headers() -> Dict[str, str]:
        return {"X-Finnhub-Token": os.getenv("FINNHUB_API_KEY", "")}

    def get_quote(self, symbol: str) -> Dict:
        with self._lock:
            cached = self._quotes.get(symbol)
        if cached and time.monotonic() - cached[0] < FINNHUB_QUOTE_CACHE_SECONDS:
            return dict(cached[1])
        response = http_get(
            f"{FINNHUB_BASE}/quote",
            params={"symbol": symbol},
            headers=self._headers(),
            limiter=self.limiter,
        )
        quote = parse_finnhub_quote(_json(response))
        with self._lock:
            self._quotes[symbol] = (time.monotonic(), quote)
        return dict(quote)

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        if breaker.is_open("finnhub:candles"):
            raise NoData("candles unavailable on this plan")
        now = datetime.now(timezone.utc)
        try:
            response = http_get(
                f"{FINNHUB_BASE}/stock/candle",
                params={
                    "symbol": symbol,
                    "resolution": "D",
                    "from": int(_start_date(days).timestamp()),
                    "to": int(now.timestamp()),
                },
                headers=self._headers(),
            )
        except RateLimited:
            # candles are premium on free keys (403); keep the quote endpoint alive
            breaker.trip("finnhub:candles", FINNHUB_CANDLE_COOLDOWN_SECONDS)
            raise NoData("candles unavailable on this plan") from None
        return parse_finnhub_candles(_json(response))


def parse_finnhub_quote(data) -> Dict:
    price = data.get("c") if isinstance(data, dict) else None
    if not price:
        raise NoData("no quote")
    stamp = data.get("t") or None
    as_of = (
        datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M")
        if stamp
        else ""
    )
    return {
        "price": float(price),
        "change": float(data.get("d") or 0.0),
        "change_pct": float(data.get("dp") or 0.0),
        "sparkline": [],
        "as_of": as_of,
        "ts": float(stamp) if stamp else None,
    }


def parse_finnhub_candles(data) -> pd.DataFrame:
    if not isinstance(data, dict) or data.get("s") != "ok" or not data.get("t"):
        raise NoData("no candle data")
    rows = [
        {
            "Date": pd.Timestamp(ts, unit="s"),
            "Open": o,
            "High": h,
            "Low": low,
            "Close": c,
            "Volume": v,
        }
        for ts, o, h, low, c, v in zip(
            data["t"], data["o"], data["h"], data["l"], data["c"], data["v"]
        )
    ]
    frame = build_frame(rows)
    if frame.empty:
        raise NoData("no candle data")
    return frame


# -------------------------------------------------------------------- Stooq


def stooq_symbol(symbol: str):
    symbol = (symbol or "").upper()
    if symbol in STOOQ_SYMBOLS:
        return STOOQ_SYMBOLS[symbol]
    if is_us_ticker(symbol):
        return f"{symbol.lower().replace('.', '-')}.us"
    return None


def parse_stooq_csv(text: str) -> pd.DataFrame:
    """Parse Stooq CSV; "No data", empty bodies and HTML pages raise NoData."""
    body = (text or "").strip()
    if not body.lower().startswith("date,"):
        if "apikey" in body.lower():
            raise NoData("stooq now requires an apikey (CSV download blocked)")
        if body.lstrip().lower().startswith("<"):
            raise NoData("stooq returned an HTML page instead of CSV")
        raise NoData("stooq returned no data")
    frame = pd.read_csv(io.StringIO(body))
    if frame.empty or "Close" not in frame.columns:
        raise NoData("stooq returned no data")
    frame = frame.rename(columns=str.title)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    result = build_frame(frame.dropna(subset=["Date"]).to_dict("records"))
    if result.empty:
        raise NoData("stooq returned no data")
    return result


class StooqProvider(Provider):
    name = "stooq"
    label = "Stooq"
    freshness = "end-of-day"
    latency = "eod"

    def supports(self, symbol: str) -> bool:
        return stooq_symbol(symbol) is not None

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        start = _start_date(days)
        response = http_get(
            STOOQ_CSV_URL,
            params={
                "s": stooq_symbol(symbol),
                "i": "d",
                "d1": start.strftime("%Y%m%d"),
                "d2": datetime.now(timezone.utc).strftime("%Y%m%d"),
            },
            headers={"User-Agent": BROWSER_USER_AGENT},
        )
        return parse_stooq_csv(response.text)


# ------------------------------------------------------------ Alpha Vantage


class _DailyQuota:
    def __init__(self, limit: int):
        self.limit = limit
        self._day = None
        self._used = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        today = datetime.now(timezone.utc).date()
        with self._lock:
            if self._day != today:
                self._day, self._used = today, 0
            if self._used >= self.limit:
                return False
            self._used += 1
            return True


alpha_vantage_quota = _DailyQuota(ALPHA_VANTAGE_DAILY_LIMIT)


def parse_alpha_vantage_daily(data) -> pd.DataFrame:
    if not isinstance(data, dict):
        raise NoData("unexpected response")
    if data.get("Note") or data.get("Information"):
        raise RateLimited("Alpha Vantage limit reached")
    series = data.get("Time Series (Daily)")
    if not series:
        raise NoData("no data")
    rows = [
        {
            "Date": day,
            "Open": values.get("1. open"),
            "High": values.get("2. high"),
            "Low": values.get("3. low"),
            "Close": values.get("4. close"),
            "Volume": values.get("5. volume"),
        }
        for day, values in series.items()
    ]
    frame = build_frame(rows)
    if frame.empty:
        raise NoData("no data")
    return frame


class AlphaVantageProvider(Provider):
    """Detail views only: the free key allows ~25 calls a day."""

    name = "alphavantage"
    label = "Alpha Vantage"
    detail_only = True
    freshness = "end-of-day"
    latency = "eod"

    def available(self) -> bool:
        return bool(os.getenv("ALPHA_VANTAGE_API_KEY"))

    def supports(self, symbol: str) -> bool:
        return is_us_ticker(symbol)

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        if not alpha_vantage_quota.take():
            raise NoData("daily quota reserved")
        response = http_get(
            ALPHA_VANTAGE_URL,
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "outputsize": "compact",
                "apikey": os.getenv("ALPHA_VANTAGE_API_KEY", ""),
            },
        )
        return parse_alpha_vantage_daily(_json(response))


# -------------------------------------------------------------------- Yahoo


def yahoo_range(days: int) -> str:
    for limit, label in ((30, "1mo"), (90, "3mo"), (180, "6mo"), (365, "1y")):
        if days <= limit:
            return label
    return "2y" if days <= 730 else "5y"


def parse_yahoo_chart(data) -> pd.DataFrame:
    try:
        result = data["chart"]["result"][0]
        stamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        raise NoData("no chart data") from None
    rows = [
        {
            "Date": pd.Timestamp(ts, unit="s"),
            "Open": quote.get("open", [None] * len(stamps))[i],
            "High": quote.get("high", [None] * len(stamps))[i],
            "Low": quote.get("low", [None] * len(stamps))[i],
            "Close": quote.get("close", [None] * len(stamps))[i],
            "Volume": quote.get("volume", [None] * len(stamps))[i],
        }
        for i, ts in enumerate(stamps)
    ]
    frame = build_frame(rows)
    if frame.empty:
        raise NoData("no chart data")
    return frame


def parse_yahoo_quote(data) -> Dict:
    """Quote from the chart payload: market price + its timestamp (``meta``)."""
    frame = parse_yahoo_chart(data)
    meta = data["chart"]["result"][0].get("meta") or {}
    quote = quote_from_frame(frame)
    price = meta.get("regularMarketPrice")
    stamp = meta.get("regularMarketTime")
    if price:
        closes = [float(v) for v in frame["Close"].dropna()]
        bar_day = pd.Timestamp(frame.index[-1]).date()
        market_day = (
            datetime.fromtimestamp(stamp, timezone.utc).date() if stamp else None
        )
        previous = (
            closes[-2] if bar_day == market_day and len(closes) > 1 else closes[-1]
        )
        quote.update(
            price=float(price),
            change=float(price) - previous,
            change_pct=(float(price) - previous) / previous * 100 if previous else 0.0,
        )
        quote["sparkline"] = closes[:-1][-29:] + [float(price)]
    if stamp:
        quote["ts"] = float(stamp)
        quote["as_of"] = datetime.fromtimestamp(stamp, timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )
    return quote


def parse_yahoo_intraday(data) -> pd.DataFrame:
    try:
        result = data["chart"]["result"][0]
        stamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        raise NoData("no chart data") from None
    frame = pd.DataFrame(
        {
            "Open": quote.get("open"),
            "High": quote.get("high"),
            "Low": quote.get("low"),
            "Close": quote.get("close"),
            "Volume": quote.get("volume"),
        },
        index=pd.to_datetime(stamps, unit="s"),
    )
    frame = frame.dropna(subset=["Close"])
    frame["Volume"] = frame["Volume"].fillna(0.0)
    if frame.empty:
        raise NoData("no chart data")
    frame.index.name = "Date"
    return frame.astype(float)


class YahooChartProvider(Provider):
    """Direct Yahoo chart endpoint (single symbol, browser-like User-Agent)."""

    name = "yahoo"
    label = "Yahoo Finance"
    freshness = "delayed up to 15 min"
    latency = "delayed"
    intraday = True

    def __init__(self):
        self.limiter = TokenBucket(YAHOO_CALLS_PER_MINUTE, 15)

    def _chart(self, symbol: str, params: Dict):
        response = http_get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params=params,
            headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "application/json"},
            limiter=self.limiter,
        )
        return _json(response)

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        return parse_yahoo_chart(
            self._chart(symbol, {"range": yahoo_range(days), "interval": "1d"})
        )

    def get_quote(self, symbol: str) -> Dict:
        return parse_yahoo_quote(
            self._chart(symbol, {"range": "1mo", "interval": "1d"})
        )

    def get_intraday(self, symbol: str, label: str) -> pd.DataFrame:
        span, interval = YAHOO_INTRADAY[label]
        return parse_yahoo_intraday(
            self._chart(symbol, {"range": span, "interval": interval})
        )


# --------------------------------------------------------------- Twelve Data


def parse_twelvedata_series(data) -> pd.DataFrame:
    if not isinstance(data, dict):
        raise NoData("unexpected response")
    if data.get("code") == 429:
        raise RateLimited("Twelve Data limit reached")
    values = data.get("values")
    if data.get("status") == "error" or not values:
        raise NoData(str(data.get("message") or "no data")[:80])
    rows = [
        {
            "Date": v.get("datetime"),
            "Open": v.get("open"),
            "High": v.get("high"),
            "Low": v.get("low"),
            "Close": v.get("close"),
            "Volume": v.get("volume"),
        }
        for v in values
    ]
    frame = build_frame(rows)
    if frame.empty:
        raise NoData("no data")
    return frame


class TwelveDataProvider(Provider):
    """Optional provider; needs the free ``TWELVEDATA_API_KEY``."""

    name = "twelvedata"
    label = "Twelve Data"
    freshness = "delayed / end-of-day (free tier)"
    latency = "eod"

    def __init__(self):
        self.limiter = TokenBucket(TWELVEDATA_CALLS_PER_MINUTE, 3)

    def available(self) -> bool:
        return bool(os.getenv("TWELVEDATA_API_KEY"))

    def supports(self, symbol: str) -> bool:
        return is_us_ticker(symbol)

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        response = http_get(
            f"{TWELVEDATA_URL}/time_series",
            params={
                "symbol": symbol,
                "interval": "1day",
                "outputsize": min(max(int(days), 2), 500),
                "apikey": os.getenv("TWELVEDATA_API_KEY", ""),
            },
            limiter=self.limiter,
        )
        return parse_twelvedata_series(_json(response))


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in text or "too many requests" in text or "rate limit" in text


def _yfinance_period(days: int) -> str:
    return yahoo_range(days)


class YFinanceProvider(Provider):
    """Last resort. Imported lazily so the package works without yfinance."""

    name = "yfinance"
    label = "yfinance"
    freshness = "delayed up to 15 min"
    latency = "delayed"

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        import yfinance as yf

        try:
            raw = yf.Ticker(symbol).history(
                period=_yfinance_period(days), interval="1d", timeout=8
            )
        except Exception as exc:
            if _is_rate_limit(exc):
                raise RateLimited("rate limited") from None
            raise ProviderError(f"yfinance failed ({type(exc).__name__})") from None
        return frame_from_yfinance(raw, symbol)

    def get_quotes(self, symbols: Sequence[str]) -> Dict[str, Dict]:
        """One batched download for many symbols."""
        import yfinance as yf

        symbols = list(symbols)
        try:
            raw = yf.download(
                tickers=symbols,
                period="1mo",
                interval="1d",
                group_by="ticker",
                progress=False,
                threads=True,
                timeout=8,
            )
        except Exception as exc:
            if _is_rate_limit(exc):
                raise RateLimited("rate limited") from None
            raise ProviderError(f"yfinance failed ({type(exc).__name__})") from None
        quotes = {}
        for symbol in symbols:
            try:
                quotes[symbol] = quote_from_frame(frame_from_yfinance(raw, symbol))
            except ProviderError:
                continue
        if not quotes:
            # yfinance swallows rate-limit errors and returns an empty frame
            raise RateLimited("empty batch result")
        return quotes


def frame_from_yfinance(raw, symbol: str) -> pd.DataFrame:
    """OHLCV frame of one symbol from a yfinance result (single or batch)."""
    if raw is None or getattr(raw, "empty", True):
        raise NoData("no price data")
    frame = raw
    if isinstance(raw.columns, pd.MultiIndex):
        if symbol in raw.columns.get_level_values(0):
            frame = raw[symbol]
        elif symbol in raw.columns.get_level_values(1):
            frame = raw.xs(symbol, axis=1, level=1)
        else:
            raise NoData("symbol missing")
    if "Close" not in frame.columns:
        raise NoData("no price data")
    frame = frame.dropna(subset=["Close"]).copy()
    frame = frame.reset_index().rename(columns={frame.index.name or "index": "Date"})
    if "Date" not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: "Date"})
    result = build_frame(frame.to_dict("records"))
    if result.empty:
        raise NoData("no price data")
    return result
