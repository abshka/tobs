"""
Tests for ConnectionManager download progress tracking.

Covers:
- init_download_progress initialization
- update_download_progress updates
- finish_download_progress completion
- Speed calculation and sliding window
- Stall detection
"""

import time
from unittest.mock import patch

import pytest

from src.core.connection import ConnectionConfig, ConnectionManager

# ============================================================================
# Batch 12: Download Progress Tracking Tests
# ============================================================================


class TestDownloadProgressInit:
    """Test download progress initialization."""

    @pytest.mark.asyncio
    async def test_init_download_progress(self, connection_manager):
        """Should initialize download progress tracking."""
        # Arrange
        download_id = "test_download_1"
        total_size = 1024 * 1024  # 1 MB

        # Act
        with patch("src.core.connection.time.time", return_value=1000.0):
            progress = connection_manager.init_download_progress(
                download_id, total_size
            )

        # Assert
        assert download_id in connection_manager.download_progress
        assert progress.total_bytes == total_size
        assert progress.downloaded_bytes == 0
        assert progress.start_time == 1000.0
        assert progress.last_progress_time == 1000.0

    @pytest.mark.asyncio
    async def test_init_download_progress_multiple(self, connection_manager):
        """Should track multiple downloads independently."""
        # Arrange
        downloads = [
            ("download_1", 1024 * 1024),
            ("download_2", 2 * 1024 * 1024),
            ("download_3", 5 * 1024 * 1024),
        ]

        # Act
        for download_id, size in downloads:
            connection_manager.init_download_progress(download_id, size)

        # Assert
        assert len(connection_manager.download_progress) == 3
        for download_id, size in downloads:
            progress = connection_manager.download_progress[download_id]
            assert progress.total_bytes == size
            assert progress.downloaded_bytes == 0


class TestDownloadProgressUpdate:
    """Test download progress updates."""

    @pytest.mark.asyncio
    async def test_update_download_progress(self, connection_manager):
        """Should update download progress correctly."""
        # Arrange
        download_id = "test_download"
        total_size = 1024 * 1024
        downloaded = 256 * 1024  # Absolute value

        with patch("src.core.connection.time.time") as mock_time:
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)

            # Act
            mock_time.return_value = 1001.0  # 1 second later
            speed = connection_manager.update_download_progress(download_id, downloaded)

            # Assert
            progress = connection_manager.download_progress[download_id]
            assert progress.downloaded_bytes == downloaded
            assert progress.last_progress_time == 1001.0
            assert speed is not None  # Should return current speed

    @pytest.mark.asyncio
    async def test_update_download_progress_incremental(self, connection_manager):
        """Should handle incremental progress updates."""
        # Arrange
        download_id = "test_download"
        total_size = 1024 * 1024

        with patch("src.core.connection.time.time") as mock_time:
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)

            # Act - Update with increasing values
            for i in range(1, 6):
                mock_time.return_value = 1000.0 + i
                downloaded = i * 100 * 1024
                connection_manager.update_download_progress(download_id, downloaded)

            # Assert
            progress = connection_manager.download_progress[download_id]
            assert progress.downloaded_bytes == 500 * 1024

    @pytest.mark.asyncio
    async def test_update_nonexistent_download(self, connection_manager):
        """Should handle updates to non-initialized downloads gracefully."""
        # Act & Assert - Should return None
        speed = connection_manager.update_download_progress("nonexistent", 1024)

        assert speed is None
        assert "nonexistent" not in connection_manager.download_progress

    @pytest.mark.asyncio
    async def test_update_with_no_progress(self, connection_manager):
        """Should handle update with same bytes (no progress)."""
        # Arrange
        download_id = "test_download"
        total_size = 1024 * 1024
        downloaded = 256 * 1024

        with patch("src.core.connection.time.time") as mock_time:
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)
            connection_manager.update_download_progress(download_id, downloaded)

            # Act - Update with same value (no progress)
            mock_time.return_value = 1001.0
            speed = connection_manager.update_download_progress(download_id, downloaded)

            # Assert - Should return avg speed (not current speed)
            stats = connection_manager.get_stats(download_id)
            assert speed == stats.avg_speed_kbps


