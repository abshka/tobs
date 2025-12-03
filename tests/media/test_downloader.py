"""
Unit tests for MediaDownloader.

Tests media download strategies including persistent and standard downloads.
This is a CRITICAL component - high test coverage required.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest

from src.media.downloader import MediaDownloader

pytestmark = pytest.mark.unit


class TestMediaDownloader:
    """Tests for MediaDownloader class."""

    @pytest.fixture
    def media_downloader(self, mock_connection_manager, tmp_path):
        """Create MediaDownloader instance for tests."""
        return MediaDownloader(
            connection_manager=mock_connection_manager,
            temp_dir=tmp_path,
        )

    async def test_initialization(
        self, media_downloader, tmp_path, mock_connection_manager
    ):
        """Test MediaDownloader initialization."""
        assert media_downloader.connection_manager == mock_connection_manager
        assert media_downloader.temp_dir == tmp_path
        assert media_downloader._persistent_download_attempts == 0
        assert media_downloader._persistent_download_successes == 0
        assert media_downloader._standard_download_attempts == 0
        assert media_downloader._standard_download_successes == 0
        assert media_downloader._persistent_enabled is True  # Default from env
        assert media_downloader._persistent_min_size_mb == 0.5  # Default

    async def test_download_media_persistent_mode(
        self, media_downloader, sample_message, tmp_path
    ):
        """Test download_media routes to persistent mode."""
        media_downloader._persistent_enabled = True
        expected_path = tmp_path / "result.tmp"

        # Mock the internal method
        with patch.object(
            media_downloader,
            "_persistent_download",
            new_callable=AsyncMock,
            return_value=expected_path,
        ) as mock_persistent:
            result = await media_downloader.download_media(sample_message)

            mock_persistent.assert_called_once()
            assert result == expected_path

    async def test_download_media_standard_mode(
        self, media_downloader, sample_message, tmp_path
    ):
        """Test download_media routes to standard mode."""
        media_downloader._persistent_enabled = False
        expected_path = tmp_path / "result.tmp"

        with patch.object(
            media_downloader,
            "_standard_download",
            new_callable=AsyncMock,
            return_value=expected_path,
        ) as mock_standard:
            result = await media_downloader.download_media(sample_message)

            mock_standard.assert_called_once()
            assert result == expected_path

    async def test_download_media_no_file(self, media_downloader):
        """Test download_media when message has no file."""
        message_no_file = MagicMock()
        message_no_file.id = 99999
        message_no_file.file = None

        result = await media_downloader.download_media(message_no_file)
        assert result is None

    async def test_download_media_zero_size(self, media_downloader, sample_message):
        """Test download_media when file has zero size."""
        sample_message.file.size = 0
        result = await media_downloader.download_media(sample_message)
        assert result is None

    async def test_persistent_download_complete_file_exists(
        self, media_downloader, sample_message, tmp_path
    ):
        """Test persistent download when complete file already exists."""
        expected_size = 5 * 1024 * 1024  # 5 MB
        temp_path = tmp_path / f"persistent_{sample_message.id}.tmp"

        # Pre-create complete file
        temp_path.write_bytes(b"x" * expected_size)

        result = await media_downloader._persistent_download(
            sample_message, expected_size
        )

        assert result == temp_path
        assert media_downloader._persistent_download_successes == 1

    async def test_standard_download_success(
        self, media_downloader, sample_message, tmp_path
    ):
        """Test successful standard download."""
        expected_size = 2 * 1024 * 1024  # 2 MB

        # Mock download_media on the message
        async def mock_download(file, progress_callback=None):
            file.write_bytes(b"x" * expected_size)
            return file

        sample_message.download_media = mock_download

        # Mock semaphore
        mock_sem = AsyncMock()
        mock_sem.__aenter__ = AsyncMock()
        mock_sem.__aexit__ = AsyncMock()
        media_downloader.connection_manager.download_semaphore = mock_sem

        result = await media_downloader._standard_download(
            sample_message, expected_size
        )

        # Should return a path (actual filename is generated with timestamp)
        assert result is not None
        assert result.exists()
        assert result.stat().st_size == expected_size
        assert media_downloader._standard_download_successes == 1

    async def test_get_statistics(self, media_downloader):
        """Test statistics retrieval."""
        media_downloader._persistent_download_attempts = 10
        media_downloader._persistent_download_successes = 8
        media_downloader._standard_download_attempts = 5
        media_downloader._standard_download_successes = 5

        stats = media_downloader.get_statistics()

        # Check structure (actual keys are nested)
        assert "persistent_downloads" in stats
        assert stats["persistent_downloads"]["attempts"] == 10
        assert stats["persistent_downloads"]["successes"] == 8
        assert "standard_downloads" in stats
        assert stats["standard_downloads"]["attempts"] == 5
        assert stats["standard_downloads"]["successes"] == 5
