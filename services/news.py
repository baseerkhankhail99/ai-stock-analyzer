import os
import re
import threading
import xml.etree.ElementTree as ET  # nosec B405 - RSS bodies are rejected if they contain DTD/entity declarations before parsing
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlencode

import requests

from services.cache import get_app_cache
from services.market_data import ASSET_NAMES

SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^.=-]{1,15}$")
USER_AGENT = "ai-stock-analyzer/1.0"
REQUEST_TIMEOUT = 6
LLM_TIMEOUT = 8
CACHE_TTL = 900
NEGATIVE_CACHE_TTL = 120
DEFAULT_LIMIT = 10
MAX_ITEMS = 25
POSITIVE_THRESHOLD = 0.05
NEGATIVE_THRESHOLD = -0.05

_SENTIMENT_ANALYZER = None
_SENTIMENT_LOCK = threading.Lock()


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _valid_symbol(symbol: str) -> bool:
    return bool(SYMBOL_PATTERN.fullmatch(symbol or ""))


def _cache_key(kind: str, symbol: str) -> str:
    return f"news:{kind}:{symbol}"


def _get_analyzer():
    global _SENTIMENT_ANALYZER
    if _SENTIMENT_ANALYZER is None:
        with _SENTIMENT_LOCK:
            if _SENTIMENT_ANALYZER is None:
                from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

                _SENTIMENT_ANALYZER = SentimentIntensityAnalyzer()
    return _SENTIMENT_ANALYZER


def _sentiment_label(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score > POSITIVE_THRESHOLD:
        return "positive"
    if score < NEGATIVE_THRESHOLD:
        return "negative"
    return "neutral"


def _headline_record(
    title: str,
    link: str,
    source: str,
    published: str,
) -> Dict[str, Any]:
    compound = _get_analyzer().polarity_scores(title or "").get("compound", 0.0)
    return {
        "title": title or "",
        "link": link or "",
        "source": source or "",
        "published": published or "",
        "sentiment": max(-1.0, min(1.0, float(compound))),
    }


def _request(
    url: str, extra_headers: Optional[Dict[str, str]] = None
) -> requests.Response:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/xml, application/xml",
    }
    if extra_headers:
        headers.update(extra_headers)
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response


def _iso_from_timestamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _iso_from_rss_date(value: str) -> str:
    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, IndexError, OverflowError):
        return ""


def _reject_unsafe_xml(text: str) -> None:
    probe = (text or "").upper()
    if "<!DOCTYPE" in probe or "<!ENTITY" in probe:
        raise ValueError("RSS rejected due to unsafe XML declarations")


def _parse_rss_items(xml_text: str, default_source: str) -> List[Dict[str, Any]]:
    _reject_unsafe_xml(xml_text)
    root = ET.fromstring(
        xml_text
    )  # nosec B314 - feed text is pre-screened for DTD/entity usage
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        source = (item.findtext("source") or default_source).strip() or default_source
        published = _iso_from_rss_date((item.findtext("pubDate") or "").strip())
        if title:
            items.append(_headline_record(title, link, source, published))
    return items


def _is_crypto(symbol: str) -> bool:
    return symbol.endswith("-USD")


def _is_us_equity(symbol: str) -> bool:
    return (
        not _is_crypto(symbol)
        and "^" not in symbol
        and "=" not in symbol
        and "-" not in symbol
    )


def _sorted_limited(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    def sort_key(item: Dict[str, Any]):
        return item.get("published") or ""

    return sorted(items, key=sort_key, reverse=True)[:limit]


def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique = []
    for item in items:
        key = (item.get("link") or "").strip().lower() or (
            item.get("title") or ""
        ).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _fetch_finnhub(symbol: str) -> List[Dict[str, Any]]:
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key or not _is_us_equity(symbol):
        return []
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=7)
    url = "https://finnhub.io/api/v1/company-news?" + urlencode(
        {"symbol": symbol, "from": start.isoformat(), "to": today.isoformat()},
        quote_via=quote_plus,
    )
    payload = _request(url, {"X-Finnhub-Token": api_key}).json()
    items = []
    for entry in payload if isinstance(payload, list) else []:
        title = str(entry.get("headline") or "").strip()
        if not title:
            continue
        items.append(
            _headline_record(
                title=title,
                link=str(entry.get("url") or "").strip(),
                source=str(entry.get("source") or "Finnhub").strip() or "Finnhub",
                published=_iso_from_timestamp(entry.get("datetime")),
            )
        )
    return items


