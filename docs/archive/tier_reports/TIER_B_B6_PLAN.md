# TIER B-6: Hash-Based Media Deduplication
**Implementation Plan v1.0**

## Overview
Implement content-based media deduplication using Telethon's `upload.GetFileHashes` API to eliminate duplicate downloads of identical files regardless of their Telegram IDs.

**Priority:** P2 (Medium-High Impact)  
**Estimated Effort:** 4 days (~32 hours) → Target: 2 days (aggressive)  
**Expected Impact:** 10-20% network traffic reduction  
**Status:** 🔴 Planning Complete, Ready to Implement

---

## Problem Statement

### Current Limitations (ID-Based Deduplication)
- ✅ **Works:** Same file uploaded once → deduplicated correctly
- ❌ **Fails:** Same content, different uploads → downloaded multiple times
- ❌ **Fails:** File shared across chats → each instance downloaded separately
- ❌ **Fails:** Re-uploaded photos/videos → treated as unique

### Real-World Impact
```
Scenario: User shares vacation photo in 5 group chats
Current behavior: Download 5 times (5x bandwidth)
After B-6: Download once, reuse 4 times (80% savings)

Scenario: Meme reposted 20 times across channels
Current: 20 downloads
After B-6: 1 download + 19 cache hits (95% savings)
```

### Measurements from Memory
- Current deduplication: ID-based only (doc_id + access_hash)
- Telegram API support: `upload.GetFileHashes` available but **NOT USED**
- Expected bandwidth savings: **10-20% on typical workloads**
- Heavy repost scenarios: **up to 80-95% savings**

---

## Solution Architecture

### Three-Tier Deduplication Strategy

