"""Provider chain: try providers in order, log the winner, skip broken ones."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from services.providers.base import (
    NoData,
    Provider,
    RateLimited,
    breaker,
    empty_frame,
    is_crypto,
    log_throttle,
    quote_from_frame,
)
from services.providers.crypto import (
    BinanceProvider,
    CoinbaseProvider,
    CoinGeckoProvider,
    KrakenProvider,
)
from services.providers.equities import (
    ETF_PROXIES,
    AlphaVantageProvider,
    FinnhubProvider,
    StooqProvider,
    TwelveDataProvider,
    YahooChartProvider,
    YFinanceProvider,
)
from services.tick_store import tick_store

logger = logging.getLogger(__name__)

MAX_WORKERS = 6
OVERVIEW_BUDGET_SECONDS = 20
FAILURES_BEFORE_COOLDOWN = 3
FAILURE_COOLDOWN_SECONDS = 30
DIAGNOSTIC_BUDGET_SECONDS = 25
HISTORY_DAYS = 730

coingecko = CoinGeckoProvider()
coinbase = CoinbaseProvider()
kraken = KrakenProvider()
binance = BinanceProvider()
finnhub = FinnhubProvider()
twelvedata = TwelveDataProvider()
stooq = StooqProvider()
alpha_vantage = AlphaVantageProvider()
yahoo = YahooChartProvider()
yfinance_provider = YFinanceProvider()

ALL_PROVIDERS: List[Provider] = [
    coinbase,
    kraken,
    coingecko,
    binance,
    finnhub,
    twelvedata,
    stooq,
    alpha_vantage,
    yahoo,
    yfinance_provider,
]
PROVIDERS_BY_NAME = {p.name: p for p in ALL_PROVIDERS}

_status: Dict[str, Dict] = {}
_status_lock = threading.Lock()


def history_chain(symbol: str) -> List[Provider]:
    if is_crypto(symbol):
        order = [coingecko, coinbase, kraken, binance, yahoo, yfinance_provider]
    else:
        order = [
            finnhub,
            stooq,
            twelvedata,
            alpha_vantage,
            yahoo,
            yfinance_provider,
        ]
    return [p for p in order if p.supports(symbol)]


def quote_chain(symbol: str, overview: bool = False) -> List[Provider]:
    """Real-time sources first. Overview skips quota-limited providers.

    Yahoo stays in the chain (rate limited by its own token bucket): it is the
    quote source that works for stocks, indices and commodities when Stooq and
    Finnhub are unavailable. yfinance remains the batched last resort.
    """
    if is_crypto(symbol):
        order = [coinbase, kraken, coingecko, binance, yahoo]
    else:
        order = [finnhub, yahoo, twelvedata, stooq]
    chain = [p for p in order if p.supports(symbol)]
    if overview:
        chain = [p for p in chain if not p.detail_only]
    return chain


def _record(name: str, ok: bool, error: str = "", latency_ms: int = 0) -> None:
    with _status_lock:
        previous = _status.get(name, {})
        _status[name] = {
            "ok": ok,
            "error": error,
            "latency_ms": latency_ms,
            "at": time.time(),
            "fails": 0 if ok else previous.get("fails", 0) + 1,
        }


def attempt(provider: Provider, method: str, *args, bypass_breaker: bool = False):
    """Call one provider method; returns (result, outcome-dict)."""
    outcome = {"provider": provider.name, "ok": False, "error": "", "latency_ms": 0}
    if not provider.available():
        outcome["error"] = "not configured"
        outcome["skipped"] = True
        return None, outcome
    if not bypass_breaker and breaker.is_open(provider.name):
        outcome["error"] = f"cooling down ({breaker.remaining(provider.name)}s)"
        outcome["skipped"] = True
        return None, outcome
    started = time.monotonic()
    result, error = None, ""
    try:
        result = getattr(provider, method)(*args)
        if result is None or (isinstance(result, pd.DataFrame) and result.empty):
            raise NoData("empty result")
    except RateLimited as exc:
        error = f"rate limited/blocked: {exc}"
        breaker.trip(provider.name, reason=str(exc))
        result = None
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        result = None
    outcome["latency_ms"] = int((time.monotonic() - started) * 1000)
    outcome["ok"] = result is not None
    outcome["error"] = error
    _record(provider.name, outcome["ok"], error, outcome["latency_ms"])
    if outcome["ok"]:
        breaker.success(provider.name)
    else:
        _after_failure(provider, method, error)
    return result, outcome


def _after_failure(provider: Provider, method: str, error: str) -> None:
    """Throttled WARNING (once a minute per provider) and failure cool-down."""
    with _status_lock:
        fails = _status.get(provider.name, {}).get("fails", 0)
    if fails >= FAILURES_BEFORE_COOLDOWN and not breaker.is_open(provider.name):
        breaker.trip(provider.name, FAILURE_COOLDOWN_SECONDS, reason=error)
    if log_throttle.allow(f"provider:{provider.name}"):
        logger.warning(
            "Provider %s %s failed: %s (consecutive failures: %d)",
            provider.name,
            method,
            error,
            fails,
        )


def get_history(symbol: str, days: int = HISTORY_DAYS) -> Tuple[pd.DataFrame, str]:
    """OHLCV frame (Open..Volume, date index) and the answering provider."""
    for provider in history_chain(symbol):
        frame, outcome = attempt(provider, "get_history", symbol, days)
        if outcome["ok"]:
            logger.info("History for %s served by %s", symbol, provider.name)
            return frame, provider.name
    return empty_frame(), ""


def get_quote(
    symbol: str, overview: bool = False, exclude: Sequence[Provider] = ()
) -> Tuple[Optional[Dict], str]:
    for provider in quote_chain(symbol, overview):
        if provider in exclude:
            continue
        quote, outcome = attempt(provider, "get_quote", symbol)
        if outcome["ok"]:
            logger.debug("Quote for %s served by %s", symbol, provider.name)
            record_tick(symbol, quote, provider.name)
            return quote, provider.name
    return None, ""


def record_tick(symbol: str, quote: Dict, source: str) -> None:
    """Remember the latest price with its own timestamp for freshness badges."""
    if quote and quote.get("price") and quote.get("ts"):
        tick_store.update(symbol, quote["price"], source, quote["ts"])


def quote_from_history(symbol: str) -> Tuple[Optional[Dict], str]:
    """Price/change/30-point sparkline from the history chain (last resort)."""
    frame, source = get_history(symbol, 60)
    if frame.empty:
        return None, ""
    try:
        quote = quote_from_frame(frame)
    except NoData:
        return None, ""
    quote["derived_from_history"] = True
    return quote, source


def _crypto_quotes(symbols: Sequence[str]) -> Dict[str, Tuple[Dict, str]]:
    """CoinGecko in ONE call (market data, sparklines); exchanges fill the gaps."""
    results: Dict[str, Tuple[Dict, str]] = {}
    markets, _ = attempt(coingecko, "get_markets", list(symbols))
    for symbol, quote in (markets or {}).items():
        results[symbol] = (quote, coingecko.name)
        record_tick(symbol, quote, coingecko.name)
    for symbol in symbols:
        if symbol not in results:
            quote, source = get_quote(symbol, True, exclude=(coingecko,))
            if quote:
                results[symbol] = (quote, source)
    return results


def _yfinance_batch(symbols: Sequence[str]) -> Dict[str, Tuple[Dict, str]]:
    quotes, _ = attempt(yfinance_provider, "get_quotes", list(symbols))
    return {s: (q, yfinance_provider.name) for s, q in (quotes or {}).items()}


def fetch_quotes(
    symbols: Sequence[str], budget: float = OVERVIEW_BUDGET_SECONDS
) -> Dict[str, Tuple[Dict, str]]:
    """Quotes for many symbols, fetched concurrently within a time budget.

    Failed or slow symbols are simply missing from the result.
    """
    deadline = time.monotonic() + budget
    crypto = [s for s in symbols if is_crypto(s)]
    others = [s for s in symbols if not is_crypto(s)]
    results: Dict[str, Tuple[Dict, str]] = {}
    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    try:
        futures = {}
        if crypto:
            futures[pool.submit(_crypto_quotes, crypto)] = crypto
        for symbol in others:
            futures[pool.submit(get_quote, symbol, True)] = symbol
        done, _ = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        for future in done:
            try:
                value = future.result()
            except Exception as exc:
                logger.warning("Quote task failed: %s", type(exc).__name__)
                continue
            if isinstance(futures[future], list):
                results.update(value)
            elif value[0]:
                results[futures[future]] = value

        missing = [s for s in symbols if s not in results]
        if missing and deadline - time.monotonic() > 1:
            # separate executor: stuck workers above must not queue this call
            batch_pool = ThreadPoolExecutor(max_workers=1)
            try:
                batch = batch_pool.submit(_yfinance_batch, missing)
                finished, _ = wait([batch], timeout=deadline - time.monotonic())
                if finished:
                    results.update(batch.result())
            finally:
                batch_pool.shutdown(wait=False, cancel_futures=True)

        # History works where quotes do not: derive price/change/sparkline.
        missing = [s for s in symbols if s not in results]
        if missing and deadline - time.monotonic() > 1:
            futures = {pool.submit(quote_from_history, s): s for s in missing}
            done, _ = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
            for future in done:
                try:
                    quote, source = future.result()
                except Exception as exc:
                    logger.warning("History fallback failed: %s", type(exc).__name__)
                    continue
                if quote:
                    results[futures[future]] = (quote, source)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def refresh_crypto_ticks(symbols: Sequence[str]) -> int:
    """Poll exchange tickers (REST) into the tick store; returns ticks stored."""
    stored = 0
    for symbol in symbols:
        for provider in (coinbase, kraken):
            if not provider.supports(symbol):
                continue
            quote, outcome = attempt(provider, "get_ticker_quote", symbol)
            if outcome["ok"]:
                record_tick(symbol, quote, provider.name)
                stored += 1
                break
    return stored


def fetch_proxy_quotes() -> Dict[str, Tuple[Dict, str]]:
    """Live ETF proxy quotes via Finnhub (empty without a key)."""
    results: Dict[str, Tuple[Dict, str]] = {}
    if not finnhub.available():
        return results
    for etf, _label in ETF_PROXIES.values():
        quote, outcome = attempt(finnhub, "get_quote", etf)
        if outcome["ok"]:
            record_tick(etf, quote, finnhub.name)
            results[etf] = (quote, finnhub.name)
    return results


def intraday_chain(symbol: str) -> List[Provider]:
    chain = [coinbase, kraken] if is_crypto(symbol) else [yahoo]
    return [p for p in chain if p.supports(symbol) and getattr(p, "intraday", False)]


def get_intraday(symbol: str, label: str) -> Tuple[pd.DataFrame, str]:
    """Intraday candles ("1D" = 5 min, "5D" = 15 min); empty when unavailable."""
    for provider in intraday_chain(symbol):
        frame, outcome = attempt(provider, "get_intraday", symbol, label)
        if outcome["ok"]:
            return frame, provider.name
    return empty_frame(), ""


def format_retry(seconds: int) -> str:
    return f"{seconds}s" if seconds < 120 else f"{seconds // 60}m"


def sources_status() -> Dict[str, Dict]:
    """Snapshot of every provider's state with a human reason (no network)."""
    snapshot = {}
    with _status_lock:
        recorded = dict(_status)
    for provider in ALL_PROVIDERS:
        info = {
            "label": provider.label,
            "freshness": provider.freshness,
            "latency": provider.latency,
        }
        last = recorded.get(provider.name)
        if last:
            info["last_attempt_ago_s"] = int(time.time() - last["at"])
            info["latency_ms"] = last["latency_ms"]
        if not provider.available():
            info["state"] = "not configured"
            info["reason"] = f"{provider.label}: not configured (API key missing)"
        elif breaker.is_open(provider.name):
            remaining = breaker.remaining(provider.name)
            info["state"] = "cooling down"
            info["cooldown_seconds"] = remaining
            why = breaker.reason(provider.name) or (last or {}).get("error") or "error"
            info["last_error"] = why
            info[
                "reason"
            ] = f"{provider.label}: {why}, retrying in {format_retry(remaining)}"
        elif last:
            info["state"] = "ok" if last["ok"] else "failed"
            if last["error"]:
                info["last_error"] = last["error"]
                info["reason"] = f"{provider.label}: {last['error']}"
        else:
            info["state"] = "unused"
        snapshot[provider.name] = info
    return snapshot