def _fetch_yahoo(symbol: str) -> List[Dict[str, Any]]:
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?" + urlencode(
        {"s": symbol, "region": "US", "lang": "en-US"}, quote_via=quote_plus
    )
    return _parse_rss_items(_request(url).text, "Yahoo Finance")


def _fetch_google(symbol: str) -> List[Dict[str, Any]]:
    query = ASSET_NAMES.get(symbol, symbol)
    url = "https://news.google.com/rss/search?" + urlencode(
        {
            "q": f"{query} when:7d",
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        },
        quote_via=quote_plus,
    )
    return _parse_rss_items(_request(url).text, "Google News")


def _fetch_bundle(symbol: str) -> Dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    if not _valid_symbol(symbol):
        return {
            "available": False,
            "score": None,
            "label": None,
            "count": 0,
            "headlines": [],
            "sources": [],
            "error": "Invalid symbol",
        }

    cache = get_app_cache()
    cached = cache.get(_cache_key("bundle", symbol))
    if cached is not None:
        return cached

    headlines: List[Dict[str, Any]] = []
    used_sources = set()
    errors = []
    fetchers = [] if _is_crypto(symbol) else [_fetch_finnhub]
    fetchers.extend([_fetch_yahoo, _fetch_google])

    for fetcher in fetchers:
        try:
            batch = fetcher(symbol)
            if batch:
                headlines.extend(batch)
                used_sources.update(
                    item.get("source") for item in batch if item.get("source")
                )
        except requests.RequestException:
            errors.append(f"{fetcher.__name__[7:]} unavailable")
        except ValueError as exc:
            errors.append(str(exc))
        except Exception:
            errors.append(f"{fetcher.__name__[7:]} unavailable")

    deduped = _sorted_limited(_dedupe(headlines), MAX_ITEMS)
    score = None
    label = None
    error = ""
    ttl = CACHE_TTL
    if deduped:
        score = sum(item["sentiment"] for item in deduped) / len(deduped)
        label = _sentiment_label(score)
    elif errors:
        ttl = NEGATIVE_CACHE_TTL
        error = "; ".join(errors)

    result = {
        "available": bool(deduped),
        "score": score,
        "label": label,
        "count": len(deduped),
        "headlines": deduped,
        "sources": sorted(used_sources),
        "error": error,
    }
    cache.set(_cache_key("bundle", symbol), result, ttl=ttl)
    return result


def fetch_headlines(symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
    try:
        limit = max(1, min(int(limit), MAX_ITEMS))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    return _fetch_bundle(symbol).get("headlines", [])[:limit]


def news_sentiment(symbol: str) -> Dict[str, Any]:
    bundle = _fetch_bundle(symbol)
    headlines = bundle.get("headlines", [])[:DEFAULT_LIMIT]
    score = None
    label = None
    if headlines:
        score = sum(item["sentiment"] for item in headlines) / len(headlines)
        label = _sentiment_label(score)
    return {
        "available": bool(headlines),
        "score": score,
        "label": label,
        "count": len(headlines),
        "headlines": headlines,
        "sources": sorted(
            {item.get("source") for item in headlines if item.get("source")}
        ),
        "error": bundle.get("error", ""),
    }


def llm_narrative(symbol: str, summary_text: str) -> Optional[str]:
    symbol = _normalize_symbol(symbol)
    if not _valid_symbol(symbol):
        return None
    api_key = os.getenv("LLM_API_KEY")
    if not api_key or not summary_text:
        return None

    cache = get_app_cache()
    cache_key = _cache_key("llm", symbol)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached.get("text")

    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Summarize market-news sentiment briefly. Deterministic numbers and "
                    "metrics provided by the caller are the source of truth."
                ),
            },
            {
                "role": "user",
                "content": f"Symbol: {symbol}\nSummary:\n{summary_text}",
            },
        ],
    }
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer " + api_key, "User-Agent": USER_AGENT},
            timeout=LLM_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        text = (
            (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")
            or ""
        ).strip()
        if not text:
            return None
        cache.set(cache_key, {"text": text}, ttl=CACHE_TTL)
        return text
    except Exception:
        return None
