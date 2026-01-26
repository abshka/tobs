import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config, ExportTarget
from src.telegram_client import TelegramManager
from src.media import MediaProcessor
from src.core_manager import CoreSystemManager


@pytest.fixture
def mock_config(tmp_path):
    config = MagicMock(spec=Config)
    config.export_path = tmp_path / "export"
    config.export_path.mkdir()
    config.media_download = True
    config.enable_transcription = False
    config.export_reactions = True
    config.use_structured_export = True
    config.performance_profile = "balanced"
    config.use_takeout = False
    config.shard_count = 1
    config.enable_shard_fetch = False
    config.get_export_path_for_entity = MagicMock(
        return_value=tmp_path / "export" / "test_chat"
    )
    config.get_media_path_for_entity = MagicMock(
        return_value=tmp_path / "export" / "test_chat" / "media"
    )
    return config


@pytest.fixture
def mock_telegram_client():
    client = MagicMock()
    client.get_messages = AsyncMock(return_value=[])
    client.get_entity = AsyncMock()
    return client


@pytest.fixture
def mock_telegram_manager(mock_telegram_client):
    manager = MagicMock(spec=TelegramManager)
    manager.client = mock_telegram_client
    manager.client_connected = True
    manager.connect = AsyncMock()
    manager.resolve_entity = AsyncMock()

    # Mock fetch_messages as an async generator
    async def _fetch_messages(*args, **kwargs):
        for msg in kwargs.get("messages", []):
            yield msg

    manager.fetch_messages = _fetch_messages
    manager.get_topic_messages_stream = _fetch_messages
    manager.get_forum_topics = AsyncMock(return_value=[])
    manager.get_total_message_count = AsyncMock(return_value=10)

    return manager


@pytest.fixture
def mock_cache_manager():
    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    cache.load_cache = AsyncMock()
    return cache


@pytest.fixture
def mock_media_processor():
    processor = MagicMock(spec=MediaProcessor)
    processor.download_and_process_media = AsyncMock(return_value=[])
    processor.transcribe_audio = AsyncMock(return_value=None)
    processor.wait_for_downloads = AsyncMock()
    processor.process_pending_tasks = AsyncMock()
    return processor


@pytest.fixture
def mock_note_generator():
    return MagicMock()


@pytest.fixture
def mock_http_session():
    return MagicMock()


@pytest.fixture
def mock_performance_monitor():
    monitor = MagicMock()
    monitor.last_sample_time = 0  # Mock initial value
    monitor.sample_resources = AsyncMock()  # Mock the method
    return monitor


@pytest.fixture
def mock_core_system_manager(mock_config, mock_cache_manager, mock_performance_monitor):
    manager = MagicMock(spec=CoreSystemManager)
    manager.config = mock_config
    manager.cache_manager = mock_cache_manager
    manager.performance_monitor = mock_performance_monitor
    return manager


@pytest.fixture
def exporter(
    mock_config,
    mock_telegram_manager,
    mock_cache_manager,
    mock_media_processor,
    mock_note_generator,
    mock_http_session,
    mock_performance_monitor,
):
    from src.export.exporter import Exporter  # Local import to avoid circular dependency

    return Exporter(
        mock_config,
        mock_telegram_manager,
        mock_cache_manager,
        mock_media_processor,
        mock_note_generator,
        mock_http_session,
        performance_monitor=mock_performance_monitor,  # Pass the mocked performance_monitor
    )