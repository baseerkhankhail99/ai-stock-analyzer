"""Crypto providers: Coinbase and Kraken (live), CoinGecko, Binance.US."""

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Sequence

import pandas as pd

from services.providers.base import (
    NoData,
    Provider,
    ProviderError,
    TokenBucket,
    build_frame,
    http_get,
)
from services.providers.base import json_body as _json

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
BINANCE_BASE = "https://api.binance.us/api/v3"
COINBASE_BASE = "https://api.exchange.coinbase.com"
KRAKEN_BASE = "https://api.kraken.com/0/public"
COINBASE_MAX_CANDLES = 300
COINGECKO_MAX_DAYS = 365
BINANCE_MAX_ROWS = 1000

COINGECKO_IDS: Dict[str, str] = {
    "BTC-USD": "bitcoin",
    "ETH-USD": "ethereum",
    "DOGE-USD": "dogecoin",
    "SOL-USD": "solana",
    "XRP-USD": "ripple",
    "BNB-USD": "binancecoin",
    "ADA-USD": "cardano",
    "LTC-USD": "litecoin",
    "DOT-USD": "polkadot",
    "AVAX-USD": "avalanche-2",
    "LINK-USD": "chainlink",
    "TRX-USD": "tron",
    "SHIB-USD": "shiba-inu",
    "BCH-USD": "bitcoin-cash",
    "XLM-USD": "stellar",
    "ATOM-USD": "cosmos",
    "UNI-USD": "uniswap",
}


def _iso_to_epoch(value):
    """Epoch seconds from an ISO-8601 string; None when missing or invalid."""
    try:
        parsed = pd.Timestamp(value)
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize("UTC")
        return parsed.timestamp()
    except (TypeError, ValueError):
        return None


def _daily_sparkline(prices, points: int = 5):
    """Pick one price per day (newest last) from an hourly 7-day series."""
    values = [float(p) for p in prices or [] if p is not None]
    return list(reversed(values[::-1][::24][:points]))


def parse_markets(payload) -> Dict[str, Dict]:
    """Map `/coins/markets` rows to quotes keyed by Yahoo-style symbol."""
    by_id = {coin_id: symbol for symbol, coin_id in COINGECKO_IDS.items()}
    quotes = {}
    for row in payload or []:
        symbol = by_id.get(row.get("id"))
        price = row.get("current_price")
        if symbol is None or price is None:
            continue
        pct = row.get("price_change_percentage_24h")
        if pct is None:
            pct = row.get("price_change_percentage_24h_in_currency")
        change = row.get("price_change_24h")
        sparkline = _daily_sparkline((row.get("sparkline_in_7d") or {}).get("price"))
        quotes[symbol] = {
            "price": float(price),
            "change": float(change) if change is not None else 0.0,
            "change_pct": float(pct) if pct is not None else 0.0,
            "sparkline": sparkline,
            "as_of": str(row.get("last_updated") or ""),
            "ts": _iso_to_epoch(row.get("last_updated")),
        }
    return quotes


def parse_market_chart(payload) -> pd.DataFrame:
    """Daily frame from `/market_chart`.

    CoinGecko only provides daily closes and volumes here, so Open is the
    previous close and High/Low are bounded by Open/Close (approximation).
    """
    prices = (payload or {}).get("prices") or []
    if not prices:
        raise NoData("no price data")
    volumes = {
        pd.Timestamp(ts, unit="ms").normalize(): vol
        for ts, vol in (payload.get("total_volumes") or [])
    }
    rows, previous = [], None
    for ts, close in prices:
        if close is None:
            continue
        day = pd.Timestamp(ts, unit="ms").normalize()
        open_ = previous if previous is not None else close
        rows.append(
            {
                "Date": day,
                "Open": open_,
                "High": max(open_, close),
                "Low": min(open_, close),
                "Close": close,
                "Volume": volumes.get(day) or 0.0,
            }
        )
        previous = close
    frame = build_frame(rows)
    if frame.empty:
        raise NoData("no price data")
    return frame


class CoinGeckoProvider(Provider):
    name = "coingecko"
    label = "CoinGecko"
    freshness = "delayed ~1-2 min"
    latency = "delayed"

    def supports(self, symbol: str) -> bool:
        return symbol in COINGECKO_IDS

    @staticmethod
    def _headers() -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        key = os.getenv("COINGECKO_API_KEY")
        if key:
            headers["x-cg-demo-api-key"] = key
        return headers

    def get_markets(self, symbols: Sequence[str]) -> Dict[str, Dict]:
        """One call for many coins (used by the overview)."""
        ids = [COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS]
        if not ids:
            raise NoData("no supported coins")
        response = http_get(
            f"{COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(ids),
                "sparkline": "true",
                "price_change_percentage": "24h",
            },
            headers=self._headers(),
        )
        quotes = parse_markets(_json(response))
        if not quotes:
            raise NoData("empty markets response")
        return quotes

    def get_quote(self, symbol: str) -> Dict:
        return self.get_markets([symbol])[symbol]

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        response = http_get(
            f"{COINGECKO_BASE}/coins/{COINGECKO_IDS[symbol]}/market_chart",
            params={
                "vs_currency": "usd",
                "days": min(max(int(days), 2), COINGECKO_MAX_DAYS),
                "interval": "daily",
            },
            headers=self._headers(),
        )
        return parse_market_chart(_json(response))