class TestDownloadProgressSpeed:
    """Test speed calculation."""

    @pytest.mark.asyncio
    async def test_speed_calculation(self, connection_manager):
        """Should calculate download speed correctly."""
        # Arrange
        download_id = "test_download"
        total_size = 10 * 1024 * 1024  # 10 MB
        downloaded = 1024 * 1024  # 1 MB

        with patch("src.core.connection.time.time") as mock_time:
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)

            # Act - Download 1MB in 1 second (should be ~1024 KB/s)
            mock_time.return_value = 1001.0
            speed = connection_manager.update_download_progress(download_id, downloaded)

            # Assert
            assert speed is not None
            assert 1000 <= speed <= 1100  # ~1024 KB/s

    @pytest.mark.asyncio
    async def test_speed_recorded_in_stats(self, connection_manager):
        """Should record speed in operation stats."""
        # Arrange
        download_id = "test_download"
        total_size = 10 * 1024 * 1024

        with patch("src.core.connection.time.time") as mock_time:
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)

            # Act - Multiple updates
            for i in range(1, 4):
                mock_time.return_value = 1000.0 + i
                downloaded = i * 512 * 1024
                connection_manager.update_download_progress(download_id, downloaded)

            # Assert
            stats = connection_manager.get_stats(download_id)
            assert len(stats.speed_history) == 3
            assert stats.avg_speed_kbps > 0


class TestDownloadProgressStall:
    """Test stall detection."""

    @pytest.mark.asyncio
    async def test_stall_detection(self, connection_manager):
        """Should detect when download stalls."""
        # Arrange
        download_id = "test_download"
        total_size = 10 * 1024 * 1024
        downloaded = 1024 * 1024

        with (
            patch("src.core.connection.time.time") as mock_time,
            patch("src.core.connection.logger") as mock_logger,
        ):
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)

            # First update - normal
            mock_time.return_value = 1001.0
            connection_manager.update_download_progress(download_id, downloaded)

            # Second update - after 70 seconds with same progress (stalled)
            mock_time.return_value = 1072.0
            connection_manager.update_download_progress(download_id, downloaded)

            # Assert - Should log warning about stall
            stats = connection_manager.get_stats(download_id)
            assert stats.stall_count == 1
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_stall_with_regular_updates(self, connection_manager):
        """Should not detect stall with regular updates."""
        # Arrange
        download_id = "test_download"
        total_size = 10 * 1024 * 1024

        with patch("src.core.connection.time.time") as mock_time:
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)

            # Regular updates every 5 seconds with progress
            for i in range(1, 6):
                mock_time.return_value = 1000.0 + (i * 5)
                downloaded = i * 1024 * 1024
                connection_manager.update_download_progress(download_id, downloaded)

            # Assert
            stats = connection_manager.get_stats(download_id)
            assert stats.stall_count == 0

    @pytest.mark.asyncio
    async def test_stall_count_reset_on_progress(self, connection_manager):
        """Should reset stall count when progress resumes."""
        # Arrange
        download_id = "test_download"
        total_size = 10 * 1024 * 1024
        downloaded = 1024 * 1024

        with (
            patch("src.core.connection.time.time") as mock_time,
            patch("src.core.connection.logger") as mock_logger,
        ):
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)

            # First update with progress
            mock_time.return_value = 1001.0
            connection_manager.update_download_progress(download_id, downloaded)

            # Stall - update with SAME downloaded amount after 70 seconds
            mock_time.return_value = 1072.0  # 71 seconds later
            connection_manager.update_download_progress(
                download_id, downloaded
            )  # Same value = no progress

            stats = connection_manager.get_stats(download_id)
            assert stats.stall_count >= 1  # Should have detected stall
            mock_logger.warning.assert_called()  # Should have logged warning

            # Resume progress - advance downloaded bytes
            mock_time.return_value = 1073.0
            connection_manager.update_download_progress(download_id, 2 * downloaded)

            # Assert - stall_count should be reset to 0
            assert stats.stall_count == 0


