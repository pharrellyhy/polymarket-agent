"""Tests for TTL cache."""

import time

from polymarket_agent.data.cache import TTLCache


def test_cache_stores_and_retrieves():
    cache = TTLCache(default_ttl=60)
    cache.set("key1", {"data": "value"})
    assert cache.get("key1") == {"data": "value"}


def test_cache_returns_none_for_missing_key():
    cache = TTLCache(default_ttl=60)
    assert cache.get("missing") is None


def test_cache_expires_after_ttl():
    cache = TTLCache(default_ttl=0.1)
    cache.set("key1", "value")
    assert cache.get("key1") == "value"
    time.sleep(0.15)
    assert cache.get("key1") is None


def test_cache_custom_ttl_per_key():
    cache = TTLCache(default_ttl=60)
    cache.set("short", "value", ttl=0.1)
    cache.set("long", "value", ttl=60)
    time.sleep(0.15)
    assert cache.get("short") is None
    assert cache.get("long") == "value"


def test_cache_clear():
    cache = TTLCache(default_ttl=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None
