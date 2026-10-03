"""Crypto providers: CoinGecko (primary) and Binance public API (fallback)."""

import os
from typing import Dict, Sequence

import pandas as pd

from services.providers.base import NoData, Provider, build_frame, http_get
from services.providers.base import json_body as _json

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
BINANCE_BASE = "https://api.binance.com/api/v3"
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
    label = "Binance"
    freshness = "delayed ~1 min"

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
