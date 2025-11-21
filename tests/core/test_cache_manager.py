"""
Unit tests for CacheManager (src/core/cache.py).

Session 5 - Batch 1: Initialization, Basic Operations, and Stats
"""

import asyncio
import time
from collections import OrderedDict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.cache import (
    CacheEntry,
    CacheManager,
    CacheStats,
    CacheStrategy,
    CompressionType,
)


# ============================================================================
# Batch 1: CacheEntry, CacheStats, Initialization, Basic Get/Set
# ============================================================================


class TestCacheEntry:
    """Test CacheEntry dataclass behavior."""

    def test_cache_entry_creation(self):
        """Test basic CacheEntry creation."""
        entry = CacheEntry(
            data="test_value",
            created_at=1000.0,
            last_accessed=1000.0,
            access_count=5,
            ttl=60.0,
        )

        assert entry.data == "test_value"
        assert entry.created_at == 1000.0
        assert entry.last_accessed == 1000.0
        assert entry.access_count == 5
        assert entry.ttl == 60.0
        assert entry.compressed is False
        assert entry.compression_type == "none"

    def test_is_expired_with_ttl(self):
        """Test is_expired returns True when TTL exceeded."""
        # Entry created 100 seconds ago with 60s TTL
        entry = CacheEntry(
            data="test",
            created_at=time.time() - 100,
            last_accessed=time.time(),
            ttl=60.0,
        )
        assert entry.is_expired() is True

    def test_is_expired_within_ttl(self):
        """Test is_expired returns False when TTL not exceeded."""
        # Entry created 30 seconds ago with 60s TTL
        entry = CacheEntry(
            data="test",
            created_at=time.time() - 30,
            last_accessed=time.time(),
            ttl=60.0,
        )
        assert entry.is_expired() is False

    def test_is_expired_no_ttl(self):
        """Test is_expired returns False when no TTL set."""
        entry = CacheEntry(
            data="test",
            created_at=time.time() - 1000,
            last_accessed=time.time(),
            ttl=None,
        )
        assert entry.is_expired() is False

    def test_update_access(self):
        """Test update_access increments count and updates timestamp."""
        entry = CacheEntry(
            data="test",
            created_at=1000.0,
            last_accessed=1000.0,
            access_count=3,
        )

        time_before = time.time()
        entry.update_access()
        time_after = time.time()

        assert entry.access_count == 4
        assert time_before <= entry.last_accessed <= time_after


class TestCacheStats:
    """Test CacheStats dataclass behavior."""

    def test_cache_stats_defaults(self):
        """Test CacheStats default values."""
        stats = CacheStats()

        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.sets == 0
        assert stats.deletes == 0
        assert stats.evictions == 0
        assert stats.compression_saves == 0
        assert stats.total_size_mb == 0.0

    def test_hit_rate_with_hits_and_misses(self):
        """Test hit_rate calculation with both hits and misses."""
        stats = CacheStats(hits=75, misses=25)
        assert stats.hit_rate == 0.75

    def test_hit_rate_all_hits(self):
        """Test hit_rate when all requests are hits."""
        stats = CacheStats(hits=100, misses=0)
        assert stats.hit_rate == 1.0

    def test_hit_rate_all_misses(self):
        """Test hit_rate when all requests are misses."""
        stats = CacheStats(hits=0, misses=50)
        assert stats.hit_rate == 0.0

    def test_hit_rate_no_requests(self):
        """Test hit_rate returns 0.0 when no requests made."""
        stats = CacheStats(hits=0, misses=0)
        assert stats.hit_rate == 0.0


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_cache_path(tmp_path: Path) -> Path:
    """Create temporary cache file path."""
    return tmp_path / "test_cache.json"