def binance_pair(symbol: str) -> str:
    return symbol.upper().replace("-USD", "USDT")


def parse_klines(rows) -> pd.DataFrame:
    parsed = [
        {
            "Date": pd.Timestamp(row[0], unit="ms"),
            "Open": row[1],
            "High": row[2],
            "Low": row[3],
            "Close": row[4],
            "Volume": row[5],
        }
        for row in rows or []
    ]
    frame = build_frame(parsed)
    if frame.empty:
        raise NoData("no kline data")
    return frame


class BinanceProvider(Provider):
    """Public market data; often geo-blocked (HTTP 451) so it fails gracefully."""

    name = "binance"
    label = "Binance.US"
    freshness = "near real-time (optional fallback)"
    latency = "realtime"

    def supports(self, symbol: str) -> bool:
        return symbol in COINGECKO_IDS

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        response = http_get(
            f"{BINANCE_BASE}/klines",
            params={
                "symbol": binance_pair(symbol),
                "interval": "1d",
                "limit": min(max(int(days), 2), BINANCE_MAX_ROWS),
            },
        )
        return parse_klines(_json(response))

    def get_quote(self, symbol: str) -> Dict:
        response = http_get(
            f"{BINANCE_BASE}/ticker/24hr", params={"symbol": binance_pair(symbol)}
        )
        data = _json(response)
        try:
            quote = {
                "price": float(data["lastPrice"]),
                "change": float(data["priceChange"]),
                "change_pct": float(data["priceChangePercent"]),
                "sparkline": [],
                "as_of": pd.Timestamp(data["closeTime"], unit="ms").strftime(
                    "%Y-%m-%d %H:%M"
                ),
            }
        except (KeyError, TypeError, ValueError):
            raise NoData("unexpected ticker payload") from None
        return quote


# ----------------------------------------------------------------- Coinbase

COINBASE_RANGES = {"1D": (300, 1), "5D": (900, 5)}


def coinbase_product(symbol: str) -> str:
    return symbol.upper()


def parse_coinbase_candles(rows) -> pd.DataFrame:
    """Frame from Coinbase candles ``[time, low, high, open, close, volume]``."""
    parsed = [
        {
            "Date": pd.Timestamp(row[0], unit="s"),
            "Low": row[1],
            "High": row[2],
            "Open": row[3],
            "Close": row[4],
            "Volume": row[5],
        }
        for row in rows or []
        if len(row) >= 6
    ]
    frame = build_frame(parsed)
    if frame.empty:
        raise NoData("no candle data")
    return frame


def parse_coinbase_ticker(ticker, stats=None) -> Dict:
    try:
        price = float(ticker["price"])
    except (KeyError, TypeError, ValueError):
        raise NoData("unexpected ticker payload") from None
    ts = _iso_to_epoch(ticker.get("time")) if isinstance(ticker, dict) else None
    open_24h = None
    try:
        open_24h = float((stats or {}).get("open"))
    except (TypeError, ValueError):
        pass
    change = price - open_24h if open_24h else 0.0
    return {
        "price": price,
        "change": change,
        "change_pct": (change / open_24h * 100) if open_24h else 0.0,
        "sparkline": [],
        "as_of": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")
        if ts
        else "",
        "ts": ts,
    }


class CoinbaseProvider(Provider):
    """Coinbase Exchange public API: real-time ticker, works from US cloud IPs."""

    name = "coinbase"
    label = "Coinbase"
    freshness = "live (exchange ticker)"
    latency = "realtime"
    intraday = True

    def __init__(self):
        self.limiter = TokenBucket(300, 20)

    def supports(self, symbol: str) -> bool:
        return symbol in COINBASE_PRODUCTS

    def _get(self, path, params=None):
        return _json(
            http_get(f"{COINBASE_BASE}{path}", params=params, limiter=self.limiter)
        )

    def get_ticker_quote(self, symbol: str) -> Dict:
        return self.get_ticker(symbol)

    def get_ticker(self, symbol: str) -> Dict:
        """Last trade only (one cheap call) for the high-frequency refresher."""
        return parse_coinbase_ticker(
            self._get(f"/products/{coinbase_product(symbol)}/ticker")
        )

    def get_quote(self, symbol: str) -> Dict:
        product = coinbase_product(symbol)
        ticker = self._get(f"/products/{product}/ticker")
        try:
            stats = self._get(f"/products/{product}/stats")
        except ProviderError:
            stats = None  # 24h stats are optional; change shows as 0
        return parse_coinbase_ticker(ticker, stats)

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        days = min(max(int(days), 2), COINBASE_MAX_CANDLES)
        end = datetime.now(timezone.utc)
        rows = self._get(
            f"/products/{coinbase_product(symbol)}/candles",
            {
                "granularity": 86400,
                "start": (end - timedelta(days=days)).isoformat(),
                "end": end.isoformat(),
            },
        )
        return parse_coinbase_candles(rows)

    def get_intraday(self, symbol: str, label: str) -> pd.DataFrame:
        """1D = 5-minute and 5D = 15-minute candles (newest candle is live)."""
        granularity, days = COINBASE_RANGES[label]
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        chunk = timedelta(seconds=granularity * COINBASE_MAX_CANDLES)
        frames, cursor = [], start
        while cursor < end:
            stop = min(cursor + chunk, end)
            rows = self._get(
                f"/products/{coinbase_product(symbol)}/candles",
                {
                    "granularity": granularity,
                    "start": cursor.isoformat(),
                    "end": stop.isoformat(),
                },
            )
            if rows:
                frames.append(parse_coinbase_candles_intraday(rows))
            cursor = stop
        if not frames:
            raise NoData("no intraday candles")
        frame = pd.concat(frames)
        return frame[~frame.index.duplicated(keep="last")].sort_index()


