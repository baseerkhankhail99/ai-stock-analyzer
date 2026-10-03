import requests
from helpers import ensure, ensure_equal

from services import news


class DummyResponse:
    def __init__(self, text="", json_data=None, status_code=200):
        self.text = text
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def _rss(
    title,
    source="Yahoo Finance",
    link="https://example.com/item",
    pub_date="Mon, 01 Jan 2024 12:00:00 GMT",
):
    return f"""
    <rss><channel>
        <item>
            <title>{title}</title>
            <link>{link}</link>
            <source>{source}</source>
            <pubDate>{pub_date}</pubDate>
        </item>
    </channel></rss>
    """


def test_fetch_headlines_scores_sentiment_and_uses_cache(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if "finance.yahoo.com" in url:
            return DummyResponse(
                text=_rss(
                    "Apple posts great profit growth",
                    source="Yahoo Finance",
                    link="https://example.com/yahoo-apple",
                )
            )
        if "news.google.com" in url:
            return DummyResponse(
                text=_rss(
                    "Apple shares rally after upbeat outlook",
                    source="Google News",
                    link="https://example.com/google-apple",
                )
            )
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(news.requests, "get", fake_get)

    headlines = news.fetch_headlines("AAPL", limit=2)
    ensure_equal(len(headlines), 2)
    ensure(all(item["sentiment"] > 0 for item in headlines))

    summary = news.news_sentiment("AAPL")
    ensure(summary["available"])
    ensure(summary["score"] is not None and summary["score"] > 0)
    ensure_equal(summary["label"], "positive")
    ensure_equal(len(calls), 2)


def test_news_sentiment_gracefully_handles_total_failure_and_negative_caches(
    monkeypatch,
):
    calls = []

    def failing_get(url, headers=None, timeout=None):
        calls.append(url)
        raise requests.RequestException("boom")

    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(news.requests, "get", failing_get)

    first = news.news_sentiment("ZZZZ")
    second = news.news_sentiment("ZZZZ")

    ensure_equal(first["available"], False)
    ensure_equal(first["count"], 0)
    ensure(first["error"])
    ensure_equal(second, first)
    ensure_equal(len(calls), 2)


def test_invalid_symbol_returns_safe_empty_payload():
    ensure_equal(news.fetch_headlines("bad symbol!"), [])
    payload = news.news_sentiment("bad symbol!")
    ensure_equal(payload["available"], False)
    ensure_equal(payload["score"], None)
    ensure_equal(payload["label"], None)
    ensure_equal(payload["count"], 0)
    ensure(payload["error"])


def test_rss_parsing_rejects_doctype(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        if "finance.yahoo.com" in url:
            return DummyResponse(text="<!DOCTYPE rss><rss><channel></channel></rss>")
        if "news.google.com" in url:
            raise requests.RequestException("offline")
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(news.requests, "get", fake_get)

    payload = news.news_sentiment("IBM")
    ensure_equal(payload["available"], False)
    ensure_equal(payload["count"], 0)
    ensure("unsafe XML" in payload["error"])
