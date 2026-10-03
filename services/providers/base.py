"""Shared building blocks for market data providers."""

import logging
import threading
import time
from typing import Callable, Dict, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 8
BREAKER_COOLDOWN_SECONDS = 300
RETRY_BACKOFF_SECONDS = 0.5
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
BLOCKED_STATUSES = (403, 429, 451)


class ProviderError(Exception):
    """A provider could not answer (message never contains secrets)."""


class NoData(ProviderError):
    """The provider answered but had no usable data for the symbol."""


class RateLimited(ProviderError):
    """The provider rate-limited or blocked us (HTTP 403/429/451)."""


class CircuitBreaker:
    """Skip a provider for a cool-down period after it rate-limits us."""

    def __init__(
        self,
        cooldown: float = BREAKER_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cooldown = cooldown
        self._clock = clock
        self._until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def trip(self, name: str, cooldown: Optional[float] = None) -> None:
        with self._lock:
            self._until[name] = self._clock() + (cooldown or self.cooldown)

    def remaining(self, name: str) -> int:
        with self._lock:
            left = self._until.get(name, 0) - self._clock()
        return int(left) + 1 if left > 0 else 0

    def is_open(self, name: str) -> bool:
        return self.remaining(name) > 0

    def reset(self, name: Optional[str] = None) -> None:
        with self._lock:
            if name is None:
                self._until.clear()
            else:
                self._until.pop(name, None)


breaker = CircuitBreaker()


def http_get(url, params=None, headers=None, retries: int = 1):
    """GET with an explicit timeout; retries (with backoff) only on 5xx.

    Raises RateLimited for 403/429/451 and ProviderError for other failures.
    Error messages deliberately omit URLs because they may carry API keys.
    """
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=HTTP_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise ProviderError(f"request failed ({type(exc).__name__})") from None
        status = response.status_code
        if status in BLOCKED_STATUSES:
            raise RateLimited(f"HTTP {status}")
        if status >= 500 and attempt < retries:
            time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
            continue
        if status >= 400:
            raise ProviderError(f"HTTP {status}")
        return response
    raise ProviderError("request failed")  # pragma: no cover


def json_body(response):
    try:
        return response.json()
    except ValueError:
        raise ProviderError("invalid JSON") from None


def is_crypto(symbol: str) -> bool:
    symbol = (symbol or "").upper()
    return symbol.endswith("-USD") and not symbol.startswith("^")


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def build_frame(rows) -> pd.DataFrame:
    """OHLCV frame (capitalised columns, daily date index) from row dicts."""
    frame = pd.DataFrame(rows)
    if frame.empty or "Close" not in frame.columns:
        return empty_frame()
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None).dt.normalize()
    frame = frame.set_index("Date").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    for column in OHLCV_COLUMNS:
        if column not in frame.columns:
            frame[column] = frame["Close"] if column != "Volume" else 0.0
    frame = frame[OHLCV_COLUMNS].apply(pd.to_numeric, errors="coerce")
    frame["Volume"] = frame["Volume"].fillna(0.0)
    frame = frame.dropna(subset=["Close"])
    frame.index.name = "Date"
    return frame


def quote_from_frame(frame: pd.DataFrame) -> Dict:
    """Latest price, daily change and a 5 point sparkline from a history frame."""
    closes = [float(v) for v in frame["Close"].dropna().tolist()]
    if not closes:
        raise NoData("no price data")
    price = closes[-1]
    previous = closes[-2] if len(closes) > 1 else price
    change = price - previous
    return {
        "price": price,
        "change": change,
        "change_pct": (change / previous * 100) if previous else 0.0,
        "sparkline": closes[-5:],
        "as_of": pd.Timestamp(frame.index[-1]).strftime("%Y-%m-%d"),
    }


class Provider:
    """Common interface: get_history(symbol, days) and get_quote(symbol)."""

    name = ""
    label = ""
    # detail_only providers have a tiny quota and are skipped for the overview
    detail_only = False
    # delay note shown to users, never claim real time
    freshness = "delayed"

    def available(self) -> bool:
        return True

    def supports(self, symbol: str) -> bool:
        return True

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        raise NotImplementedError

    def get_quote(self, symbol: str) -> Dict:
        return quote_from_frame(self.get_history(symbol, 14))
