"""
Unit tests for MediaCache.

Tests media file caching functionality.
"""

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.media.cache import MediaCache

pytestmark = pytest.mark.unit


class TestMediaCache:
    """Tests for MediaCache class."""

    @pytest.fixture
    def mock_cache_manager(self):
        """Create mock cache manager."""
        manager = AsyncMock()
        manager.get = AsyncMock(return_value=None)
        manager.set = AsyncMock()
        return manager

    @pytest.fixture
    def media_cache(self, mock_cache_manager):
        """Create MediaCache instance for tests."""
        return MediaCache(cache_manager=mock_cache_manager)

    @pytest.fixture
    def media_cache_no_manager(self):
        """Create MediaCache without cache manager."""
        return MediaCache(cache_manager=None)

    @pytest.fixture
    def sample_message(self):
        """Create sample message."""
        message = MagicMock()
        message.id = 12345
        return message

    async def test_initialization_with_cache_manager(
        self, media_cache, mock_cache_manager
    ):
        """Test MediaCache initialization with cache manager."""
        assert media_cache.cache_manager == mock_cache_manager

    async def test_initialization_without_cache_manager(self, media_cache_no_manager):
        """Test MediaCache initialization without cache manager."""
        assert media_cache_no_manager.cache_manager is None

    async def test_check_cache_hit(
        self, media_cache, mock_cache_manager, sample_message, tmp_path
    ):
        """Test cache hit returns cached file."""
        # Setup cached file
        cached_file = tmp_path / "cached_media.mp4"
        cached_file.write_bytes(b"cached content")

        output_path = tmp_path / "output.mp4"

        # Mock cache manager to return cached info
        mock_cache_manager.get.return_value = {
            "path": str(cached_file),
            "size": cached_file.stat().st_size,
            "timestamp": time.time(),
        }

        result = await media_cache.check_cache(sample_message, output_path)

        # Should return output_path with copied content
        assert result == output_path
        assert output_path.exists()
        assert output_path.read_bytes() == b"cached content"

        # Verify cache was checked
        mock_cache_manager.get.assert_called_once_with("media_12345")

    async def test_check_cache_miss(
        self, media_cache, mock_cache_manager, sample_message, tmp_path
    ):
        """Test cache miss returns None."""
        output_path = tmp_path / "output.mp4"

        # Mock cache manager to return None (cache miss)
        mock_cache_manager.get.return_value = None

        result = await media_cache.check_cache(sample_message, output_path)

        assert result is None
        mock_cache_manager.get.assert_called_once_with("media_12345")

    async def test_check_cache_file_not_exists(
        self, media_cache, mock_cache_manager, sample_message, tmp_path
    ):
        """Test cache returns None if cached file doesn't exist."""
        output_path = tmp_path / "output.mp4"

        # Mock cache manager to return path to non-existent file
        mock_cache_manager.get.return_value = {
            "path": str(tmp_path / "nonexistent.mp4"),
            "size": 1000,
            "timestamp": time.time(),
        }

        result = await media_cache.check_cache(sample_message, output_path)

        assert result is None

    async def test_check_cache_empty_file(
        self, media_cache, mock_cache_manager, sample_message, tmp_path
    ):
        """Test cache returns None if cached file is empty."""
        # Create empty cached file
        cached_file = tmp_path / "empty.mp4"
        cached_file.write_bytes(b"")

        output_path = tmp_path / "output.mp4"

        mock_cache_manager.get.return_value = {
            "path": str(cached_file),
            "size": 0,
            "timestamp": time.time(),
        }

        result = await media_cache.check_cache(sample_message, output_path)

        assert result is None

    async def test_check_cache_without_cache_manager(
        self, media_cache_no_manager, sample_message, tmp_path
    ):
        """Test check_cache returns None when no cache manager."""
        output_path = tmp_path / "output.mp4"

        result = await media_cache_no_manager.check_cache(sample_message, output_path)

        assert result is None

    async def test_check_cache_same_path(
        self, media_cache, mock_cache_manager, sample_message, tmp_path
    ):
        """Test cache hit with same input/output path (no copy needed)."""
        cached_file = tmp_path / "media.mp4"
        cached_file.write_bytes(b"content")

        # Same path for cached and output
        output_path = cached_file

        mock_cache_manager.get.return_value = {
            "path": str(cached_file),
            "size": cached_file.stat().st_size,
            "timestamp": time.time(),
        }

        result = await media_cache.check_cache(sample_message, output_path)

        # Should return same path without copying
        assert result == output_path

    async def test_save_to_cache(
        self, media_cache, mock_cache_manager, sample_message, tmp_path
    ):
        """Test save_to_cache stores file info."""
        result_path = tmp_path / "result.mp4"
        result_path.write_bytes(b"processed content")

        await media_cache.save_to_cache(sample_message, result_path)

        # Verify cache manager was called with correct data
        mock_cache_manager.set.assert_called_once()
        call_args = mock_cache_manager.set.call_args

        assert call_args[0][0] == "media_12345"  # cache key
        cache_data = call_args[0][1]
        assert cache_data["path"] == str(result_path)
        assert cache_data["size"] == result_path.stat().st_size
        assert "timestamp" in cache_data

    async def test_save_to_cache_without_cache_manager(
        self, media_cache_no_manager, sample_message, tmp_path
    ):
        """Test save_to_cache does nothing when no cache manager."""
        result_path = tmp_path / "result.mp4"
        result_path.write_bytes(b"content")

        # Should not raise exception
        await media_cache_no_manager.save_to_cache(sample_message, result_path)

    async def test_save_to_cache_exception_handling(
        self, media_cache, mock_cache_manager, sample_message, tmp_path
    ):
        """Test save_to_cache handles exceptions gracefully."""
        result_path = tmp_path / "result.mp4"
        result_path.write_bytes(b"content")

        # Mock cache manager to raise exception
        mock_cache_manager.set.side_effect = Exception("Cache error")

        # Should not raise exception
        await media_cache.save_to_cache(sample_message, result_path)

    async def test_copy_file_async(self, media_cache, tmp_path):
        """Test _copy_file_async copies file correctly."""
        src_file = tmp_path / "source.mp4"
        dst_file = tmp_path / "subdir" / "dest.mp4"

        src_file.write_bytes(b"test content for copy")

        await media_cache._copy_file_async(src_file, dst_file)

        assert dst_file.exists()
        assert dst_file.read_bytes() == b"test content for copy"
        assert dst_file.parent.exists()  # Subdirectory created

    async def test_copy_file_async_large_file(self, media_cache, tmp_path):
        """Test _copy_file_async handles large files (chunks)."""
        src_file = tmp_path / "large.mp4"
        dst_file = tmp_path / "large_copy.mp4"

        # Create file larger than 1MB (chunk size)
        large_content = b"x" * (2 * 1024 * 1024)  # 2 MB
        src_file.write_bytes(large_content)

        await media_cache._copy_file_async(src_file, dst_file)

        assert dst_file.exists()
        assert dst_file.stat().st_size == len(large_content)

    async def test_check_cache_exception_handling(
        self, media_cache, mock_cache_manager, sample_message, tmp_path
    ):
        """Test check_cache handles exceptions gracefully."""
        output_path = tmp_path / "output.mp4"

        # Mock cache manager to raise exception
        mock_cache_manager.get.side_effect = Exception("Cache error")

        result = await media_cache.check_cache(sample_message, output_path)

        # Should return None on exception
        assert result is None