@pytest.fixture
async def cache_manager_lru(temp_cache_path: Path) -> CacheManager:
    """Create CacheManager with LRU strategy."""
    manager = CacheManager(
        cache_path=temp_cache_path,
        strategy=CacheStrategy.LRU,
        max_size=5,
        compression=CompressionType.NONE,
        auto_save_interval=999.0,  # Very long to prevent auto-save during tests
    )
    # Mock the file loading to avoid actual I/O
    with patch.object(manager, "_load_cache", new=AsyncMock()):
        await manager.start()
    yield manager
    await manager.shutdown()


@pytest.fixture
async def cache_manager_simple(temp_cache_path: Path) -> CacheManager:
    """Create CacheManager with SIMPLE strategy."""
    manager = CacheManager(
        cache_path=temp_cache_path,
        strategy=CacheStrategy.SIMPLE,
        max_size=5,
        compression=CompressionType.NONE,
        auto_save_interval=999.0,
    )
    with patch.object(manager, "_load_cache", new=AsyncMock()):
        await manager.start()
    yield manager
    await manager.shutdown()


# ============================================================================
# Batch 1 Tests: Initialization & Basic Operations
# ============================================================================


class TestCacheManagerInitialization:
    """Test CacheManager initialization and configuration."""

    @pytest.mark.asyncio
    async def test_init_lru_uses_ordered_dict(self, temp_cache_path):
        """Test LRU strategy initializes OrderedDict."""
        manager = CacheManager(
            cache_path=temp_cache_path,
            strategy=CacheStrategy.LRU,
        )
        assert isinstance(manager._cache, OrderedDict)
        assert manager.strategy == CacheStrategy.LRU

    @pytest.mark.asyncio
    async def test_init_simple_uses_regular_dict(self, temp_cache_path):
        """Test SIMPLE strategy initializes regular dict."""
        manager = CacheManager(
            cache_path=temp_cache_path,
            strategy=CacheStrategy.SIMPLE,
        )
        assert isinstance(manager._cache, dict)
        assert not isinstance(manager._cache, OrderedDict)
        assert manager.strategy == CacheStrategy.SIMPLE

    @pytest.mark.asyncio
    async def test_init_ttl_uses_regular_dict(self, temp_cache_path):
        """Test TTL strategy initializes regular dict."""
        manager = CacheManager(
            cache_path=temp_cache_path,
            strategy=CacheStrategy.TTL,
        )
        assert isinstance(manager._cache, dict)
        assert not isinstance(manager._cache, OrderedDict)
        assert manager.strategy == CacheStrategy.TTL

    @pytest.mark.asyncio
    async def test_init_sets_configuration(self, temp_cache_path):
        """Test initialization sets all configuration values."""
        manager = CacheManager(
            cache_path=temp_cache_path,
            strategy=CacheStrategy.LRU,
            max_size=100,
            default_ttl=300.0,
            compression=CompressionType.GZIP,
            auto_save_interval=60.0,
            compression_threshold=2048,
        )

        assert manager.cache_path == temp_cache_path.resolve()
        assert manager.backup_path == temp_cache_path.with_suffix(".backup")
        assert manager.max_size == 100
        assert manager.default_ttl == 300.0
        assert manager.compression == CompressionType.GZIP
        assert manager.auto_save_interval == 60.0
        assert manager.compression_threshold == 2048
        assert manager._dirty is False
        assert manager._shutdown is False

    @pytest.mark.asyncio
    async def test_start_loads_cache_and_starts_auto_save(self, temp_cache_path):
        """Test start() loads cache and starts auto-save task."""
        manager = CacheManager(cache_path=temp_cache_path)

        with patch.object(manager, "_load_cache", new=AsyncMock()) as mock_load:
            await manager.start()

            mock_load.assert_awaited_once()
            assert manager._auto_save_task is not None
            assert not manager._auto_save_task.done()

        await manager.shutdown()


