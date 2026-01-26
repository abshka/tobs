"""
InputPeer caching layer for Telethon entity resolution.

This module provides an LRU cache with TTL for Telethon InputPeer objects,
reducing redundant API calls for entity resolution.

Part of TIER C optimization (C-3).
"""

import time
from collections import OrderedDict
from typing import Dict, Optional, Union

from telethon.tl.types import InputPeerUser, InputPeerChannel, InputPeerChat

# Use direct logging import to avoid circular dependencies
import logging
logger = logging.getLogger(__name__)

InputPeer = Union[InputPeerUser, InputPeerChannel, InputPeerChat]


class InputPeerCache:
    """
    LRU cache with TTL for Telethon InputPeer objects.
    
    Reduces redundant entity resolution API calls by caching InputPeer
    objects for frequently accessed entities.
    
    Features:
    - LRU eviction when cache size exceeds max_size
    - TTL-based expiration for stale entries
    - Thread-safe operations (single asyncio event loop assumed)
    - Metrics: hits, misses, evictions, expired entries
    
    Typical usage:
        cache = InputPeerCache(max_size=1000, ttl_seconds=3600)
        
        # Check cache before API call
        peer = cache.get(entity_id)
        if peer is None:
            peer = await client.get_input_entity(entity)
            cache.set(entity_id, peer)
    """
    
    def __init__(self, max_size: int = 1000, ttl_seconds: float = 3600.0):
        """
        Initialize the InputPeer cache.
        
        Args:
            max_size: Maximum number of entries before LRU eviction
            ttl_seconds: Time-to-live for cache entries in seconds
        """
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        
        # OrderedDict maintains insertion order for LRU
        self._cache: OrderedDict[int, tuple[InputPeer, float]] = OrderedDict()
        
        # Metrics
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0
        
        logger.info(
            f"InputPeerCache initialized: max_size={max_size}, "
            f"ttl={ttl_seconds}s"
        )
    
    def get(self, entity_id: int) -> Optional[InputPeer]:
        """
        Retrieve an InputPeer from cache.
        
        Args:
            entity_id: Telegram entity ID (user_id, channel_id, or chat_id)
        
        Returns:
            InputPeer object if found and not expired, None otherwise
        """
        if entity_id not in self._cache:
            self._misses += 1
            return None
        
        peer, timestamp = self._cache[entity_id]
        
        # Check TTL expiration
        if time.time() - timestamp > self._ttl_seconds:
            self._expirations += 1
            self._misses += 1
            del self._cache[entity_id]
            return None
        
        # LRU: move to end (most recently used)
        self._cache.move_to_end(entity_id)
        self._hits += 1
        
        return peer
    
    def set(self, entity_id: int, peer: InputPeer) -> None:
        """
        Store an InputPeer in cache.
        
        Args:
            entity_id: Telegram entity ID
            peer: InputPeer object to cache
        """
        current_time = time.time()
        
        # If entity already exists, update it (move to end)
        if entity_id in self._cache:
            del self._cache[entity_id]
        
        # Store with current timestamp
        self._cache[entity_id] = (peer, current_time)
        
        # LRU eviction if exceeding max size
        if len(self._cache) > self._max_size:
            # Remove oldest entry (first item)
            evicted_id, _ = self._cache.popitem(last=False)
            self._evictions += 1
            logger.debug(f"InputPeerCache evicted entity_id={evicted_id}")
    
    def clear(self) -> None:
        """Clear all cache entries."""
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"InputPeerCache cleared {count} entries")
    
    def evict_expired(self) -> int:
        """
        Manually evict all expired entries.
        
        Returns:
            Number of entries evicted
        """
        current_time = time.time()
        expired_ids = [
            entity_id
            for entity_id, (_, timestamp) in self._cache.items()
            if current_time - timestamp > self._ttl_seconds
        ]
        
        for entity_id in expired_ids:
            del self._cache[entity_id]
            self._expirations += 1
        
        if expired_ids:
            logger.debug(f"InputPeerCache evicted {len(expired_ids)} expired entries")
        
        return len(expired_ids)
    
    def get_metrics(self) -> Dict[str, int]:
        """
        Get cache performance metrics.
        
        Returns:
            Dictionary with metrics: size, hits, misses, hit_rate, evictions, expirations
        """
        total_requests = self._hits + self._misses
        hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0.0
        
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 2),
            "evictions": self._evictions,
            "expirations": self._expirations,
            "total_requests": total_requests,
        }
    
    def __len__(self) -> int:
        """Return current cache size."""
        return len(self._cache)
    
    def __repr__(self) -> str:
        """Return string representation with metrics."""
        metrics = self.get_metrics()
        return (
            f"InputPeerCache(size={metrics['size']}/{metrics['max_size']}, "
            f"hits={metrics['hits']}, misses={metrics['misses']}, "
            f"hit_rate={metrics['hit_rate']}%)"
        )
