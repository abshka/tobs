"""
Shared fixtures for all tests.

This conftest.py provides common fixtures used across the test suite.
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Temporary directory for test files."""
    return tmp_path


@pytest.fixture
def mock_config() -> MagicMock:
    """Mock configuration object."""
    config = MagicMock()
    config.media_download = True
    config.max_workers = 2
    config.temp_dir = None
    config.enable_smart_caching = True
    config.vaapi_device = "/dev/dri/renderD128"

    # Performance settings
    config.performance = MagicMock()
    config.performance.enable_persistent_download = True
    config.performance.persistent_download_min_size_mb = 0.5
    config.performance.persistent_max_failures = 10
    config.performance.persistent_chunk_timeout = 600

    return config


@pytest.fixture
def mock_client() -> AsyncMock:
    """Mock Telegram client."""
    client = AsyncMock()
    client.download_media = AsyncMock()
    return client


@pytest.fixture
def mock_connection_manager() -> MagicMock:
    """Mock connection manager with semaphore."""
    manager = MagicMock()
    manager.download_semaphore = asyncio.Semaphore(3)
    return manager


@pytest.fixture
def mock_cache_manager() -> AsyncMock:
    """Mock cache manager."""
    manager = AsyncMock()
    manager.get = AsyncMock(return_value=None)
    manager.set = AsyncMock()
    return manager


@pytest.fixture
def sample_message() -> MagicMock:
    """Sample Telegram message with media."""
    message = MagicMock()
    message.id = 12345
    message.file = MagicMock()
    message.file.size = 1024 * 1024 * 5  # 5 MB
    message.file.mime_type = "video/mp4"
    message.file.name = "test_video.mp4"
    message.media = MagicMock()
    message.media.__class__.__name__ = "MessageMediaDocument"
    message.media.document = MagicMock()
    message.media.document.mime_type = "video/mp4"
    message.media.document.attributes = []
    return message


@pytest.fixture
def sample_photo_message() -> MagicMock:
    """Sample Telegram message with photo."""
    message = MagicMock()
    message.id = 12346
    message.file = MagicMock()
    message.file.size = 1024 * 1024 * 2  # 2 MB
    message.file.mime_type = "image/jpeg"
    message.file.name = "test_photo.jpg"
    message.media = MagicMock()
    message.media.__class__.__name__ = "MessageMediaPhoto"
    return message


@pytest.fixture
def sample_audio_message() -> MagicMock:
    """Sample Telegram message with audio."""
    message = MagicMock()
    message.id = 12347
    message.file = MagicMock()
    message.file.size = 1024 * 1024 * 3  # 3 MB
    message.file.mime_type = "audio/mpeg"
    message.file.name = "test_audio.mp3"
    message.media = MagicMock()
    message.media.__class__.__name__ = "MessageMediaDocument"
    message.media.document = MagicMock()
    message.media.document.mime_type = "audio/mpeg"
    message.media.document.attributes = []
    return message


# Mock Telegram error classes for testing
class FloodWaitError(Exception):
    """Mock FloodWaitError."""

    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"Flood wait: {seconds}s")


class SlowModeWaitError(Exception):
    """Mock SlowModeWaitError."""

    def __init__(self, seconds: int):
        self.seconds = seconds
        super().__init__(f"Slow mode wait: {seconds}s")


class TelegramTimeoutError(Exception):
    """Mock TelegramTimeoutError."""

    pass


class RPCError(Exception):
    """Mock RPCError."""

    pass


@pytest.fixture
async def connection_manager():
    """Create a real ConnectionManager instance for testing.

    Provides a fully functional ConnectionManager with monitoring disabled.
    Properly cleans up resources after test completion.
    """
    from src.core.connection import ConnectionManager

    # Create manager (monitoring will start automatically)
    manager = ConnectionManager()

    # Yield to test
    yield manager

    # Cleanup: cancel monitoring task and close pools
    if (
        hasattr(manager, "_monitor_task")
        and manager._monitor_task
        and not manager._monitor_task.done()
    ):
        manager._monitor_task.cancel()
        try:
            await manager._monitor_task
        except asyncio.CancelledError:
            pass

    # Note: AdaptiveTaskPool doesn't have a close() method
    # Pools will be cleaned up automatically when manager is destroyed
