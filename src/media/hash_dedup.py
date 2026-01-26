"""
Hash-based media deduplication using local SHA256 hashing.

Note: Telethon does NOT provide upload.GetFileHashes API as of v1.33+.
This implementation uses local hash computation during download instead.
Enables deduplication across different Telegram IDs and chat contexts.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Dict, Optional

import msgpack
from loguru import logger


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
            
    async def get_file_hash(self, client, media, timeout: float = 5.0) -> Optional[str]:
        """
        Attempt to get file hash from Telegram API.
        
        Note: Telethon v1.33+ does NOT provide upload.GetFileHashes API.
        This method returns None, causing the downloader to fall back to
        ID-based deduplication. Hash-based dedup only works post-download
        via compute_file_hash().
        
        Args:
            client: Telegram client (unused)
            media: Media object (unused)
            timeout: API call timeout (unused)
            
        Returns:
            None (API not available)
        """
        # Telethon doesn't provide GetFileHashes API
        # Return None to skip pre-download hash check
        return None
    
    async def compute_file_hash(self, file_path: Path) -> Optional[str]:
        """
        Compute SHA256 hash of local file.
        
        Since Telethon doesn't provide GetFileHashes API, we compute
        hash locally after download. This still provides deduplication
        benefits for identical files across different Telegram IDs.
        
        Args:
            file_path: Path to downloaded file
            
        Returns:
            SHA256 hash (hex string) or None on error
        """
        if not self._enable_api_hashing:
            return None
            
        try:
            sha256_hash = hashlib.sha256()
            
            # Read file in chunks to handle large files efficiently
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256_hash.update(chunk)
                    
            file_hash = sha256_hash.hexdigest()
            logger.debug(f"Computed file hash: {file_hash[:16]}... for {file_path.name}")
            return file_hash
            
        except Exception as e:
            logger.debug(f"Failed to compute file hash: {type(e).__name__}: {e}")
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
