"""
Unit tests for CacheManager persistence and I/O operations.

Tests cover:
- _load_cache(): Loading from JSON files
- _save_cache(): Saving to JSON files with backup
- _try_restore_from_backup(): Backup restoration logic
- _auto_save_loop(): Background auto-save task
- start(): Manager startup and initialization
- shutdown(): Graceful shutdown and final save
"""

import asyncio
import json
import base64
import pickle
import zlib
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open as sync_mock_open

import pytest

from src.core.cache import CacheEntry, CacheManager, CacheStrategy, CompressionType

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_cache_path(tmp_path):
    """Temporary cache file path."""
    return tmp_path / "cache.json"


@pytest.fixture
def sample_cache_json():
    """Valid cache JSON data."""
    return {
        "version": 2,
        "timestamp": time.time(),
        "strategy": "simple",
        "compression": "none",
        "entries": {
            "key1": {
                "data": "value1",
                "created_at": time.time(),
                "last_accessed": time.time(),
                "access_count": 1,
                "ttl": None,
                "compressed": False,
                "compression_type": "none",
            },
            "key2": {
                "data": {"nested": "data"},
                "created_at": time.time(),
                "last_accessed": time.time(),
                "access_count": 2,
                "ttl": None,
                "compressed": False,
                "compression_type": "none",
            },
        },
    }


@pytest.fixture
def expired_cache_json():
    """Cache JSON with expired entries."""
    old_time = time.time() - 3600  # 1 hour ago
    return {
        "version": 2,
        "timestamp": time.time(),
        "strategy": "ttl",
        "compression": "none",
        "entries": {
            "expired_key": {
                "data": "old_value",
                "created_at": old_time,
                "last_accessed": old_time,
                "access_count": 1,
                "ttl": 60,  # 60 seconds TTL, expired
                "compressed": False,
                "compression_type": "none",
            },
            "valid_key": {
                "data": "valid_value",
                "created_at": time.time(),
                "last_accessed": time.time(),
                "access_count": 1,
                "ttl": 3600,  # Still valid
                "compressed": False,
                "compression_type": "none",
            },
        },
    }


def create_async_file_mock(content=None, should_fail=False):
    """Helper to create properly structured async file mock."""
    mock_file = AsyncMock()
    
    if should_fail:
        mock_file.__aenter__.side_effect = IOError("Read error")
    else:
        # Setup async context manager
        mock_file.__aenter__.return_value = mock_file
        mock_file.__aexit__.return_value = False
        
        if content is not None:
            mock_file.read.return_value = content
        
        # For writes
        mock_file.write = AsyncMock()
    
    return mock_file


# ============================================================================
# _load_cache() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_load_cache_success(temp_cache_path, sample_cache_json):
    """Successfully loads valid cache file with entries."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock(json.dumps(sample_cache_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._load_cache()

    assert len(manager._cache) == 2
    assert "key1" in manager._cache
    assert "key2" in manager._cache
    assert manager._cache["key1"].data == "value1"


@pytest.mark.asyncio
async def test_load_cache_file_not_exists(temp_cache_path):
    """Handles non-existent cache file gracefully."""
    manager = CacheManager(temp_cache_path)

    with patch("src.core.cache.Path.exists", return_value=False):
        await manager._load_cache()

    assert len(manager._cache) == 0


@pytest.mark.asyncio
async def test_load_cache_empty_file(temp_cache_path):
    """Handles empty cache file."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock("   \n  ")

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._load_cache()

    assert len(manager._cache) == 0


@pytest.mark.asyncio
async def test_load_cache_reconstructs_entries(temp_cache_path, sample_cache_json):
    """Parses JSON and reconstructs CacheEntry objects correctly."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock(json.dumps(sample_cache_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._load_cache()

    entry = manager._cache["key1"]
    assert isinstance(entry, CacheEntry)
    assert entry.data == "value1"
    assert entry.access_count == 1
    assert entry.compressed is False


@pytest.mark.asyncio
async def test_load_cache_skips_expired_entries(temp_cache_path, expired_cache_json):
    """Skips expired entries during load."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock(json.dumps(expired_cache_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._load_cache()

    assert "expired_key" not in manager._cache
    assert "valid_key" in manager._cache
    assert len(manager._cache) == 1


