"""In-memory store of the latest price ticks plus honest freshness labels.

Single-process only: with several gunicorn workers each worker would hold its
own copy. Scaling out needs a shared store (e.g. Redis) behind this interface.
"""

import threading
import time
from typing import Dict, Optional

LIVE_MAX_AGE_SECONDS = 15
EOD_AFTER_SECONDS = 6 * 3600

# How trustworthy each source's timestamp is: "realtime" ticks can be LIVE,
# "delayed" sources never are, "eod" sources are end-of-day closes.
SOURCE_LATENCY: Dict[str, str] = {
    "coinbase": "realtime",
    "kraken": "realtime",
    "binance": "realtime",
    "finnhub": "realtime",
    "coingecko": "delayed",
    "yahoo": "delayed",
    "yfinance": "delayed",
    "stooq": "eod",
    "alphavantage": "eod",
    "twelvedata": "eod",
    "history": "eod",
}


def latency_for(source: Optional[str]) -> str:
    return SOURCE_LATENCY.get(source or "", "delayed")


def format_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 48 * 3600:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def freshness(
    ts: Optional[float], source: Optional[str], now: Optional[float] = None
) -> Dict:
    """Badge data computed from the tick timestamp: live / delayed / eod."""
    now = time.time() if now is None else now
    latency = latency_for(source)
    tooltip = f"Source: {source or 'unknown'}"
    if latency == "eod" or (ts is None and latency != "realtime"):
        return {"state": "eod", "label": "End of day", "age": None, "tooltip": tooltip}
    if ts is None:
        return {
            "state": "unknown",
            "label": "Unknown",
            "age": None,
            "tooltip": tooltip,
        }
    age = max(0.0, now - ts)
    if age > EOD_AFTER_SECONDS:
        return {
            "state": "eod",
            "label": f"End of day (last tick {format_age(age)} ago)",
            "age": age,
            "tooltip": tooltip,
        }
    if latency == "realtime" and age <= LIVE_MAX_AGE_SECONDS:
        return {
            "state": "live",
            "label": f"● LIVE ({format_age(age)})",
            "age": age,
            "tooltip": tooltip,
        }
    return {
        "state": "delayed",
        "label": f"● Delayed ({format_age(age)})",
        "age": age,
        "tooltip": tooltip
        + (" (free feed is delayed)" if latency != "realtime" else ""),
    }


class TickStore:
    def __init__(self):
        self._ticks: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def update(
        self,
        symbol: str,
        price: float,
        source: str,
        ts: Optional[float] = None,
        **extra,
    ) -> None:
        if not price or price <= 0:
            return
        tick = {
            "symbol": symbol,
            "price": float(price),
            "source": source,
            "ts": float(ts) if ts else time.time(),
            **extra,
        }
        with self._lock:
            current = self._ticks.get(symbol)
            if current and current["ts"] > tick["ts"]:
                return
            self._ticks[symbol] = tick

    def get(self, symbol: str) -> Optional[Dict]:
        with self._lock:
            tick = self._ticks.get(symbol)
            return dict(tick) if tick else None

    def snapshot(self) -> Dict[str, Dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._ticks.items()}

    def clear(self) -> None:
        with self._lock:
            self._ticks.clear()


tick_store = TickStore()
