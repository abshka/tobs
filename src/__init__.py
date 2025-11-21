"""
TOBS - Telegram Exporter to Markdown
Main package exports.
"""

from .config import Config, ExportTarget
from .core import (
    AlertLevel,
    BackoffStrategy,
    CacheManager,
    CacheStrategy,
    CompressionType,
    ConnectionManager,
    PerformanceMonitor,
    PoolType,
    ResourceState,
    get_cache_manager,
    get_connection_manager,
    get_performance_monitor,
    profile_async,
    profile_sync,
)
from .core_manager import (
    CoreSystemManager,
    get_core_manager,
    initialize_core_systems,
    is_core_initialized,
    shutdown_core_systems,
)
from .exceptions import ExporterError, TelegramConnectionError

# ForumManager is not part of core exports - import directly when needed
from .media import MediaProcessor
from .note_generator import NoteGenerator
from .telegram_client import TelegramManager
from .utils import logger, setup_logging

__version__ = "2.0.0"

__all__ = [
    # Core version
    "__version__",
    # Configuration
    "Config",
    "ExportTarget",
    # Core systems
    "CacheManager",
    "ConnectionManager",
    "PerformanceMonitor",
    "CoreSystemManager",
    # Core enums and types
    "AlertLevel",
    "BackoffStrategy",
    "CacheStrategy",
    "CompressionType",
    "PoolType",
    "ResourceState",
    # Core factory functions
    "get_cache_manager",
    "get_connection_manager",
    "get_performance_monitor",
    "get_core_manager",
    "initialize_core_systems",
    "shutdown_core_systems",
    "is_core_initialized",
    # Core decorators
    "profile_async",
    "profile_sync",
    # Main components
    "TelegramManager",
    "MediaProcessor",
    "NoteGenerator",
    # "ForumManager", - not part of core exports
    # Exceptions
    "ExporterError",
    "TelegramConnectionError",
    # Utilities
    "logger",
    "setup_logging",
]