def failure_reasons() -> List[str]:
    """Reasons of providers that are failing right now (for the UI)."""
    return [
        info["reason"]
        for info in sources_status().values()
        if info["state"] in ("failed", "cooling down") and info.get("reason")
    ]


DIAGNOSTIC_SYMBOLS = [
    ("stock", "AAPL"),
    ("crypto", "BTC-USD"),
    ("commodity", "GC=F"),
    ("index", "^GSPC"),
]


def run_diagnostics(include_alpha_vantage: bool = False) -> Dict:
    """Try each provider individually for one asset per category."""
    tasks = []
    for category, symbol in DIAGNOSTIC_SYMBOLS:
        for provider in history_chain(symbol):
            if provider.detail_only and not include_alpha_vantage:
                tasks.append((category, symbol, provider, True))
            else:
                tasks.append((category, symbol, provider, False))

    def run(task):
        category, symbol, provider, skip = task
        if skip:
            return {
                "provider": provider.name,
                "ok": False,
                "skipped": True,
                "error": "skipped to protect the daily quota",
                "latency_ms": 0,
            }
        _, outcome = attempt(provider, "get_history", symbol, 30, bypass_breaker=True)
        return outcome

    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    try:
        futures = [(task, pool.submit(run, task)) for task in tasks]
        wait([f for _, f in futures], timeout=DIAGNOSTIC_BUDGET_SECONDS)
        report: Dict[str, Dict] = {}
        for (category, symbol, provider, _), future in futures:
            entry = report.setdefault(category, {"symbol": symbol, "providers": []})
            if future.done() and not future.cancelled():
                entry["providers"].append(future.result())
            else:
                entry["providers"].append(
                    {
                        "provider": provider.name,
                        "ok": False,
                        "error": "timed out",
                        "latency_ms": int(DIAGNOSTIC_BUDGET_SECONDS * 1000),
                    }
                )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return report
