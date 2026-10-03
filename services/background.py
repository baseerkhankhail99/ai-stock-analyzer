"""Background threads: pre-warm caches and keep them fresh.

Designed for ``gunicorn --workers 1 --threads N``: the threads are daemon,
started at most once per process and only *write* caches, so web requests
just read them. With several workers each would run its own copy (and its own
tick store); scale out only together with a shared store such as Redis.
"""

import json
import logging
import os
import threading
import time
from typing import Callable, Optional

from services import market_data, providers
from services.providers.base import is_crypto
from services.providers.crypto import COINBASE_PRODUCTS
from services.tick_store import tick_store

logger = logging.getLogger(__name__)

OVERVIEW_INTERVAL_SECONDS = 15
TICK_INTERVAL_SECONDS = 5
HISTORY_INTERVAL_SECONDS = 300
WS_URL = "wss://ws-feed.exchange.coinbase.com"
WS_MAX_BACKOFF_SECONDS = 120

_started = False
_start_lock = threading.Lock()
_threads = []


def enabled(app) -> bool:
    if os.getenv("DISABLE_BACKGROUND_WORKERS", "").lower() in ("1", "true", "yes"):
        return False
    return not app.config.get("TESTING")


def _in_context(app, func: Callable, *args):
    try:
        with app.app_context():
            return func(*args)
    except Exception as exc:
        logger.warning("Background task %s failed: %s", func.__name__, exc)
        return None


def refresh_overview() -> None:
    market_data.get_overview(force=True)


def refresh_ticks() -> None:
    """REST-poll crypto tickers that the websocket did not update recently."""
    now = time.time()
    stale = [
        s
        for s in market_data.all_symbols()
        if is_crypto(s)
        and s in COINBASE_PRODUCTS
        and now - (tick_store.get(s) or {}).get("ts", 0) > TICK_INTERVAL_SECONDS * 2
    ]
    if stale:
        providers.refresh_crypto_ticks(stale)


def refresh_history() -> None:
    for symbol in market_data.all_symbols():
        market_data.fetch_history(symbol, force=True)
        time.sleep(1)  # spread the load, stay far below provider limits


def _loop(name: str, interval: float, task: Callable, stop: threading.Event) -> None:
    while not stop.is_set():
        started = time.monotonic()
        try:
            task()
        except Exception as exc:
            logger.warning("Background %s failed: %s", name, exc)
        stop.wait(max(1.0, interval - (time.monotonic() - started)))


def _websocket_loop(stop: threading.Event) -> None:
    """Coinbase ticker websocket -> tick store. Optional: needs websocket-client."""
    try:
        import websocket  # type: ignore
    except ImportError:
        logger.info("websocket-client not installed; using REST polling for ticks")
        return
    products = sorted(COINBASE_PRODUCTS & set(market_data.all_symbols()))
    backoff = 5
    while not stop.is_set():

        def on_open(ws):
            ws.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "product_ids": products,
                        "channels": ["ticker"],
                    }
                )
            )

        def on_message(_ws, message):
            handle_ws_message(message)

        try:
            ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as exc:
            logger.warning("Coinbase websocket error: %s", type(exc).__name__)
        stop.wait(backoff)
        backoff = min(backoff * 2, WS_MAX_BACKOFF_SECONDS)


def handle_ws_message(message) -> bool:
    """Store a Coinbase ticker message; returns whether a tick was stored."""
    try:
        data = json.loads(message)
        if data.get("type") != "ticker":
            return False
        from services.providers.crypto import _iso_to_epoch

        tick_store.update(
            data["product_id"],
            float(data["price"]),
            "coinbase",
            _iso_to_epoch(data.get("time")),
        )
        return True
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def start(app, stop: Optional[threading.Event] = None) -> bool:
    """Start the workers once per process; returns False if already started."""
    global _started
    with _start_lock:
        if _started:
            return False
        _started = True
    stop = stop or threading.Event()
    specs = [
        (
            "overview",
            OVERVIEW_INTERVAL_SECONDS,
            lambda: _in_context(app, refresh_overview),
        ),
        ("ticks", TICK_INTERVAL_SECONDS, lambda: _in_context(app, refresh_ticks)),
        (
            "history",
            HISTORY_INTERVAL_SECONDS,
            lambda: _in_context(app, refresh_history),
        ),
    ]
    for name, interval, task in specs:
        thread = threading.Thread(
            target=_loop,
            args=(name, interval, task, stop),
            name=f"bg-{name}",
            daemon=True,
        )
        thread.start()
        _threads.append(thread)
    if os.getenv("ENABLE_WEBSOCKET", "1").lower() not in ("0", "false", "no"):
        thread = threading.Thread(
            target=_websocket_loop, args=(stop,), name="bg-ws", daemon=True
        )
        thread.start()
        _threads.append(thread)
    logger.info("Background refreshers started")
    return True
