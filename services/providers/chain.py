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
)
from services.providers.crypto import BinanceProvider, CoinGeckoProvider
from services.providers.equities import (
    AlphaVantageProvider,
    FinnhubProvider,
    StooqProvider,
    YahooChartProvider,
    YFinanceProvider,
)

logger = logging.getLogger(__name__)

MAX_WORKERS = 6
OVERVIEW_BUDGET_SECONDS = 12
DIAGNOSTIC_BUDGET_SECONDS = 25
HISTORY_DAYS = 730

coingecko = CoinGeckoProvider()
binance = BinanceProvider()
finnhub = FinnhubProvider()
stooq = StooqProvider()
alpha_vantage = AlphaVantageProvider()
yahoo = YahooChartProvider()
yfinance_provider = YFinanceProvider()

ALL_PROVIDERS: List[Provider] = [
    coingecko,
    binance,
    finnhub,
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
        order = [coingecko, binance, yahoo, yfinance_provider]
    else:
        order = [finnhub, stooq, alpha_vantage, yahoo, yfinance_provider]
    return [p for p in order if p.supports(symbol)]


def quote_chain(symbol: str, overview: bool = False) -> List[Provider]:
    """Overview quotes skip quota-limited and Yahoo providers (Yahoo is batched)."""
    chain = history_chain(symbol)
    if overview:
        chain = [
            p
            for p in chain
            if not p.detail_only and p not in (yahoo, yfinance_provider)
        ]
    return chain


def _record(name: str, ok: bool, error: str = "", latency_ms: int = 0) -> None:
    with _status_lock:
        _status[name] = {
            "ok": ok,
            "error": error,
            "latency_ms": latency_ms,
            "at": time.time(),
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
        breaker.trip(provider.name)
        error = f"rate limited/blocked: {exc}"
        result = None
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        result = None
    outcome["latency_ms"] = int((time.monotonic() - started) * 1000)
    outcome["ok"] = result is not None
    outcome["error"] = error
    _record(provider.name, outcome["ok"], error, outcome["latency_ms"])
    if error:
        logger.warning("Provider %s %s failed: %s", provider.name, method, error)
    return result, outcome


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
            logger.info("Quote for %s served by %s", symbol, provider.name)
            return quote, provider.name
    return None, ""


def _crypto_quotes(symbols: Sequence[str]) -> Dict[str, Tuple[Dict, str]]:
    """CoinGecko in ONE call, per-symbol fallbacks only for what is missing."""
    results: Dict[str, Tuple[Dict, str]] = {}
    markets, _ = attempt(coingecko, "get_markets", list(symbols))
    for symbol, quote in (markets or {}).items():
        results[symbol] = (quote, coingecko.name)
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
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def sources_status() -> Dict[str, Dict]:
    """Snapshot of every provider's state (no network, no secrets)."""
    snapshot = {}
    with _status_lock:
        recorded = dict(_status)
    for provider in ALL_PROVIDERS:
        info = {"label": provider.label, "freshness": provider.freshness}
        if not provider.available():
            info["state"] = "not configured"
        elif breaker.is_open(provider.name):
            info["state"] = "cooling down"
            info["cooldown_seconds"] = breaker.remaining(provider.name)
        elif provider.name in recorded:
            last = recorded[provider.name]
            info["state"] = "ok" if last["ok"] else "failed"
            if last["error"]:
                info["last_error"] = last["error"]
        else:
            info["state"] = "unused"
        snapshot[provider.name] = info
    return snapshot


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
