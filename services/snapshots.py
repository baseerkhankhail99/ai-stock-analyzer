"""Persist last-known-good market data so it survives restarts."""

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from flask import has_app_context

from models import MarketSnapshot, db

logger = logging.getLogger(__name__)

MIN_WRITE_INTERVAL_SECONDS = 300

_last_write: Dict[str, float] = {}
_lock = threading.Lock()


def save_snapshot(key: str, payload: Any) -> None:
    """Best-effort, throttled write; never raises."""
    if not has_app_context():
        return
    with _lock:
        last = _last_write.get(key)
        if last is not None and time.monotonic() - last < MIN_WRITE_INTERVAL_SECONDS:
            return
    try:
        row = db.session.get(MarketSnapshot, key)
        raw = json.dumps(payload, default=str)
        if row is None:
            db.session.add(MarketSnapshot(key=key, payload=raw))
        else:
            row.payload, row.updated_at = raw, datetime.utcnow()
        db.session.commit()
        with _lock:
            _last_write[key] = time.monotonic()
    except Exception as exc:
        db.session.rollback()
        logger.warning("Could not persist snapshot %s: %s", key, type(exc).__name__)


def load_snapshot(key: str) -> Optional[Any]:
    if not has_app_context():
        return None
    try:
        row = db.session.get(MarketSnapshot, key)
        return json.loads(row.payload) if row is not None else None
    except Exception as exc:
        db.session.rollback()
        logger.warning("Could not load snapshot %s: %s", key, type(exc).__name__)
        return None