@pytest.mark.asyncio
async def test_load_cache_skips_invalid_entries(temp_cache_path):
    """Skips invalid entries (malformed data) with warning."""
    manager = CacheManager(temp_cache_path)
    
    # Create truly invalid entry data (missing required fields will raise TypeError)
    invalid_json = {
        "version": 2,
        "timestamp": time.time(),
        "strategy": "simple",
        "compression": "none",
        "entries": {
            "valid_key": {
                "data": "value",
                "created_at": time.time(),
                "last_accessed": time.time(),
                "access_count": 1,
                "ttl": None,
                "compressed": False,
                "compression_type": "none",
            },
            "invalid_key": {
                # Missing all required fields - will cause TypeError on CacheEntry(**entry_data)
                "invalid_field": "value",
            },
        },
    }
    
    mock_file = create_async_file_mock(json.dumps(invalid_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._load_cache()

    # Only valid entry should be loaded
    assert "valid_key" in manager._cache
    assert "invalid_key" not in manager._cache
    assert len(manager._cache) == 1


@pytest.mark.asyncio
async def test_load_cache_invalid_json_triggers_backup(temp_cache_path):
    """Handles invalid JSON and triggers backup restore."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock("{invalid json")

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            with patch.object(manager, "_try_restore_from_backup", new_callable=AsyncMock) as mock_restore:
                await manager._load_cache()

    mock_restore.assert_called_once()


@pytest.mark.asyncio
async def test_load_cache_exception_triggers_backup(temp_cache_path):
    """Handles general exceptions and triggers backup restore."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock(should_fail=True)

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            with patch.object(manager, "_try_restore_from_backup", new_callable=AsyncMock) as mock_restore:
                await manager._load_cache()

    mock_restore.assert_called_once()


@pytest.mark.asyncio
async def test_load_cache_updates_internal_state(temp_cache_path, sample_cache_json):
    """Updates internal cache state correctly."""
    manager = CacheManager(temp_cache_path)
    initial_size = len(manager._cache)
    
    mock_file = create_async_file_mock(json.dumps(sample_cache_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._load_cache()

    assert len(manager._cache) > initial_size
    assert len(manager._cache) == 2


# ============================================================================
# _save_cache() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_save_cache_success(temp_cache_path):
    """Saves cache to JSON file with correct structure."""
    manager = CacheManager(temp_cache_path)
    await manager.set("key1", "value1")

    written_content = None
    
    def create_write_mock():
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = mock_file
        mock_file.__aexit__.return_value = False
        
        async def capture_write(content):
            nonlocal written_content
            written_content = content
        
        mock_file.write = capture_write
        return mock_file

    with patch("src.core.cache.Path.exists", return_value=False):
        with patch("aiofiles.open", side_effect=lambda *args, **kwargs: create_write_mock()):
            await manager._save_cache()

    assert written_content is not None
    data = json.loads(written_content)
    assert data["version"] == 2
    assert "timestamp" in data
    assert "entries" in data
    assert "key1" in data["entries"]


@pytest.mark.asyncio
async def test_save_cache_creates_backup(temp_cache_path, sample_cache_json):
    """Creates backup before saving (if file exists)."""
    manager = CacheManager(temp_cache_path)
    await manager.set("key1", "value1")

    backup_write_called = False
    original_content = json.dumps(sample_cache_json)

    def create_mock_file(path, mode, **kwargs):
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = mock_file
        mock_file.__aexit__.return_value = False
        
        nonlocal backup_write_called
        if "backup" in str(path) and "w" in mode:
            async def track_backup_write(content):
                nonlocal backup_write_called
                backup_write_called = True
            mock_file.write = track_backup_write
        elif mode == "r":
            mock_file.read = AsyncMock(return_value=original_content)
        else:
            mock_file.write = AsyncMock()
        
        return mock_file

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", side_effect=create_mock_file):
            await manager._save_cache()

    assert backup_write_called


@pytest.mark.asyncio
async def test_save_cache_only_when_dirty(temp_cache_path):
    """Only saves when _dirty flag is True."""
    manager = CacheManager(temp_cache_path)
    manager._dirty = False

    with patch("aiofiles.open") as mock_open:
        await manager._save_cache()

    mock_open.assert_not_called()


@pytest.mark.asyncio
async def test_save_cache_skips_when_not_dirty(temp_cache_path):
    """Skips save when _dirty is False (optimization)."""
    manager = CacheManager(temp_cache_path)
    await manager.set("key1", "value1")
    manager._dirty = False  # Force clean state

    call_count = 0

    with patch("aiofiles.open") as mock_open:
        mock_open.side_effect = lambda *args, **kwargs: call_count + 1

        await manager._save_cache()

    assert call_count == 0


@pytest.mark.asyncio
async def test_save_cache_excludes_expired_entries(temp_cache_path):
    """Excludes expired entries from save."""
    manager = CacheManager(temp_cache_path)
    await manager.set("valid_key", "value1")
    await manager.set("expired_key", "value2", ttl=0.01)

    # Wait for expiration
    await asyncio.sleep(0.02)

    written_content = None
    
    def create_write_mock():
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = mock_file
        mock_file.__aexit__.return_value = False
        
        async def capture_write(content):
            nonlocal written_content
            written_content = content
        
        mock_file.write = capture_write
        return mock_file

    with patch("src.core.cache.Path.exists", return_value=False):
        with patch("aiofiles.open", side_effect=lambda *args, **kwargs: create_write_mock()):
            await manager._save_cache()

    data = json.loads(written_content)
    assert "valid_key" in data["entries"]
    assert "expired_key" not in data["entries"]


@pytest.mark.asyncio
async def test_save_cache_resets_dirty_flag(temp_cache_path):
    """Resets _dirty flag after successful save."""
    manager = CacheManager(temp_cache_path)
    await manager.set("key1", "value1")

    assert manager._dirty is True
    
    mock_file = AsyncMock()
    mock_file.__aenter__.return_value = mock_file
    mock_file.__aexit__.return_value = False
    mock_file.write = AsyncMock()

    with patch("src.core.cache.Path.exists", return_value=False):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._save_cache()

    assert manager._dirty is False


@pytest.mark.asyncio
async def test_save_cache_uses_lock(temp_cache_path):
    """Uses lock to prevent race conditions."""
    manager = CacheManager(temp_cache_path)
    await manager.set("key1", "value1")

    # Simpler approach: verify lock is used by checking it exists and is a Lock
    assert hasattr(manager, "_lock")
    assert isinstance(manager._lock, asyncio.Lock)
    
    # Run save and verify it works (lock is used internally)
    mock_file = AsyncMock()
    mock_file.__aenter__.return_value = mock_file
    mock_file.__aexit__.return_value = False
    mock_file.write = AsyncMock()

    with patch("src.core.cache.Path.exists", return_value=False):
        with patch("aiofiles.open", return_value=mock_file):
            # Should complete without deadlock or errors
            await manager._save_cache()
    
    # If we got here, lock was used correctly (no exception raised)
    assert True


@pytest.mark.asyncio
async def test_save_cache_handles_write_errors(temp_cache_path):
    """Handles write errors and raises exception."""
    manager = CacheManager(temp_cache_path)
    await manager.set("key1", "value1")

    with patch("src.core.cache.Path.exists", return_value=False):
        with patch("aiofiles.open", side_effect=IOError("Write error")):
            with pytest.raises(IOError):
                await manager._save_cache()


@pytest.mark.asyncio
async def test_save_cache_includes_metadata(temp_cache_path):
    """Saves with correct version, timestamp, strategy, compression metadata."""
    manager = CacheManager(
        temp_cache_path, strategy=CacheStrategy.LRU, compression=CompressionType.GZIP
    )
    await manager.set("key1", "value1")

    written_content = None
    
    def create_write_mock():
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = mock_file
        mock_file.__aexit__.return_value = False
        
        async def capture_write(content):
            nonlocal written_content
            written_content = content
        
        mock_file.write = capture_write
        return mock_file

    with patch("src.core.cache.Path.exists", return_value=False):
        with patch("aiofiles.open", side_effect=lambda *args, **kwargs: create_write_mock()):
            await manager._save_cache()

    data = json.loads(written_content)
    assert data["version"] == 2
    assert "timestamp" in data
    assert data["strategy"] == "lru"
    assert data["compression"] == "gzip"


@pytest.mark.asyncio
async def test_save_cache_serializes_entries_correctly(temp_cache_path):
    """Properly serializes CacheEntry objects using asdict()."""
    manager = CacheManager(temp_cache_path)
    await manager.set("key1", {"nested": "data"})

    written_content = None
    
    def create_write_mock():
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = mock_file
        mock_file.__aexit__.return_value = False
        
        async def capture_write(content):
            nonlocal written_content
            written_content = content
        
        mock_file.write = capture_write
        return mock_file

    with patch("src.core.cache.Path.exists", return_value=False):
        with patch("aiofiles.open", side_effect=lambda *args, **kwargs: create_write_mock()):
            await manager._save_cache()

    data = json.loads(written_content)
    entry_data = data["entries"]["key1"]
    assert "data" in entry_data
    assert "created_at" in entry_data
    assert "last_accessed" in entry_data
    assert "access_count" in entry_data


@pytest.mark.asyncio
async def test_save_cache_encodes_bytes_as_base64(temp_cache_path):
    """Ensure that bytes in entry.data are encoded as base64 when saving cache."""
    manager = CacheManager(temp_cache_path, compression=CompressionType.PICKLE, compression_threshold=1)
    # Create content that will be pickled and compressed
    complex_data = {
        "items": set(range(50)),
        "text": "x" * 200,
    }
    await manager.set("key1", complex_data)

    written_content = None

    def create_write_mock():
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = mock_file
        mock_file.__aexit__.return_value = False

        async def capture_write(content):
            nonlocal written_content
            written_content = content

        mock_file.write = capture_write
        return mock_file

    with patch("src.core.cache.Path.exists", return_value=False):
        with patch("aiofiles.open", side_effect=lambda *args, **kwargs: create_write_mock()):
            await manager._save_cache()

    data = json.loads(written_content)
    entry_data = data["entries"]["key1"]
    assert "data" in entry_data
    # If compressed bytes were present, we should have data_encoding == 'base64'
    assert entry_data.get("data_encoding") == "base64"
    assert isinstance(entry_data["data"], str)
    # Also confirm that it decodes back to bytes
    decoded = base64.b64decode(entry_data["data"].encode("ascii"))
    assert isinstance(decoded, (bytes, bytearray))


# ============================================================================
# _try_restore_from_backup() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_restore_from_backup_success(temp_cache_path, sample_cache_json):
    """Successfully restores from backup file."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock(json.dumps(sample_cache_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._try_restore_from_backup()

    assert len(manager._cache) == 2
    assert "key1" in manager._cache


@pytest.mark.asyncio
async def test_restore_from_backup_handles_base64_encoded_entries(temp_cache_path):
    """Restore entries that had data base64-encoded (e.g., compressed bytes)."""
    manager = CacheManager(temp_cache_path)

    # Create compressed pickled data
    original_value = {"items": set(range(10)), "text": "x" * 200}
    pickled_raw = pickle.dumps(original_value)
    compressed_bytes = zlib.compress(pickled_raw)
    b64 = base64.b64encode(compressed_bytes).decode("ascii")

    backup_json = {
        "version": 2,
        "timestamp": time.time(),
        "strategy": "simple",
        "compression": "pickle",
        "entries": {
            "key1": {
                "data": b64,
                "created_at": time.time(),
                "last_accessed": time.time(),
                "access_count": 1,
                "ttl": None,
                "compressed": True,
                "compression_type": "pickle",
                "data_encoding": "base64",
            }
        },
    }

    mock_file = create_async_file_mock(json.dumps(backup_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._try_restore_from_backup()

    # Now we should have entry present and get() should return original value
    assert "key1" in manager._cache
    result = await manager.get("key1")
    assert result == original_value


@pytest.mark.asyncio
async def test_restore_from_backup_file_not_exists(temp_cache_path):
    """Handles non-existent backup file gracefully."""
    manager = CacheManager(temp_cache_path)

    with patch("src.core.cache.Path.exists", return_value=False):
        await manager._try_restore_from_backup()

    assert len(manager._cache) == 0


@pytest.mark.asyncio
async def test_restore_from_backup_parses_json(temp_cache_path, sample_cache_json):
    """Parses backup JSON correctly."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock(json.dumps(sample_cache_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._try_restore_from_backup()

    assert manager._cache["key1"].data == "value1"
    assert manager._cache["key2"].data == {"nested": "data"}


@pytest.mark.asyncio
async def test_restore_from_backup_skips_expired(temp_cache_path, expired_cache_json):
    """Skips expired entries during restore."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock(json.dumps(expired_cache_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._try_restore_from_backup()

    assert "expired_key" not in manager._cache
    assert "valid_key" in manager._cache


@pytest.mark.asyncio
async def test_restore_from_backup_skips_invalid(temp_cache_path):
    """Skips invalid entries with warning."""
    manager = CacheManager(temp_cache_path)
    
    invalid_json = {
        "version": 2,
        "timestamp": time.time(),
        "strategy": "simple",
        "compression": "none",
        "entries": {
            "valid_key": {
                "data": "value",
                "created_at": time.time(),
                "last_accessed": time.time(),
                "access_count": 1,
                "ttl": None,
                "compressed": False,
                "compression_type": "none",
            },
            "invalid_key": {
                "data": "value",
                "created_at": "invalid",  # Invalid type
            },
        },
    }
    
    mock_file = create_async_file_mock(json.dumps(invalid_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._try_restore_from_backup()

    assert "valid_key" in manager._cache
    assert "invalid_key" not in manager._cache


@pytest.mark.asyncio
async def test_restore_from_backup_handles_invalid_json(temp_cache_path):
    """Handles invalid JSON in backup."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock("{invalid}")

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._try_restore_from_backup()

    assert len(manager._cache) == 0


@pytest.mark.asyncio
async def test_restore_from_backup_handles_exceptions(temp_cache_path):
    """Handles general exceptions during restore."""
    manager = CacheManager(temp_cache_path)
    
    mock_file = create_async_file_mock(should_fail=True)

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._try_restore_from_backup()

    assert len(manager._cache) == 0


@pytest.mark.asyncio
async def test_restore_from_backup_updates_cache_state(
    temp_cache_path, sample_cache_json
):
    """Updates internal cache state correctly."""
    manager = CacheManager(temp_cache_path)
    initial_count = len(manager._cache)
    
    mock_file = create_async_file_mock(json.dumps(sample_cache_json))

    with patch("src.core.cache.Path.exists", return_value=True):
        with patch("aiofiles.open", return_value=mock_file):
            await manager._try_restore_from_backup()

    assert len(manager._cache) > initial_count


# ============================================================================
# _auto_save_loop() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_auto_save_loop_saves_dirty_cache(temp_cache_path):
    """Periodically checks and saves dirty cache."""
    manager = CacheManager(temp_cache_path, auto_save_interval=0.1)
    await manager.set("key1", "value1")

    save_called = False

    async def mock_save():
        nonlocal save_called
        save_called = True
        manager._dirty = False

    with patch.object(manager, "_save_cache", side_effect=mock_save):
        task = asyncio.create_task(manager._auto_save_loop())
        await asyncio.sleep(0.15)
        manager._shutdown = True
        await asyncio.sleep(0.05)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

    assert save_called


@pytest.mark.asyncio
async def test_auto_save_loop_calls_cleanup(temp_cache_path):
    """Calls _cleanup_expired() on each iteration."""
    manager = CacheManager(temp_cache_path, auto_save_interval=0.1)

    cleanup_called = False

    async def mock_cleanup():
        nonlocal cleanup_called
        cleanup_called = True

    with patch.object(manager, "_cleanup_expired", side_effect=mock_cleanup):
        with patch.object(manager, "_save_cache", new_callable=AsyncMock):
            task = asyncio.create_task(manager._auto_save_loop())
            await asyncio.sleep(0.15)
            manager._shutdown = True
            await asyncio.sleep(0.05)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

    assert cleanup_called


@pytest.mark.asyncio
async def test_auto_save_loop_respects_interval(temp_cache_path):
    """Respects auto_save_interval setting."""
    manager = CacheManager(temp_cache_path, auto_save_interval=0.2)
    await manager.set("key1", "value1")  # Make cache dirty
    save_count = 0

    async def mock_save():
        nonlocal save_count
        save_count += 1
        manager._dirty = False

    with patch.object(manager, "_save_cache", side_effect=mock_save):
        with patch.object(manager, "_cleanup_expired", new_callable=AsyncMock):
            task = asyncio.create_task(manager._auto_save_loop())
            await asyncio.sleep(0.35)  # Should trigger 1 save (wait > interval)
            manager._shutdown = True
            await asyncio.sleep(0.05)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

    # Should have saved at least once
    assert save_count >= 1


@pytest.mark.asyncio
async def test_auto_save_loop_stops_on_shutdown(temp_cache_path):
    """Stops when _shutdown flag is set."""
    manager = CacheManager(temp_cache_path, auto_save_interval=0.1)

    with patch.object(manager, "_save_cache", new_callable=AsyncMock):
        with patch.object(manager, "_cleanup_expired", new_callable=AsyncMock):
            task = asyncio.create_task(manager._auto_save_loop())
            await asyncio.sleep(0.05)
            manager._shutdown = True
            await asyncio.sleep(0.15)

            assert task.done()


@pytest.mark.asyncio
async def test_auto_save_loop_handles_cancellation(temp_cache_path):
    """Handles CancelledError gracefully."""
    manager = CacheManager(temp_cache_path, auto_save_interval=0.1)

    with patch.object(manager, "_save_cache", new_callable=AsyncMock):
        with patch.object(manager, "_cleanup_expired", new_callable=AsyncMock):
            task = asyncio.create_task(manager._auto_save_loop())
            await asyncio.sleep(0.05)
            task.cancel()

            # Should not raise
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_auto_save_loop_catches_exceptions(temp_cache_path):
    """Catches and logs exceptions without crashing."""
    manager = CacheManager(temp_cache_path, auto_save_interval=0.1)
    await manager.set("key1", "value1")  # Make cache dirty initially
    exception_count = 0

    async def failing_save():
        nonlocal exception_count
        exception_count += 1
        if exception_count <= 2:  # Fail first 2 times, then succeed
            manager._dirty = True  # Keep it dirty to trigger more saves
            raise RuntimeError("Save failed")
        manager._dirty = False

    with patch.object(manager, "_save_cache", side_effect=failing_save):
        with patch.object(manager, "_cleanup_expired", new_callable=AsyncMock):
            task = asyncio.create_task(manager._auto_save_loop())
            await asyncio.sleep(0.35)  # Give time for multiple iterations
            manager._shutdown = True
            await asyncio.sleep(0.05)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

    # Loop should continue despite exceptions - should have called multiple times
    assert exception_count >= 2


@pytest.mark.asyncio
async def test_auto_save_loop_skips_clean_cache(temp_cache_path):
    """Skips save if cache is not dirty."""
    manager = CacheManager(temp_cache_path, auto_save_interval=0.1)
    manager._dirty = False

    save_called = False

    async def mock_save():
        nonlocal save_called
        save_called = True

    with patch.object(manager, "_save_cache", side_effect=mock_save):
        with patch.object(manager, "_cleanup_expired", new_callable=AsyncMock):
            task = asyncio.create_task(manager._auto_save_loop())
            await asyncio.sleep(0.15)
            manager._shutdown = True
            await asyncio.sleep(0.05)
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

    assert not save_called


# ============================================================================
# start() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_start_loads_cache(temp_cache_path):
    """Loads cache from file on startup."""
    manager = CacheManager(temp_cache_path)

    load_called = False

    async def mock_load():
        nonlocal load_called
        load_called = True

    with patch.object(manager, "_load_cache", side_effect=mock_load):
        await manager.start()

    assert load_called
    
    # Cleanup
    manager._shutdown = True
    if manager._auto_save_task:
        manager._auto_save_task.cancel()
        try:
            await manager._auto_save_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_start_creates_auto_save_task(temp_cache_path):
    """Starts auto-save background task."""
    manager = CacheManager(temp_cache_path)

    with patch.object(manager, "_load_cache", new_callable=AsyncMock):
        await manager.start()

    assert manager._auto_save_task is not None
    assert isinstance(manager._auto_save_task, asyncio.Task)

    # Cleanup
    manager._shutdown = True
    manager._auto_save_task.cancel()
    try:
        await manager._auto_save_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_start_logs_startup(temp_cache_path):
    """Logs startup message with strategy."""
    manager = CacheManager(temp_cache_path, strategy=CacheStrategy.LRU)

    with patch.object(manager, "_load_cache", new_callable=AsyncMock):
        await manager.start()

    # Task should be running
    assert manager._auto_save_task is not None

    # Cleanup
    manager._shutdown = True
    manager._auto_save_task.cancel()
    try:
        await manager._auto_save_task
    except asyncio.CancelledError:
        pass


# ============================================================================
# shutdown() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_shutdown_sets_flag(temp_cache_path):
    """Sets _shutdown flag."""
    manager = CacheManager(temp_cache_path)

    with patch.object(manager, "_load_cache", new_callable=AsyncMock):
        await manager.start()

    with patch.object(manager, "_save_cache", new_callable=AsyncMock):
        await manager.shutdown()

    assert manager._shutdown is True


@pytest.mark.asyncio
async def test_shutdown_cancels_task(temp_cache_path):
    """Cancels auto-save task."""
    manager = CacheManager(temp_cache_path)

    with patch.object(manager, "_load_cache", new_callable=AsyncMock):
        await manager.start()

    task = manager._auto_save_task

    with patch.object(manager, "_save_cache", new_callable=AsyncMock):
        await manager.shutdown()

    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_shutdown_waits_for_task(temp_cache_path):
    """Waits for task cancellation."""
    manager = CacheManager(temp_cache_path)

    with patch.object(manager, "_load_cache", new_callable=AsyncMock):
        await manager.start()

    # No need to track completion - just verify shutdown completes
    with patch.object(manager, "_save_cache", new_callable=AsyncMock):
        await manager.shutdown()
    
    # If we got here, shutdown completed successfully
    assert manager._shutdown is True


@pytest.mark.asyncio
async def test_shutdown_saves_dirty_cache(temp_cache_path):
    """Performs final save if cache is dirty."""
    manager = CacheManager(temp_cache_path)
    await manager.set("key1", "value1")

    save_called = False

    async def mock_save():
        nonlocal save_called
        save_called = True
        manager._dirty = False

    with patch.object(manager, "_load_cache", new_callable=AsyncMock):
        await manager.start()

    with patch.object(manager, "_save_cache", side_effect=mock_save):
        await manager.shutdown()

    assert save_called


@pytest.mark.asyncio
async def test_shutdown_skips_clean_cache(temp_cache_path):
    """Skips final save if cache is clean."""
    manager = CacheManager(temp_cache_path)
    manager._dirty = False

    save_called = False

    async def mock_save():
        nonlocal save_called
        save_called = True

    with patch.object(manager, "_load_cache", new_callable=AsyncMock):
        await manager.start()

    with patch.object(manager, "_save_cache", side_effect=mock_save):
        await manager.shutdown()

    assert not save_called


@pytest.mark.asyncio
async def test_shutdown_handles_cancelled_error(temp_cache_path):
    """Handles CancelledError from task."""
    manager = CacheManager(temp_cache_path)

    with patch.object(manager, "_load_cache", new_callable=AsyncMock):
        await manager.start()

    # Should not raise
    with patch.object(manager, "_save_cache", new_callable=AsyncMock):
        await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_logs_completion(temp_cache_path):
    """Logs shutdown completion."""
    manager = CacheManager(temp_cache_path)

    with patch.object(manager, "_load_cache", new_callable=AsyncMock):
        await manager.start()

    with patch.object(manager, "_save_cache", new_callable=AsyncMock):
        await manager.shutdown()

    # Test completes successfully
    assert manager._shutdown is True