def parse_coinbase_candles_intraday(rows) -> pd.DataFrame:
    """Like ``parse_coinbase_candles`` but keeps the intraday timestamps."""
    frame = pd.DataFrame(
        [
            {
                "Date": pd.Timestamp(r[0], unit="s"),
                "Low": r[1],
                "High": r[2],
                "Open": r[3],
                "Close": r[4],
                "Volume": r[5],
            }
            for r in rows
            if len(r) >= 6
        ]
    )
    if frame.empty:
        raise NoData("no candle data")
    return frame.set_index("Date").sort_index().astype(float)


COINBASE_PRODUCTS = {
    s for s in COINGECKO_IDS if s not in ("BNB-USD", "TRX-USD", "SHIB-USD")
}

# ------------------------------------------------------------------- Kraken

KRAKEN_PAIRS: Dict[str, str] = {
    "BTC-USD": "XBTUSD",
    "ETH-USD": "ETHUSD",
    "DOGE-USD": "XDGUSD",
    "SOL-USD": "SOLUSD",
    "XRP-USD": "XRPUSD",
    "ADA-USD": "ADAUSD",
    "LTC-USD": "LTCUSD",
    "DOT-USD": "DOTUSD",
    "LINK-USD": "LINKUSD",
    "AVAX-USD": "AVAXUSD",
}
KRAKEN_RANGES = {"1D": (5, 1), "5D": (15, 5)}


def _kraken_result(payload):
    errors = (payload or {}).get("error") if isinstance(payload, dict) else None
    if errors:
        raise NoData(f"kraken error: {errors[0]}"[:80])
    result = (payload or {}).get("result") or {}
    return {k: v for k, v in result.items() if k != "last"}


def parse_kraken_ticker(payload) -> Dict:
    """Quote from Kraken's ticker (``c`` last trade, ``o`` today's open)."""
    values = _kraken_result(payload)
    if not values:
        raise NoData("empty ticker")
    data = next(iter(values.values()))
    try:
        price = float(data["c"][0])
        open_ = float(data["o"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise NoData("unexpected ticker payload") from None
    change = price - open_
    now = datetime.now(timezone.utc)
    return {
        "price": price,
        "change": change,
        "change_pct": (change / open_ * 100) if open_ else 0.0,
        "sparkline": [],
        "as_of": now.strftime("%Y-%m-%d %H:%M"),
        "ts": now.timestamp(),
    }


def parse_kraken_ohlc(payload, intraday: bool = False) -> pd.DataFrame:
    values = _kraken_result(payload)
    rows = next(iter(values.values()), None) if values else None
    if not rows:
        raise NoData("no candle data")
    parsed = [
        {
            "Date": pd.Timestamp(r[0], unit="s"),
            "Open": r[1],
            "High": r[2],
            "Low": r[3],
            "Close": r[4],
            "Volume": r[6],
        }
        for r in rows
    ]
    if intraday:
        return pd.DataFrame(parsed).set_index("Date").sort_index().astype(float)
    return build_frame(parsed)


class KrakenProvider(Provider):
    name = "kraken"
    label = "Kraken"
    freshness = "live (exchange ticker)"
    latency = "realtime"
    intraday = True

    def __init__(self):
        self.limiter = TokenBucket(60, 10)

    def supports(self, symbol: str) -> bool:
        return symbol in KRAKEN_PAIRS

    def _get(self, path, params):
        return _json(
            http_get(f"{KRAKEN_BASE}{path}", params=params, limiter=self.limiter)
        )

    def get_quote(self, symbol: str) -> Dict:
        return parse_kraken_ticker(self._get("/Ticker", {"pair": KRAKEN_PAIRS[symbol]}))

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        payload = self._get("/OHLC", {"pair": KRAKEN_PAIRS[symbol], "interval": 1440})
        return parse_kraken_ohlc(payload)

    def get_intraday(self, symbol: str, label: str) -> pd.DataFrame:
        interval, days = KRAKEN_RANGES[label]
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        payload = self._get(
            "/OHLC",
            {"pair": KRAKEN_PAIRS[symbol], "interval": interval, "since": since},
        )
        return parse_kraken_ohlc(payload, intraday=True)
