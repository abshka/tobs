"""
Модуль core - системы кэширования, соединений и мониторинга производительности.
"""

from .cache import (
    CacheManager,
    CacheStrategy,
    CompressionType,
    get_cache_manager,
    shutdown_cache_manager,
)
from .connection import (
    API_REQUEST_CONFIG,
    FILE_IO_CONFIG,
    LARGE_FILE_CONFIG,
    MEDIA_DOWNLOAD_CONFIG,
    BackoffStrategy,
    ConnectionConfig,
    ConnectionManager,
    PoolType,
    get_connection_manager,
    shutdown_connection_manager,
)
from .performance import (
    AdaptationStrategy,
    AlertLevel,
    PerformanceMonitor,
    ResourceState,
    get_performance_monitor,
    profile_async,
    profile_sync,
    shutdown_performance_monitor,
)

__all__ = [
    # Cache
    "CacheStrategy",
    "CompressionType",
    "CacheManager",
    "get_cache_manager",
    "shutdown_cache_manager",
    # Connection
    "BackoffStrategy",
    "PoolType",
    "ConnectionConfig",
    "ConnectionManager",
    "get_connection_manager",
    "shutdown_connection_manager",
    "MEDIA_DOWNLOAD_CONFIG",
    "LARGE_FILE_CONFIG",
    "API_REQUEST_CONFIG",
    "FILE_IO_CONFIG",
    # Performance
    "AlertLevel",
    "ResourceState",
    "AdaptationStrategy",
    "PerformanceMonitor",
    "get_performance_monitor",
    "shutdown_performance_monitor",
    "profile_async",
    "profile_sync",
]
