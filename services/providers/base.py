"""Shared building blocks for market data providers."""

import logging
import threading
import time
from typing import Callable, Dict, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 8
BREAKER_COOLDOWN_SECONDS = 60
BREAKER_MAX_COOLDOWN_SECONDS = 600
LOG_INTERVAL_SECONDS = 60
RETRY_BACKOFF_SECONDS = 0.5
DEFAULT_USER_AGENT = "ai-stock-analyzer/2.0 (+https://github.com/baseerkhankhail99)"
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
    """Skip a provider for a cool-down after it rate-limits or blocks us.

    The cool-down doubles for each consecutive trip (up to a maximum) and is
    reset by ``success`` so one blocked response never locks a provider out
    for long once it recovers. The reason is kept for the diagnostics panel.
    """

    def __init__(
        self,
        cooldown: float = BREAKER_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_cooldown: float = BREAKER_MAX_COOLDOWN_SECONDS,
    ):
        self.cooldown = cooldown
        self.max_cooldown = max(max_cooldown, cooldown)
        self._clock = clock
        self._until: Dict[str, float] = {}
        self._strikes: Dict[str, int] = {}
        self._reasons: Dict[str, str] = {}
        self._lock = threading.Lock()

    def trip(
        self, name: str, cooldown: Optional[float] = None, reason: str = ""
    ) -> None:
        with self._lock:
            strikes = self._strikes.get(name, 0)
            if cooldown is None:
                cooldown = min(self.cooldown * (2**strikes), self.max_cooldown)
            self._strikes[name] = strikes + 1
            self._until[name] = self._clock() + cooldown
            self._reasons[name] = reason

    def success(self, name: str) -> None:
        with self._lock:
            self._strikes.pop(name, None)
            self._reasons.pop(name, None)

    def reason(self, name: str) -> str:
        with self._lock:
            return self._reasons.get(name, "")

    def remaining(self, name: str) -> int:
        with self._lock:
            left = self._until.get(name, 0) - self._clock()
        return int(left) + 1 if left > 0 else 0

    def is_open(self, name: str) -> bool:
        return self.remaining(name) > 0

    def reset(self, name: Optional[str] = None) -> None:
        with self._lock:
            for store in (self._until, self._strikes, self._reasons):
                if name is None:
                    store.clear()
                else:
                    store.pop(name, None)


class TokenBucket:
    """Thread-safe token bucket used to respect provider rate limits."""

    def __init__(
        self,
        rate_per_minute: float,
        capacity: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.rate = rate_per_minute / 60.0
        self.capacity = float(capacity if capacity is not None else rate_per_minute)
        self._tokens = self.capacity
        self._clock = clock
        self._stamp = clock()
        self._lock = threading.Lock()

    def take(self, tokens: float = 1.0) -> bool:
        with self._lock:
            now = self._clock()
            self._tokens = min(
                self.capacity, self._tokens + (now - self._stamp) * self.rate
            )
            self._stamp = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def wait_seconds(self, tokens: float = 1.0) -> int:
        with self._lock:
            missing = tokens - self._tokens
        return int(missing / self.rate) + 1 if missing > 0 and self.rate else 0


class LogThrottle:
    """Allow one log line per key and interval (default once per minute)."""

    def __init__(
        self,
        interval: float = LOG_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.interval = interval
        self._clock = clock
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.interval:
                return False
            self._last[key] = now
            return True

    def reset(self) -> None:
        with self._lock:
            self._last.clear()


log_throttle = LogThrottle()


breaker = CircuitBreaker()


def http_get(url, params=None, headers=None, retries: int = 1, limiter=None):
    """GET with an explicit timeout; retries (with backoff) only on 5xx.

    Raises RateLimited for 403/429/451 and ProviderError for other failures.
    Error messages deliberately omit URLs because they may carry API keys.
    An optional ``TokenBucket`` limiter rejects the call locally (without
    tripping the circuit breaker) when the provider's rate budget is spent.
    """
    headers = {"User-Agent": DEFAULT_USER_AGENT, **(headers or {})}
    if limiter is not None and not limiter.take():
        raise ProviderError(f"local rate limit, retry in {limiter.wait_seconds()}s")
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


SPARKLINE_POINTS = 30


def quote_from_frame(frame: pd.DataFrame) -> Dict:
    """Latest price, daily change and a 30 point sparkline from a history frame."""
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
        "sparkline": closes[-SPARKLINE_POINTS:],
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
    # kind of timestamp the provider delivers: "realtime", "delayed" or "eod"
    latency = "delayed"

    def available(self) -> bool:
        return True

    def supports(self, symbol: str) -> bool:
        return True

    def get_history(self, symbol: str, days: int = 730) -> pd.DataFrame:
        raise NotImplementedError

    def get_quote(self, symbol: str) -> Dict:
        return quote_from_frame(self.get_history(symbol, 45))

    def get_ticker_quote(self, symbol: str) -> Dict:
        """Cheapest possible latest-price call (defaults to ``get_quote``)."""
        return self.get_quote(symbol)