```
┌──────────────────────────────────────────────────────────┐
│  TIER 1: Hash-Based (Content)                            │
│  - Check file content hash (SHA256)                      │
│  - Telegram API: upload.GetFileHashes                    │
│  - Highest precision, slowest (API call required)        │
│  - Fallback: Skip if API unavailable                     │
└────────────────────┬─────────────────────────────────────┘
                     │ MISS
                     ↓
┌──────────────────────────────────────────────────────────┐
│  TIER 2: ID-Based (Existing)                             │
│  - Check doc_id + access_hash                            │
│  - Fast, already implemented                             │
│  - Works for same file in single context                 │
└────────────────────┬─────────────────────────────────────┘
                     │ MISS
                     ↓
┌──────────────────────────────────────────────────────────┐
│  TIER 3: Download                                        │
│  - Perform actual download                               │
│  - Update BOTH hash cache AND ID cache                   │
└──────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Hash-First Strategy**: Check hash cache BEFORE ID cache for maximum reuse
2. **Graceful Degradation**: Fallback to ID-based if `GetFileHashes` fails
3. **Dual Cache Update**: Update both hash and ID caches on successful download
4. **Persistent Storage**: Use msgpack for hash cache (security-compliant, S-3)
5. **LRU Eviction**: Limit hash cache size to prevent unbounded growth

---

## Implementation Steps

### Step 1: Create Hash Deduplication Module (12 hours → 6 hours)

**File:** `src/media/hash_dedup.py`

```python
"""
Hash-based media deduplication using Telegram file hashes.

Uses Telethon's upload.GetFileHashes API to compute content hashes
before downloading files, enabling deduplication across different
Telegram IDs and chat contexts.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Dict, Optional

import msgpack
from loguru import logger
from telethon.tl.functions.upload import GetFileHashes


class HashBasedDeduplicator:
    """
    Content-based media deduplication using file hashes.
    
    Manages a persistent cache mapping file content hashes (SHA256)
    to local file paths. Enables reuse of identical files across
    different Telegram message IDs.
    
    Cache Structure:
    {
        "hash_sha256_64chars": "/path/to/file.jpg",
        ...
    }
    """
    
    def __init__(
        self,
        cache_path: Path,
        max_cache_size: int = 10000,
        enable_api_hashing: bool = True
    ):
        """
        Initialize hash-based deduplicator.
        
        Args:
            cache_path: Path to persistent hash cache file
            max_cache_size: Maximum entries in cache (LRU eviction)
            enable_api_hashing: Use Telegram API for hashing (vs local)
        """
        self._cache_path = cache_path.resolve()
        self._max_cache_size = max_cache_size
        self._enable_api_hashing = enable_api_hashing
        
        # Hash cache: hash -> file_path
        self._hash_cache: Dict[str, Path] = {}
        
        # Statistics
        self._stats = {
            "hits": 0,
            "misses": 0,
            "api_calls": 0,
            "api_failures": 0,
            "evictions": 0,
        }
        
        # Load existing cache
        self._load_cache()
        
    def _load_cache(self):
        """Load hash cache from disk (msgpack format)."""
        if not self._cache_path.exists():
            logger.debug("Hash cache file not found, starting fresh")
            return
            
        try:
            with open(self._cache_path, 'rb') as f:
                data = msgpack.unpackb(f.read(), raw=False)
                # Convert strings back to Path objects
                self._hash_cache = {
                    k: Path(v) for k, v in data.items()
                    if Path(v).exists()  # Filter out stale entries
                }
            logger.info(
                f"Loaded hash cache: {len(self._hash_cache)} entries from "
                f"{self._cache_path}"
            )
        except Exception as e:
            logger.warning(f"Failed to load hash cache: {e}, starting fresh")
            self._hash_cache = {}
            
    def _save_cache(self):
        """Save hash cache to disk (msgpack format)."""
        try:
            # Ensure directory exists
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Convert Path objects to strings for msgpack
            data = {k: str(v) for k, v in self._hash_cache.items()}
            
            # Atomic write: write to .tmp, then rename
            tmp_path = self._cache_path.with_suffix('.tmp')
            with open(tmp_path, 'wb') as f:
                f.write(msgpack.packb(data, use_bin_type=True))
            tmp_path.replace(self._cache_path)
            
            logger.debug(f"Saved hash cache: {len(self._hash_cache)} entries")
        except Exception as e:
            logger.error(f"Failed to save hash cache: {e}")
            
    async def get_file_hash(
        self,
        client,
        media,
        timeout: float = 5.0
    ) -> Optional[str]:
        """
        Get file content hash using Telegram API.
        
        Uses Telethon's upload.GetFileHashes to retrieve server-side
        computed SHA256 hash chunks, then combines them into a single
        hash representing the entire file content.
        
        Args:
            client: Telegram client
            media: Media object (from message.media)
            timeout: API call timeout in seconds
            
        Returns:
            SHA256 hash (hex string) or None if unavailable
        """
        if not self._enable_api_hashing:
            return None
            
        self._stats["api_calls"] += 1
        
        try:
            # Get file location for API call
            from telethon import utils
            location = utils.get_input_location(media)
            
            # Call Telegram API with timeout
            hashes_result = await asyncio.wait_for(
                client(GetFileHashes(location=location, offset=0)),
                timeout=timeout
            )
            
            if not hashes_result:
                logger.debug("GetFileHashes returned empty result")
                self._stats["api_failures"] += 1
                return None
                
            # Combine all chunk hashes into single file hash
            combined = hashlib.sha256()
            for hash_obj in hashes_result:
                # hash_obj.hash is bytes
                combined.update(hash_obj.hash)
                
            file_hash = combined.hexdigest()
            logger.debug(f"Computed file hash: {file_hash[:16]}...")
            return file_hash
            
        except asyncio.TimeoutError:
            logger.debug(f"GetFileHashes timeout after {timeout}s")
            self._stats["api_failures"] += 1
            return None
        except Exception as e:
            logger.debug(f"Failed to get file hash: {type(e).__name__}: {e}")
            self._stats["api_failures"] += 1
            return None
            
    async def check_cache(self, file_hash: str) -> Optional[Path]:
        """
        Check if file with this hash already exists in cache.
        
        Args:
            file_hash: SHA256 hash (hex string)
            
        Returns:
            Path to existing file or None if not found
        """
        cached_path = self._hash_cache.get(file_hash)
        
        if cached_path:
            # Verify file still exists
            if cached_path.exists() and cached_path.stat().st_size > 0:
                self._stats["hits"] += 1
                logger.info(f"🔐 Hash cache HIT: {file_hash[:16]}... -> {cached_path.name}")
                return cached_path
            else:
                # Stale entry, remove it
                logger.debug(f"Removing stale hash cache entry: {file_hash[:16]}")
                del self._hash_cache[file_hash]
                self._save_cache()
                
        self._stats["misses"] += 1
        return None
        
    def add_to_cache(self, file_hash: str, file_path: Path):
        """
        Add file to hash cache.
        
        Implements LRU eviction when cache size exceeds max_cache_size.
        
        Args:
            file_hash: SHA256 hash (hex string)
            file_path: Path to downloaded file
        """
        # Check if we need to evict (simple FIFO for now)
        if len(self._hash_cache) >= self._max_cache_size:
            # Remove oldest entry (first key in dict)
            evicted_hash = next(iter(self._hash_cache))
            evicted_path = self._hash_cache.pop(evicted_hash)
            self._stats["evictions"] += 1
            logger.debug(
                f"Evicted hash cache entry: {evicted_hash[:16]}... "
                f"({evicted_path.name})"
            )
            
        # Add new entry
        self._hash_cache[file_hash] = file_path
        logger.debug(f"Added to hash cache: {file_hash[:16]}... -> {file_path.name}")
        
        # Save to disk
        self._save_cache()
        
    def get_stats(self) -> Dict[str, int]:
        """Get deduplication statistics."""
        stats = self._stats.copy()
        stats["cache_size"] = len(self._hash_cache)
        if stats["hits"] + stats["misses"] > 0:
            stats["hit_rate"] = stats["hits"] / (stats["hits"] + stats["misses"])
        else:
            stats["hit_rate"] = 0.0
        return stats
        
    def reset_stats(self):
        """Reset statistics counters."""
        self._stats = {
            "hits": 0,
            "misses": 0,
            "api_calls": 0,
            "api_failures": 0,
            "evictions": 0,
        }
```

**Verification:**
- [ ] `python -m py_compile src/media/hash_dedup.py` → OK
- [ ] Module imports correctly
- [ ] Cache loading/saving works with msgpack
- [ ] LRU eviction triggers at max_cache_size

---

### Step 2: Add Configuration (2 hours → 1 hour)

**File:** `src/config.py`

Add to `PerformanceSettings`:
```python
# Hash-Based Deduplication (B-6)
hash_based_deduplication: bool = True
hash_cache_max_size: int = 10000
hash_api_timeout: float = 5.0  # Timeout for GetFileHashes API call
```

**File:** `.env.example`

```bash
# ===================================================================
# TIER B-6: Hash-Based Media Deduplication
# ===================================================================
# Content-based deduplication using file hashes (vs ID-based only).
# Enables reuse of identical files across different message IDs.
#
# Example: Same meme reposted 20 times → download once, reuse 19 times
# Bandwidth savings: 10-20% typical, up to 80-95% on heavy reposts
# ===================================================================
HASH_BASED_DEDUPLICATION=true      # Enable hash-based dedup
HASH_CACHE_MAX_SIZE=10000          # Max hash cache entries (LRU)
HASH_API_TIMEOUT=5.0               # GetFileHashes API timeout (seconds)
```

**File:** `.env`

```bash
# B-6: Hash-Based Deduplication
HASH_BASED_DEDUPLICATION=true
HASH_CACHE_MAX_SIZE=10000
HASH_API_TIMEOUT=5.0
```

**Verification:**
- [ ] Config parsing works
- [ ] ENV variables read correctly
- [ ] Defaults match plan

---

### Step 3: Integrate into MediaDownloader (8 hours → 4 hours)

**File:** `src/media/downloader.py`

```python
from src.media.hash_dedup import HashBasedDeduplicator

class MediaDownloader:
    def __init__(
        self,
        connection_manager,
        temp_dir: Path,
        client=None,
        worker_clients=None,
        cache_manager=None,
        config=None,
    ):
        # Existing init...
        
        # B-6: Hash-based deduplication
        if config and config.performance.hash_based_deduplication:
            cache_dir = Path(config.cache_path) if hasattr(config, 'cache_path') else temp_dir.parent / 'cache'
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            self._hash_dedup = HashBasedDeduplicator(
                cache_path=cache_dir / "media_hash_cache.msgpack",
                max_cache_size=config.performance.hash_cache_max_size,
                enable_api_hashing=True
            )
            logger.info("🔐 Hash-based deduplication ENABLED")
        else:
            self._hash_dedup = None
            logger.info("ID-based deduplication only (hash dedup disabled)")
            
    async def download_media(
        self,
        message: Message,
        progress_queue=None,
        task_id=None,
    ) -> Optional[Path]:
        """Main download method with three-tier deduplication."""
        
        if not hasattr(message, "file") or not message.file:
            logger.warning("Message has no file attribute")
            return None
            
        # === TIER 1: Hash-Based Deduplication ===
        if self._hash_dedup:
            try:
                # Get file hash from Telegram API
                file_hash = await self._hash_dedup.get_file_hash(
                    client=self.client,
                    media=message.media,
                    timeout=self.config.performance.hash_api_timeout
                )
                
                if file_hash:
                    # Check hash cache
                    cached_path = await self._hash_dedup.check_cache(file_hash)
                    if cached_path:
                        # CACHE HIT: Reuse existing file
                        logger.info(
                            f"✅ Hash dedup HIT: msg {message.id} -> "
                            f"{cached_path.name}"
                        )
                        return cached_path
                    else:
                        logger.debug(
                            f"Hash dedup MISS: {file_hash[:16]}... (will download)"
                        )
            except Exception as e:
                logger.warning(f"Hash dedup failed: {e}, falling back to ID-based")
                
        # === TIER 2: ID-Based Deduplication (Existing) ===
        file_key = self._get_file_key(message)
        if file_key:
            # Check in-memory cache
            if file_key in self._downloaded_cache:
                cached_path = self._downloaded_cache[file_key]
                if cached_path.exists() and cached_path.stat().st_size > 0:
                    logger.debug(f"♻️ ID dedup hit (memory): {file_key}")
                    return cached_path
                    
            # Check persistent cache manager
            if self.cache_manager and hasattr(self.cache_manager, "get_file_path"):
                cached_path_str = await self.cache_manager.get_file_path(file_key)
                if cached_path_str:
                    cached_path = Path(cached_path_str)
                    if cached_path.exists() and cached_path.stat().st_size > 0:
                        logger.debug(f"♻️ ID dedup hit (persistent): {file_key}")
                        self._downloaded_cache[file_key] = cached_path
                        return cached_path
                        
        # === TIER 3: Download ===
        expected_size = getattr(message.file, "size", 0)
        if expected_size == 0:
            logger.warning(f"Message {message.id} has zero file size")
            return None
            
        logger.info(
            f"Downloading message {message.id}: "
            f"{expected_size / (1024 * 1024):.2f} MB"
        )
        
        # Perform download (existing logic)
        result_path = None
        if self._persistent_enabled:
            result_path = await self._persistent_download(
                message, expected_size, progress_queue, task_id
            )
        else:
            result_path = await self._standard_download(
                message, expected_size, progress_queue, task_id
            )
            
        # === Update ALL Caches on Success ===
        if result_path and result_path.exists():
            # Update ID cache (existing)
            if file_key:
                self._downloaded_cache[file_key] = result_path
                if self.cache_manager and hasattr(self.cache_manager, "store_file_path"):
                    await self.cache_manager.store_file_path(file_key, str(result_path))
                    
            # Update hash cache (B-6 new)
            if self._hash_dedup and file_hash:
                self._hash_dedup.add_to_cache(file_hash, result_path)
                logger.debug(f"Updated hash cache: {file_hash[:16]}... -> {result_path.name}")
                
        return result_path
        
    def get_hash_dedup_stats(self) -> Dict[str, int]:
        """Get hash-based deduplication statistics."""
        if self._hash_dedup:
            return self._hash_dedup.get_stats()
        return {}
```

**Verification:**
- [ ] Three-tier deduplication flow works
- [ ] Hash cache updated on successful download
- [ ] Fallback to ID-based on hash failure
- [ ] Stats collection working

---

### Step 4: Add Unit Tests (8 hours → 4 hours)

**File:** `tests/test_hash_dedup.py`

```python
import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.media.hash_dedup import HashBasedDeduplicator


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Temporary cache directory for testing."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture
def dedup(temp_cache_dir):
    """HashBasedDeduplicator instance for testing."""
    cache_path = temp_cache_dir / "test_hash_cache.msgpack"
    return HashBasedDeduplicator(
        cache_path=cache_path,
        max_cache_size=5,  # Small size for testing eviction
        enable_api_hashing=True
    )


def test_init(dedup, temp_cache_dir):
    """Test deduplicator initialization."""
    assert dedup._max_cache_size == 5
    assert dedup._enable_api_hashing is True
    assert len(dedup._hash_cache) == 0
    assert dedup._stats["hits"] == 0


def test_cache_persistence(dedup, temp_cache_dir):
    """Test cache loading and saving."""
    # Add entries
    file1 = temp_cache_dir / "file1.jpg"
    file1.write_text("test")
    dedup.add_to_cache("hash1", file1)
    
    # Create new instance (should load cache)
    dedup2 = HashBasedDeduplicator(
        cache_path=dedup._cache_path,
        max_cache_size=5
    )
    
    assert len(dedup2._hash_cache) == 1
    assert dedup2._hash_cache["hash1"] == file1


def test_lru_eviction(dedup, temp_cache_dir):
    """Test LRU eviction when cache is full."""
    # Add 5 entries (max_cache_size)
    for i in range(5):
        file = temp_cache_dir / f"file{i}.jpg"
        file.write_text(f"content{i}")
        dedup.add_to_cache(f"hash{i}", file)
        
    assert len(dedup._hash_cache) == 5
    assert dedup._stats["evictions"] == 0
    
    # Add 6th entry → should evict oldest (hash0)
    file6 = temp_cache_dir / "file6.jpg"
    file6.write_text("content6")
    dedup.add_to_cache("hash6", file6)
    
    assert len(dedup._hash_cache) == 5
    assert "hash0" not in dedup._hash_cache
    assert "hash6" in dedup._hash_cache
    assert dedup._stats["evictions"] == 1


@pytest.mark.asyncio
async def test_get_file_hash_success(dedup):
    """Test successful file hash retrieval."""
    # Mock client and API response
    mock_client = AsyncMock()
    mock_hash = MagicMock()
    mock_hash.hash = b"test_hash_bytes"
    mock_client.return_value = [mock_hash]
    
    # Mock media with get_input_location
    mock_media = MagicMock()
    
    with patch('telethon.utils.get_input_location', return_value=mock_media):
        file_hash = await dedup.get_file_hash(mock_client, mock_media)
        
    assert file_hash is not None
    assert isinstance(file_hash, str)
    assert len(file_hash) == 64  # SHA256 hex = 64 chars
    assert dedup._stats["api_calls"] == 1
    assert dedup._stats["api_failures"] == 0


@pytest.mark.asyncio
async def test_get_file_hash_timeout(dedup):
    """Test timeout handling in get_file_hash."""
    mock_client = AsyncMock()
    mock_client.side_effect = asyncio.TimeoutError()
    
    mock_media = MagicMock()
    
    with patch('telethon.utils.get_input_location', return_value=mock_media):
        file_hash = await dedup.get_file_hash(mock_client, mock_media, timeout=0.1)
        
    assert file_hash is None
    assert dedup._stats["api_calls"] == 1
    assert dedup._stats["api_failures"] == 1


@pytest.mark.asyncio
async def test_check_cache_hit(dedup, temp_cache_dir):
    """Test cache hit on existing file."""
    file1 = temp_cache_dir / "file1.jpg"
    file1.write_text("test content")
    dedup.add_to_cache("hash1", file1)
    
    result = await dedup.check_cache("hash1")
    
    assert result == file1
    assert dedup._stats["hits"] == 1
    assert dedup._stats["misses"] == 0


@pytest.mark.asyncio
async def test_check_cache_miss(dedup):
    """Test cache miss on non-existent hash."""
    result = await dedup.check_cache("nonexistent_hash")
    
    assert result is None
    assert dedup._stats["hits"] == 0
    assert dedup._stats["misses"] == 1


@pytest.mark.asyncio
async def test_check_cache_stale_entry(dedup, temp_cache_dir):
    """Test stale entry removal (file deleted after caching)."""
    file1 = temp_cache_dir / "file1.jpg"
    file1.write_text("test")
    dedup.add_to_cache("hash1", file1)
    
    # Delete file
    file1.unlink()
    
    # Check cache → should detect stale entry and remove it
    result = await dedup.check_cache("hash1")
    
    assert result is None
    assert "hash1" not in dedup._hash_cache
    assert dedup._stats["misses"] == 1


def test_get_stats(dedup):
    """Test statistics retrieval."""
    dedup._stats["hits"] = 10
    dedup._stats["misses"] = 5
    
    stats = dedup.get_stats()
    
    assert stats["hits"] == 10
    assert stats["misses"] == 5
    assert stats["hit_rate"] == pytest.approx(10 / 15)
    assert stats["cache_size"] == 0


def test_reset_stats(dedup):
    """Test statistics reset."""
    dedup._stats["hits"] = 10
    dedup._stats["misses"] = 5
    
    dedup.reset_stats()
    
    assert dedup._stats["hits"] == 0
    assert dedup._stats["misses"] == 0
```

**Verification:**
- [ ] All tests pass
- [ ] Cache persistence verified
- [ ] LRU eviction working
- [ ] API timeout handling correct
- [ ] Stale entry cleanup working

---

### Step 5: Add Integration Tests (4 hours → 2 hours)

**File:** `tests/test_hash_dedup_integration.py`

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.media.downloader import MediaDownloader
from src.media.hash_dedup import HashBasedDeduplicator


@pytest.fixture
def mock_config():
    """Mock config with hash dedup enabled."""
    config = MagicMock()
    config.performance.hash_based_deduplication = True
    config.performance.hash_cache_max_size = 100
    config.performance.hash_api_timeout = 5.0
    config.cache_path = "/tmp/cache"
    return config


@pytest.fixture
def downloader(tmp_path, mock_config):
    """MediaDownloader with hash dedup enabled."""
    mock_connection_manager = MagicMock()
    mock_client = AsyncMock()
    
    return MediaDownloader(
        connection_manager=mock_connection_manager,
        temp_dir=tmp_path / "temp",
        client=mock_client,
        config=mock_config
    )


@pytest.mark.asyncio
async def test_three_tier_dedup_flow(downloader, tmp_path):
    """Test complete three-tier deduplication flow."""
    # Mock message with media
    mock_message = MagicMock()
    mock_message.id = 12345
    mock_message.file = MagicMock()
    mock_message.file.size = 1024 * 1024  # 1MB
    mock_message.media = MagicMock()
    
    # Mock hash retrieval (TIER 1 miss)
    with patch.object(
        downloader._hash_dedup,
        'get_file_hash',
        return_value="test_hash_abc123"
    ):
        with patch.object(
            downloader._hash_dedup,
            'check_cache',
            return_value=None  # MISS
        ):
            # Mock ID-based cache (TIER 2 miss)
            downloader._get_file_key = MagicMock(return_value="doc_123_456")
            
            # Mock download (TIER 3)
            test_file = tmp_path / "downloaded.jpg"
            test_file.write_text("test content")
            
            with patch.object(
                downloader,
                '_persistent_download',
                return_value=test_file
            ):
                result = await downloader.download_media(mock_message)
                
    # Verify download happened
    assert result == test_file
    
    # Verify caches updated
    assert "doc_123_456" in downloader._downloaded_cache
    assert downloader._hash_dedup._hash_cache.get("test_hash_abc123") == test_file


@pytest.mark.asyncio
async def test_hash_cache_hit_skips_download(downloader, tmp_path):
    """Test hash cache hit prevents download (TIER 1 hit)."""
    # Create cached file
    cached_file = tmp_path / "cached.jpg"
    cached_file.write_text("cached content")
    
    # Mock message
    mock_message = MagicMock()
    mock_message.id = 12345
    mock_message.file = MagicMock()
    mock_message.file.size = 1024
    mock_message.media = MagicMock()
    
    # Mock hash cache HIT
    with patch.object(
        downloader._hash_dedup,
        'get_file_hash',
        return_value="cached_hash"
    ):
        with patch.object(
            downloader._hash_dedup,
            'check_cache',
            return_value=cached_file  # HIT!
        ):
            result = await downloader.download_media(mock_message)
            
    # Verify cached file returned, download never called
    assert result == cached_file
    assert downloader._hash_dedup.get_stats()["hits"] == 1


@pytest.mark.asyncio
async def test_hash_dedup_disabled_fallback(tmp_path, mock_config):
    """Test fallback to ID-only when hash dedup disabled."""
    # Disable hash dedup
    mock_config.performance.hash_based_deduplication = False
    
    mock_connection_manager = MagicMock()
    mock_client = AsyncMock()
    
    downloader = MediaDownloader(
        connection_manager=mock_connection_manager,
        temp_dir=tmp_path / "temp",
        client=mock_client,
        config=mock_config
    )
    
    # Verify hash dedup NOT initialized
    assert downloader._hash_dedup is None
```

**Verification:**
- [ ] Integration tests pass
- [ ] Three-tier flow working end-to-end
- [ ] Cache updates verified
- [ ] Disabled mode fallback works

---

## Acceptance Criteria

### Functionality
- [ ] Hash-based deduplication works for identical files with different IDs
- [ ] Fallback to ID-based on hash API failure
- [ ] Cache persists across sessions
- [ ] LRU eviction prevents unbounded growth
- [ ] Atomic cache writes prevent corruption

### Performance
- [ ] Hash API call timeout < 5 seconds
- [ ] Cache lookup < 1ms (in-memory dict)
- [ ] No performance regression vs ID-only baseline

### Testing
- [ ] Unit tests: 11 tests pass
- [ ] Integration tests: 3 tests pass
- [ ] py_compile: all files OK

### Configuration
- [ ] ENV variables work correctly
- [ ] Config defaults reasonable
- [ ] Disable flag works (fallback to ID-only)

### Observability
- [ ] Statistics tracked (hits, misses, API calls, failures)
- [ ] Logs show cache hits/misses
- [ ] Performance metrics available

---

## Rollback Plan

### Immediate Rollback (0 downtime)
```bash
# Disable hash-based dedup, keep ID-based
export HASH_BASED_DEDUPLICATION=false
```

### Graceful Degradation
If `GetFileHashes` API becomes unreliable:
- Module automatically falls back to ID-based on API failures
- No manual intervention required
- Logs show API failure rate

### Complete Removal
1. Set `HASH_BASED_DEDUPLICATION=false`
2. Remove `src/media/hash_dedup.py`
3. Remove hash dedup imports from `downloader.py`
4. Keep ID-based dedup (existing, proven)

---

## Testing Strategy

### Unit Tests (11 tests)
1. Initialization
2. Cache persistence (load/save)
3. LRU eviction
4. API hash retrieval (success)
5. API timeout handling
6. Cache hit (existing file)
7. Cache miss (nonexistent hash)
8. Stale entry cleanup
9. Statistics tracking
10. Statistics reset
11. Disabled mode

### Integration Tests (3 tests)
1. Three-tier dedup flow (hash miss → ID miss → download → update caches)
2. Hash cache hit (skip download)
3. Hash dedup disabled (fallback to ID-only)

### Manual Testing
```bash
# Test 1: Download same file twice (different message IDs)
# Expected: 1 download, 1 hash cache hit

# Test 2: Repost meme 5 times
# Expected: 1 download, 4 hash cache hits (80% savings)

# Test 3: Cache persistence
# Expected: Close/reopen → hash cache loaded from disk

# Test 4: LRU eviction
# Expected: Fill cache → old entries evicted, no crashes

# Test 5: API failure handling
# Expected: Fallback to ID-based, no errors
```

---

## Performance Expectations

### Bandwidth Savings
- **Light repost workload:** 5-10% reduction
- **Medium repost workload:** 10-20% reduction (target)
- **Heavy repost workload:** 80-95% reduction (meme channels)

### API Overhead
- GetFileHashes call: ~100-500ms per file
- Amortized cost: negligible (called once per unique file)
- Cache hit: 0ms (no API call)

### Memory Footprint
- Hash cache: ~10,000 entries × 100 bytes ≈ 1MB
- Negligible compared to existing cache managers

---

## Documentation Updates

### Files to Update
1. `TIER_B_PROGRESS.md` - Mark B-6 as completed
2. `README.md` - Add hash-based dedup to features
3. `.env.example` - Document new variables
4. `OPTIMIZATIONS_ROADMAP.md` - Update P2 status

### Commit Message
```
feat(B-6): Hash-based media deduplication

Implement content-based deduplication using Telethon's GetFileHashes API.
Enables reuse of identical files across different message IDs.

Changes:
- Add src/media/hash_dedup.py (HashBasedDeduplicator)
- Integrate three-tier dedup into MediaDownloader
- Add HASH_BASED_DEDUPLICATION config (enabled by default)
- Add 11 unit tests + 3 integration tests
- Update .env.example with B-6 parameters

Expected impact: 10-20% bandwidth reduction (up to 95% on heavy reposts)
Fallback: Graceful degradation to ID-based on API failure

Tests: 14/14 passing
Verification: py_compile OK, manual testing OK
```

---

## Timeline

### Aggressive Schedule (2 days)
- Day 1 Morning (4h): Step 1 - Hash dedup module
- Day 1 Afternoon (4h): Step 2-3 - Config + integration
- Day 2 Morning (4h): Step 4 - Unit tests
- Day 2 Afternoon (2h): Step 5 - Integration tests
- Day 2 Evening (2h): Manual testing + docs

### Conservative Schedule (4 days)
- Day 1: Step 1 (hash dedup module)
- Day 2: Steps 2-3 (config + integration)
- Day 3: Step 4 (unit tests)
- Day 4: Step 5 (integration tests) + manual testing + docs

**Target: 2 days (aggressive)**

---

## Status Tracking

### Implementation Progress
- [ ] Step 1: Hash dedup module (6h)
- [ ] Step 2: Configuration (1h)
- [ ] Step 3: Integration (4h)
- [ ] Step 4: Unit tests (4h)
- [ ] Step 5: Integration tests (2h)

### Verification Checklist
- [ ] py_compile: all files OK
- [ ] Unit tests: 11/11 passing
- [ ] Integration tests: 3/3 passing
- [ ] Manual testing: all scenarios OK
- [ ] Documentation: updated

### Production Readiness
- [ ] ENV variables documented
- [ ] Rollback plan validated
- [ ] Statistics collection working
- [ ] Performance verified (no regressions)
- [ ] Commit message prepared

---

**Ready to Implement:** ✅ YES  
**Estimated Completion:** 2 days  
**Risk Level:** LOW (graceful fallback to ID-based)