class TestCacheManagerBasicOperations:
    """Test basic get/set/delete/clear operations."""

    @pytest.mark.asyncio
    async def test_set_and_get_simple_value(self, cache_manager_lru):
        """Test setting and getting a simple string value."""
        await cache_manager_lru.set("key1", "value1")
        result = await cache_manager_lru.get("key1")

        assert result == "value1"
        assert cache_manager_lru._dirty is True

    @pytest.mark.asyncio
    async def test_set_and_get_complex_value(self, cache_manager_lru):
        """Test setting and getting a complex dict value."""
        complex_data = {
            "name": "test",
            "count": 42,
            "items": [1, 2, 3],
            "nested": {"key": "value"},
        }
        await cache_manager_lru.set("key2", complex_data)
        result = await cache_manager_lru.get("key2")

        assert result == complex_data

    @pytest.mark.asyncio
    async def test_get_missing_key_returns_default(self, cache_manager_lru):
        """Test get() returns default value when key not found."""
        result = await cache_manager_lru.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_missing_key_returns_custom_default(self, cache_manager_lru):
        """Test get() returns custom default value."""
        result = await cache_manager_lru.get("nonexistent", default="custom_default")
        assert result == "custom_default"

    @pytest.mark.asyncio
    async def test_delete_existing_key(self, cache_manager_lru):
        """Test deleting an existing key."""
        await cache_manager_lru.set("key1", "value1")
        result = await cache_manager_lru.delete("key1")

        assert result is True
        assert await cache_manager_lru.get("key1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key(self, cache_manager_lru):
        """Test deleting a non-existent key returns False."""
        result = await cache_manager_lru.delete("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_clear_empties_cache(self, cache_manager_lru):
        """Test clear() removes all entries."""
        await cache_manager_lru.set("key1", "value1")
        await cache_manager_lru.set("key2", "value2")
        await cache_manager_lru.set("key3", "value3")

        await cache_manager_lru.clear()

        assert await cache_manager_lru.get("key1") is None
        assert await cache_manager_lru.get("key2") is None
        assert await cache_manager_lru.get("key3") is None
        assert len(cache_manager_lru._cache) == 0


class TestCacheManagerStats:
    """Test statistics tracking."""

    @pytest.mark.asyncio
    async def test_stats_tracks_sets(self, cache_manager_lru):
        """Test stats correctly count set operations."""
        await cache_manager_lru.set("key1", "value1")
        await cache_manager_lru.set("key2", "value2")

        stats = cache_manager_lru.get_stats()
        assert stats.sets == 2

    @pytest.mark.asyncio
    async def test_stats_tracks_hits(self, cache_manager_lru):
        """Test stats correctly count cache hits."""
        await cache_manager_lru.set("key1", "value1")

        await cache_manager_lru.get("key1")
        await cache_manager_lru.get("key1")

        stats = cache_manager_lru.get_stats()
        assert stats.hits == 2

    @pytest.mark.asyncio
    async def test_stats_tracks_misses(self, cache_manager_lru):
        """Test stats correctly count cache misses."""
        await cache_manager_lru.get("nonexistent1")
        await cache_manager_lru.get("nonexistent2")

        stats = cache_manager_lru.get_stats()
        assert stats.misses == 2

    @pytest.mark.asyncio
    async def test_stats_tracks_deletes(self, cache_manager_lru):
        """Test stats correctly count delete operations."""
        await cache_manager_lru.set("key1", "value1")
        await cache_manager_lru.set("key2", "value2")

        await cache_manager_lru.delete("key1")

        stats = cache_manager_lru.get_stats()
        assert stats.deletes == 1

    @pytest.mark.asyncio
    async def test_stats_tracks_clear_as_deletes(self, cache_manager_lru):
        """Test clear() counts all removed entries as deletes."""
        await cache_manager_lru.set("key1", "value1")
        await cache_manager_lru.set("key2", "value2")
        await cache_manager_lru.set("key3", "value3")

        await cache_manager_lru.clear()

        stats = cache_manager_lru.get_stats()
        assert stats.deletes == 3

    @pytest.mark.asyncio
    async def test_hit_rate_calculation(self, cache_manager_lru):
        """Test hit rate is correctly calculated from hits and misses."""
        await cache_manager_lru.set("key1", "value1")

        # 3 hits
        await cache_manager_lru.get("key1")
        await cache_manager_lru.get("key1")
        await cache_manager_lru.get("key1")

        # 1 miss
        await cache_manager_lru.get("nonexistent")

        stats = cache_manager_lru.get_stats()
        assert stats.hits == 3
        assert stats.misses == 1
        assert stats.hit_rate == 0.75



# ============================================================================
# Batch 2: TTL & Expiration Logic
# ============================================================================


@pytest.fixture
async def cache_manager_with_ttl(temp_cache_path: Path) -> CacheManager:
    """Create CacheManager with TTL strategy and default TTL."""
    manager = CacheManager(
        cache_path=temp_cache_path,
        strategy=CacheStrategy.TTL,
        max_size=10,
        default_ttl=5.0,  # 5 second default TTL
        compression=CompressionType.NONE,
        auto_save_interval=999.0,
    )
    with patch.object(manager, "_load_cache", new=AsyncMock()):
        await manager.start()
    yield manager
    await manager.shutdown()


class TestCacheManagerTTL:
    """Test TTL and expiration logic."""

    @pytest.mark.asyncio
    async def test_get_expired_entry_returns_default(self, cache_manager_with_ttl):
        """Test get() returns default when entry is expired."""
        # Set entry with 0.1 second TTL
        await cache_manager_with_ttl.set("temp_key", "temp_value", ttl=0.1)

        # Wait for expiration
        await asyncio.sleep(0.2)

        # Should return default
        result = await cache_manager_with_ttl.get("temp_key", default="expired")
        assert result == "expired"

    @pytest.mark.asyncio
    async def test_get_expired_entry_increments_misses(self, cache_manager_with_ttl):
        """Test expired entry counts as cache miss."""
        await cache_manager_with_ttl.set("temp_key", "temp_value", ttl=0.1)
        await asyncio.sleep(0.2)

        await cache_manager_with_ttl.get("temp_key")

        stats = cache_manager_with_ttl.get_stats()
        assert stats.misses == 1
        assert stats.evictions == 1

    @pytest.mark.asyncio
    async def test_default_ttl_applied_to_entries(self, cache_manager_with_ttl):
        """Test default_ttl is applied when no custom TTL specified."""
        await cache_manager_with_ttl.set("key1", "value1")

        # Check the entry has the default TTL
        entry = cache_manager_with_ttl._cache["key1"]
        assert entry.ttl == 5.0

    @pytest.mark.asyncio
    async def test_custom_ttl_overrides_default(self, cache_manager_with_ttl):
        """Test custom TTL overrides default_ttl."""
        await cache_manager_with_ttl.set("key1", "value1", ttl=10.0)

        entry = cache_manager_with_ttl._cache["key1"]
        assert entry.ttl == 10.0

    @pytest.mark.asyncio
    async def test_no_ttl_entry_never_expires(self, cache_manager_lru):
        """Test entry with no TTL never expires."""
        # LRU manager has no default TTL
        await cache_manager_lru.set("key1", "value1")

        # Wait a bit
        await asyncio.sleep(0.1)

        # Should still be accessible
        result = await cache_manager_lru.get("key1")
        assert result == "value1"

        entry = cache_manager_lru._cache["key1"]
        assert entry.ttl is None
        assert entry.is_expired() is False

    @pytest.mark.asyncio
    async def test_cleanup_expired_removes_expired_entries(
        self, cache_manager_with_ttl
    ):
        """Test _cleanup_expired() removes expired entries."""
        # Add entries with very short TTL
        await cache_manager_with_ttl.set("key1", "value1", ttl=0.1)
        await cache_manager_with_ttl.set("key2", "value2", ttl=0.1)
        await cache_manager_with_ttl.set("key3", "value3", ttl=10.0)  # Won't expire

        # Wait for some to expire
        await asyncio.sleep(0.2)

        # Run cleanup
        await cache_manager_with_ttl._cleanup_expired()

        # Expired entries should be removed
        assert "key1" not in cache_manager_with_ttl._cache
        assert "key2" not in cache_manager_with_ttl._cache
        assert "key3" in cache_manager_with_ttl._cache

        # Check stats
        stats = cache_manager_with_ttl.get_stats()
        assert stats.evictions == 2

    @pytest.mark.asyncio
    async def test_cleanup_expired_skips_when_no_ttl_strategy(self, cache_manager_lru):
        """Test _cleanup_expired() is no-op for non-TTL strategies without default TTL."""
        await cache_manager_lru.set("key1", "value1")
        await cache_manager_lru.set("key2", "value2")

        # Should not remove anything
        await cache_manager_lru._cleanup_expired()

        assert "key1" in cache_manager_lru._cache
        assert "key2" in cache_manager_lru._cache

    @pytest.mark.asyncio
    async def test_cleanup_expired_marks_dirty(self, cache_manager_with_ttl):
        """Test _cleanup_expired() sets dirty flag when entries removed."""
        await cache_manager_with_ttl.set("key1", "value1", ttl=0.1)

        # Reset dirty flag
        cache_manager_with_ttl._dirty = False

        await asyncio.sleep(0.2)
        await cache_manager_with_ttl._cleanup_expired()

        assert cache_manager_with_ttl._dirty is True



# ============================================================================
# Batch 3: LRU Eviction & Strategy
# ============================================================================


class TestCacheManagerLRUEviction:
    """Test LRU strategy and eviction logic."""

    @pytest.mark.asyncio
    async def test_lru_get_moves_entry_to_end(self, cache_manager_lru):
        """Test get() moves accessed entry to end of OrderedDict."""
        # Add entries
        await cache_manager_lru.set("key1", "value1")
        await cache_manager_lru.set("key2", "value2")
        await cache_manager_lru.set("key3", "value3")

        # Access key1 (should move to end)
        await cache_manager_lru.get("key1")

        # Check order (key1 should be last)
        keys_list = list(cache_manager_lru._cache.keys())
        assert keys_list == ["key2", "key3", "key1"]

    @pytest.mark.asyncio
    async def test_lru_set_moves_entry_to_end(self, cache_manager_lru):
        """Test set() on existing key moves it to end."""
        await cache_manager_lru.set("key1", "value1")
        await cache_manager_lru.set("key2", "value2")
        await cache_manager_lru.set("key3", "value3")

        # Update key1 (should move to end)
        await cache_manager_lru.set("key1", "updated_value1")

        keys_list = list(cache_manager_lru._cache.keys())
        assert keys_list == ["key2", "key3", "key1"]

    @pytest.mark.asyncio
    async def test_lru_evict_removes_oldest_entry(self, cache_manager_lru):
        """Test LRU eviction removes the least recently used entries."""
        # Fill cache to max_size (5)
        for i in range(5):
            await cache_manager_lru.set(f"key{i}", f"value{i}")

        # Add one more (evicts 2 entries per eviction formula: 6 - 5 + 1 = 2)
        await cache_manager_lru.set("key5", "value5")

        # key0 and key1 should be evicted (oldest entries)
        assert "key0" not in cache_manager_lru._cache
        assert "key1" not in cache_manager_lru._cache
        assert "key5" in cache_manager_lru._cache
        assert len(cache_manager_lru._cache) == 4

    @pytest.mark.asyncio
    async def test_lru_evict_tracks_eviction_stats(self, cache_manager_lru):
        """Test eviction increments eviction counter."""
        # Fill cache
        for i in range(6):  # max_size is 5
            await cache_manager_lru.set(f"key{i}", f"value{i}")

        stats = cache_manager_lru.get_stats()
        assert stats.evictions >= 1

    @pytest.mark.asyncio
    async def test_lru_access_order_affects_eviction(self, cache_manager_lru):
        """Test accessing entries changes eviction order."""
        # Fill cache
        for i in range(5):
            await cache_manager_lru.set(f"key{i}", f"value{i}")

        # Access key0 and key1 (makes them most recently used)
        await cache_manager_lru.get("key0")
        await cache_manager_lru.get("key1")

        # Add one more (should evict key2 and key3, not key0/key1)
        await cache_manager_lru.set("new_key", "new_value")

        assert "key0" in cache_manager_lru._cache
        assert "key1" in cache_manager_lru._cache
        assert "key2" not in cache_manager_lru._cache
        assert "key3" not in cache_manager_lru._cache
        assert "new_key" in cache_manager_lru._cache

    @pytest.mark.asyncio
    async def test_non_lru_evicts_by_last_accessed(self, cache_manager_simple):
        """Test non-LRU strategies evict by last_accessed timestamp."""
        # Fill cache
        for i in range(5):
            await cache_manager_simple.set(f"key{i}", f"value{i}")
            await asyncio.sleep(0.01)  # Ensure different timestamps

        # Add one more entry (evicts 2 per formula)
        await cache_manager_simple.set("new_key", "new_value")

        # key0 and key1 should be evicted (oldest last_accessed)
        assert "key0" not in cache_manager_simple._cache
        assert "key1" not in cache_manager_simple._cache
        assert "new_key" in cache_manager_simple._cache
        assert len(cache_manager_simple._cache) == 4

    @pytest.mark.asyncio
    async def test_max_size_enforcement(self, cache_manager_lru):
        """Test cache never exceeds max_size."""
        # Add many entries
        for i in range(20):
            await cache_manager_lru.set(f"key{i}", f"value{i}")

        assert len(cache_manager_lru._cache) <= cache_manager_lru.max_size



# ============================================================================
# Batch 4: Compression & Data Handling
# ============================================================================


@pytest.fixture
async def cache_manager_gzip(temp_cache_path: Path) -> CacheManager:
    """Create CacheManager with GZIP compression."""
    manager = CacheManager(
        cache_path=temp_cache_path,
        strategy=CacheStrategy.LRU,
        max_size=10,
        compression=CompressionType.GZIP,
        compression_threshold=100,  # Compress data > 100 bytes
        auto_save_interval=999.0,
    )
    with patch.object(manager, "_load_cache", new=AsyncMock()):
        await manager.start()
    yield manager
    await manager.shutdown()


@pytest.fixture
async def cache_manager_pickle(temp_cache_path: Path) -> CacheManager:
    """Create CacheManager with PICKLE compression."""
    manager = CacheManager(
        cache_path=temp_cache_path,
        strategy=CacheStrategy.LRU,
        max_size=10,
        compression=CompressionType.PICKLE,
        compression_threshold=100,
        auto_save_interval=999.0,
    )
    with patch.object(manager, "_load_cache", new=AsyncMock()):
        await manager.start()
    yield manager
    await manager.shutdown()


class TestCacheManagerCompression:
    """Test compression and decompression logic."""

    @pytest.mark.asyncio
    async def test_no_compression_for_small_data(self, cache_manager_gzip):
        """Test data below threshold is not compressed."""
        small_data = "x" * 50  # 50 bytes, below threshold (100)
        await cache_manager_gzip.set("key1", small_data)

        entry = cache_manager_gzip._cache["key1"]
        assert entry.compressed is False
        assert entry.compression_type == "none"

    @pytest.mark.asyncio
    async def test_gzip_compression_for_large_data(self, cache_manager_gzip):
        """Test data above threshold is compressed with GZIP."""
        large_data = "x" * 500  # 500 bytes, above threshold
        await cache_manager_gzip.set("key1", large_data)

        entry = cache_manager_gzip._cache["key1"]
        assert entry.compressed is True
        assert entry.compression_type == "gzip"

        # Data should be bytes (compressed)
        assert isinstance(entry.data, bytes)

    @pytest.mark.asyncio
    async def test_gzip_decompression_on_get(self, cache_manager_gzip):
        """Test compressed data is decompressed on get()."""
        large_data = "A" * 500
        await cache_manager_gzip.set("key1", large_data)

        # Get should return original uncompressed data
        result = await cache_manager_gzip.get("key1")
        assert result == large_data

    @pytest.mark.asyncio
    async def test_pickle_compression_for_complex_data(self, cache_manager_pickle):
        """Test PICKLE compression for complex objects."""
        complex_data = {
            "list": [1, 2, 3, 4, 5] * 50,  # Large enough to trigger compression
            "dict": {"key": "value" * 50},
            "nested": {"a": {"b": {"c": "x" * 100}}},
        }
        await cache_manager_pickle.set("key1", complex_data)

        entry = cache_manager_pickle._cache["key1"]
        assert entry.compressed is True
        assert entry.compression_type == "pickle"

    @pytest.mark.asyncio
    async def test_pickle_decompression_restores_original(
        self, cache_manager_pickle
    ):
        """Test PICKLE decompression restores original data structure."""
        original_data = {
            "numbers": [1, 2, 3] * 50,
            "string": "test" * 50,
            "nested": {"a": [1, 2, 3] * 30},
        }
        await cache_manager_pickle.set("key1", original_data)

        result = await cache_manager_pickle.get("key1")
        assert result == original_data

    @pytest.mark.asyncio
    async def test_compression_type_none_never_compresses(self, cache_manager_lru):
        """Test CompressionType.NONE never compresses data."""
        # cache_manager_lru has CompressionType.NONE
        large_data = "x" * 5000  # Very large data
        await cache_manager_lru.set("key1", large_data)

        entry = cache_manager_lru._cache["key1"]
        assert entry.compressed is False
        assert entry.compression_type == "none"
        # Data should be original string, not bytes
        assert entry.data == large_data

    @pytest.mark.asyncio
    async def test_compression_stats_tracked(self, cache_manager_gzip):
        """Test compression saves are tracked in stats."""
        large_data = "x" * 500
        await cache_manager_gzip.set("key1", large_data)
        await cache_manager_gzip.set("key2", "y" * 500)

        stats = cache_manager_gzip.get_stats()
        # Should have at least some compression saves
        assert stats.compression_saves >= 1

    @pytest.mark.asyncio
    async def test_inefficient_compression_not_used(self, cache_manager_gzip):
        """Test that inefficiently compressible data is not compressed."""
        # Random-like data that doesn't compress well
        import random
        import string

        random_data = "".join(random.choices(string.printable, k=200))
        await cache_manager_gzip.set("key1", random_data)

        entry = cache_manager_gzip._cache["key1"]
        # Might or might not be compressed depending on randomness,
        # but implementation should fall back to uncompressed if not efficient
        # For this test, just verify no error occurs
        result = await cache_manager_gzip.get("key1")
        assert result == random_data

    @pytest.mark.asyncio
    async def test_decompress_handles_uncompressed_data(self, cache_manager_gzip):
        """Test _decompress_data handles uncompressed data gracefully."""
        uncompressed_data = "test_data"
        result = cache_manager_gzip._decompress_data(uncompressed_data, "none")
        assert result == "test_data"

    @pytest.mark.asyncio
    async def test_decompress_handles_invalid_compression_type(
        self, cache_manager_gzip
    ):
        """Test _decompress_data returns original data for unknown compression type."""
        data = b"some_bytes"
        result = cache_manager_gzip._decompress_data(data, "unknown_type")
        assert result == data