class TestDownloadProgressFinish:
    """Test download progress completion."""

    @pytest.mark.asyncio
    async def test_finish_download_progress_success(self, connection_manager):
        """Should remove download tracking when finished successfully."""
        # Arrange
        download_id = "test_download"
        total_size = 1024 * 1024

        with (
            patch("src.core.connection.time.time", return_value=1000.0),
            patch("src.core.connection.logger"),
        ):
            connection_manager.init_download_progress(download_id, total_size)
            connection_manager.update_download_progress(download_id, total_size)

            # Act
            connection_manager.finish_download_progress(download_id, success=True)

            # Assert
            assert download_id not in connection_manager.download_progress

    @pytest.mark.asyncio
    async def test_finish_download_progress_failure(self, connection_manager):
        """Should handle failed download finish."""
        # Arrange
        download_id = "test_download"
        total_size = 1024 * 1024

        with (
            patch("src.core.connection.time.time", return_value=1000.0),
            patch("src.core.connection.logger"),
        ):
            connection_manager.init_download_progress(download_id, total_size)

            # Act
            connection_manager.finish_download_progress(download_id, success=False)

            # Assert
            assert download_id not in connection_manager.download_progress

    @pytest.mark.asyncio
    async def test_finish_nonexistent_download(self, connection_manager):
        """Should handle finishing non-existent download gracefully."""
        # Act & Assert - Should not raise
        with patch("src.core.connection.logger"):
            connection_manager.finish_download_progress("nonexistent", success=True)

    @pytest.mark.asyncio
    async def test_finish_logs_completion(self, connection_manager):
        """Should log download completion with stats."""
        # Arrange
        download_id = "test_download"
        total_size = 1024 * 1024

        with (
            patch("src.core.connection.time.time") as mock_time,
            patch("src.core.connection.logger") as mock_logger,
        ):
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)

            mock_time.return_value = 1010.0  # 10 seconds later
            connection_manager.update_download_progress(download_id, total_size)

            # Act
            connection_manager.finish_download_progress(download_id, success=True)

            # Assert - Should have logged completion
            assert mock_logger.info.called


class TestDownloadProgressIntegration:
    """Test full download progress workflow."""

    @pytest.mark.asyncio
    async def test_full_download_workflow(self, connection_manager):
        """Should handle complete download lifecycle."""
        # Arrange
        download_id = "full_workflow_test"
        total_size = 5 * 1024 * 1024  # 5 MB

        with (
            patch("src.core.connection.time.time") as mock_time,
            patch("src.core.connection.logger"),
        ):
            # Init
            mock_time.return_value = 1000.0
            connection_manager.init_download_progress(download_id, total_size)

            # Download in chunks
            for i in range(1, 6):
                mock_time.return_value = 1000.0 + i
                downloaded = i * 1024 * 1024
                speed = connection_manager.update_download_progress(
                    download_id, downloaded
                )
                assert speed is not None

            # Assert final state
            progress = connection_manager.download_progress[download_id]
            assert progress.downloaded_bytes == total_size

            # Finish
            connection_manager.finish_download_progress(download_id, success=True)

            # Assert
            assert download_id not in connection_manager.download_progress

    @pytest.mark.asyncio
    async def test_multiple_concurrent_downloads(self, connection_manager):
        """Should handle multiple concurrent downloads."""
        # Arrange
        downloads = {
            "download_1": 1024 * 1024,
            "download_2": 2 * 1024 * 1024,
            "download_3": 5 * 1024 * 1024,
        }

        with (
            patch("src.core.connection.time.time") as mock_time,
            patch("src.core.connection.logger"),
        ):
            mock_time.return_value = 1000.0

            # Init all downloads
            for download_id, size in downloads.items():
                connection_manager.init_download_progress(download_id, size)

            # Update all downloads
            for i in range(1, 4):
                mock_time.return_value = 1000.0 + i
                for download_id, size in downloads.items():
                    downloaded = (size // 10) * i
                    connection_manager.update_download_progress(download_id, downloaded)

            # Verify all tracking independently
            for download_id in downloads.keys():
                assert download_id in connection_manager.download_progress

            # Finish all
            for download_id in downloads.keys():
                connection_manager.finish_download_progress(download_id, success=True)

            # Assert
            assert len(connection_manager.download_progress) == 0
