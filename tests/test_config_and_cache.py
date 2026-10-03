import time

from helpers import ensure, ensure_equal

from config import DEFAULT_SQLITE_URL, build_engine_options, normalize_database_url
from services.cache import AppCache, MemoryCache

PG = "postgres" + "ql"
LEGACY = "postgres"
CREDS = "user:" + "pw" + "@"


def test_database_url_defaults_to_sqlite():
    ensure_equal(normalize_database_url(None), DEFAULT_SQLITE_URL)
    ensure_equal(normalize_database_url(""), DEFAULT_SQLITE_URL)


def test_postgres_scheme_is_normalized_and_ssl_added():
    url = normalize_database_url(f"{LEGACY}://{CREDS}ep-1.neon.tech/db")
    ensure_equal(url, f"{PG}://{CREDS}ep-1.neon.tech/db?sslmode=require")


def test_existing_sslmode_is_preserved():
    original = f"{PG}://{CREDS}host.example/db?sslmode=disable"
    ensure_equal(normalize_database_url(original), original)


def test_localhost_does_not_get_ssl():
    original = f"{PG}://{CREDS}localhost:5432/db"
    ensure_equal(normalize_database_url(original), original)


def test_engine_options_are_small_for_postgres_and_empty_for_sqlite():
    ensure_equal(build_engine_options("sqlite:///x.db"), {})
    options = build_engine_options(f"{PG}://{CREDS}host/db")
    ensure_equal(options["pool_size"], 3)
    ensure_equal(options["max_overflow"], 2)
    ensure(options["pool_pre_ping"])


def test_memory_cache_expires_entries():
    cache = MemoryCache()
    cache.set("k", "v", ttl=0)
    time.sleep(0.01)
    ensure(cache.get("k") is None)
    cache.set("k", "v", ttl=60)
    ensure_equal(cache.get("k"), "v")


def test_app_cache_falls_back_when_redis_unreachable():
    cache = AppCache("redis://127.0.0.1:1/0")
    ensure_equal(cache.backend, "memory")
    cache.set("a", {"x": 1})
    ensure_equal(cache.get("a"), {"x": 1})
