"""
Unit tests for InputPeerCache.

Tests LRU eviction, TTL expiration, metrics, and thread safety.
Part of TIER C optimization (C-3).
"""

import time
from unittest.mock import Mock

import pytest
from telethon.tl.types import InputPeerUser, InputPeerChannel, InputPeerChat

from src.input_peer_cache import InputPeerCache


@pytest.fixture
def cache():
    """Create a cache instance for testing."""
    return InputPeerCache(max_size=3, ttl_seconds=1.0)


@pytest.fixture
def sample_peers():
    """Create sample InputPeer objects."""
    return {
        "user": InputPeerUser(user_id=123456, access_hash=999888),
        "channel": InputPeerChannel(channel_id=789012, access_hash=111222),
        "chat": InputPeerChat(chat_id=345678),
    }


def test_cache_initialization():
    """Test cache initializes with correct parameters."""
    cache = InputPeerCache(max_size=100, ttl_seconds=3600.0)
    
    assert len(cache) == 0
    metrics = cache.get_metrics()
    assert metrics["size"] == 0
    assert metrics["max_size"] == 100
    assert metrics["hits"] == 0
    assert metrics["misses"] == 0


def test_cache_hit_and_miss(cache, sample_peers):
    """Test basic cache hit and miss behavior."""
    user_peer = sample_peers["user"]
    
    # Miss: cache is empty
    result = cache.get(123456)
    assert result is None
    
    # Store peer
    cache.set(123456, user_peer)
    
    # Hit: peer is in cache
    result = cache.get(123456)
    assert result is not None
    assert result.user_id == 123456
    assert result.access_hash == 999888
    
    # Verify metrics
    metrics = cache.get_metrics()
    assert metrics["hits"] == 1
    assert metrics["misses"] == 1
    assert metrics["hit_rate"] == 50.0


def test_lru_eviction(cache, sample_peers):
    """Test LRU eviction when cache exceeds max_size."""
    # Fill cache to max_size (3)
    cache.set(1, sample_peers["user"])
    cache.set(2, sample_peers["channel"])
    cache.set(3, sample_peers["chat"])
    
    assert len(cache) == 3
    
    # Add 4th item, should evict oldest (ID 1)
    cache.set(4, sample_peers["user"])
    
    assert len(cache) == 3
    assert cache.get(1) is None  # Evicted
    assert cache.get(2) is not None  # Still present
    assert cache.get(3) is not None  # Still present
    assert cache.get(4) is not None  # Newly added
    
    # Verify eviction metric
    metrics = cache.get_metrics()
    assert metrics["evictions"] == 1


def test_lru_move_to_end(cache, sample_peers):
    """Test that accessing an item moves it to the end (most recently used)."""
    cache.set(1, sample_peers["user"])
    cache.set(2, sample_peers["channel"])
    cache.set(3, sample_peers["chat"])
    
    # Access ID 1 to move it to end
    cache.get(1)
    
    # Add 4th item, should evict ID 2 (now oldest)
    cache.set(4, sample_peers["user"])
    
    assert cache.get(1) is not None  # Kept (was accessed)
    assert cache.get(2) is None       # Evicted (oldest)
    assert cache.get(3) is not None  # Kept
    assert cache.get(4) is not None  # Kept


def test_ttl_expiration(cache, sample_peers):
    """Test that entries expire after TTL."""
    cache.set(123, sample_peers["user"])
    
    # Entry should be valid immediately
    assert cache.get(123) is not None
    
    # Wait for TTL expiration (1 second)
    time.sleep(1.1)
    
    # Entry should be expired
    result = cache.get(123)
    assert result is None
    
    # Verify expiration metric
    metrics = cache.get_metrics()
    assert metrics["expirations"] == 1


def test_evict_expired(cache, sample_peers):
    """Test manual eviction of expired entries."""
    cache.set(1, sample_peers["user"])
    cache.set(2, sample_peers["channel"])
    cache.set(3, sample_peers["chat"])
    
    # Wait for TTL expiration
    time.sleep(1.1)
    
    # Manually evict expired entries
    evicted_count = cache.evict_expired()
    
    assert evicted_count == 3
    assert len(cache) == 0
    
    # Verify expiration metric
    metrics = cache.get_metrics()
    assert metrics["expirations"] == 3


def test_clear_cache(cache, sample_peers):
    """Test clearing the entire cache."""
    cache.set(1, sample_peers["user"])
    cache.set(2, sample_peers["channel"])
    cache.set(3, sample_peers["chat"])
    
    assert len(cache) == 3
    
    cache.clear()
    
    assert len(cache) == 0
    assert cache.get(1) is None
    assert cache.get(2) is None
    assert cache.get(3) is None


def test_metrics_hit_rate():
    """Test hit rate calculation."""
    cache = InputPeerCache(max_size=10, ttl_seconds=3600)
    peer = InputPeerUser(user_id=1, access_hash=123)
    
    cache.set(1, peer)
    
    # 5 hits, 5 misses = 50% hit rate
    for _ in range(5):
        cache.get(1)  # Hit
        cache.get(999)  # Miss
    
    metrics = cache.get_metrics()
    assert metrics["hits"] == 5
    assert metrics["misses"] == 5
    assert metrics["hit_rate"] == 50.0
    assert metrics["total_requests"] == 10



def test_update_existing_entry(cache, sample_peers):
    """Test that updating an existing entry moves it to end and updates timestamp."""
    cache.set(1, sample_peers["user"])
    cache.set(2, sample_peers["channel"])
    
    # Wait a bit
    time.sleep(0.5)
    
    # Update ID 1 (should move to end and refresh timestamp)
    cache.set(1, sample_peers["chat"])
    
    # Add 3rd and 4th items
    cache.set(3, sample_peers["user"])
    cache.set(4, sample_peers["channel"])
    
    # ID 2 should be evicted (oldest), ID 1 should be kept (was updated)
    assert cache.get(1) is not None
    assert cache.get(2) is None  # Evicted
    assert cache.get(3) is not None
    assert cache.get(4) is not None


def test_repr_output(cache, sample_peers):
    """Test string representation of cache."""
    cache.set(1, sample_peers["user"])
    cache.get(1)  # Hit
    cache.get(999)  # Miss
    
    repr_str = repr(cache)
    
    assert "InputPeerCache" in repr_str
    assert "size=1/3" in repr_str
    assert "hits=1" in repr_str
    assert "misses=1" in repr_str
    assert "hit_rate=50.0%" in repr_str


def test_different_peer_types(sample_peers):
    """Test caching different types of InputPeer objects."""
    cache = InputPeerCache(max_size=10, ttl_seconds=3600)
    
    # Store different peer types
    cache.set(1, sample_peers["user"])
    cache.set(2, sample_peers["channel"])
    cache.set(3, sample_peers["chat"])
    
    # Retrieve and verify types
    user = cache.get(1)
    assert isinstance(user, InputPeerUser)
    assert user.user_id == 123456
    
    channel = cache.get(2)
    assert isinstance(channel, InputPeerChannel)
    assert channel.channel_id == 789012
    
    chat = cache.get(3)
    assert isinstance(chat, InputPeerChat)
    assert chat.chat_id == 345678
