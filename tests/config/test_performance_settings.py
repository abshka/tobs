"""
Tests for PerformanceSettings auto-configuration.

Tests cover:
- Auto-configuration for different profiles (conservative, balanced, aggressive, custom)
- Worker calculations based on CPU and memory
- System resource validation and warnings
- Default values and field assignments
"""

from unittest.mock import MagicMock, patch

from src.config import PerformanceSettings


class TestPerformanceSettingsDefaults:
    """Test default PerformanceSettings values."""

    def test_default_initialization(self):
        """Test PerformanceSettings initializes with defaults."""
        settings = PerformanceSettings()
        assert settings.workers == 8
        assert settings.download_workers == 12
        assert settings.io_workers == 16
        assert settings.ffmpeg_workers == 4
        assert settings.message_batch_size == 100
        assert settings.media_batch_size == 5

    def test_forum_settings_defaults(self):
        """Test forum-related defaults."""
        settings = PerformanceSettings()
        assert settings.forum_parallel_enabled is True
        assert settings.forum_max_workers == 8
        assert settings.forum_batch_size == 20
        assert settings.forum_media_parallel is True

    def test_timeout_settings_defaults(self):
        """Test timeout-related defaults."""
        settings = PerformanceSettings()
        assert settings.base_download_timeout == 300.0
        assert settings.large_file_timeout == 3600.0
        assert settings.huge_file_timeout == 7200.0
        assert settings.large_file_threshold_mb == 500
        assert settings.huge_file_threshold_mb == 1000

    def test_persistent_download_defaults(self):
        """Test persistent download defaults."""
        settings = PerformanceSettings()
        assert settings.enable_persistent_download is True
        assert settings.persistent_download_min_size_mb == 1
        assert settings.persistent_max_failures == 20
        assert settings.persistent_chunk_timeout == 600

    def test_parallel_download_defaults(self):
        """Test parallel download defaults."""
        settings = PerformanceSettings()
        assert settings.enable_parallel_download is False
        assert settings.parallel_download_min_size_mb == 5
        assert settings.max_parallel_connections == 8
        assert settings.max_concurrent_downloads == 3


@patch("src.config.psutil.virtual_memory")
@patch("src.config.os.cpu_count")
class TestPerformanceSettingsConservativeProfile:
    """Test auto_configure with conservative profile."""

    def test_conservative_profile_basic(self, mock_cpu_count, mock_memory):
        """Test conservative profile with normal resources."""
        mock_cpu_count.return_value = 8
        mock_memory.return_value = MagicMock(
            total=16 * 1024**3,  # 16 GB total
            available=12 * 1024**3,  # 12 GB available
        )

        settings = PerformanceSettings.auto_configure("conservative")

        assert settings.workers == 4  # min(4, cpu_count)
        assert settings.download_workers == 6  # min(6, cpu_count)
        assert settings.io_workers == 8  # min(8, cpu_count * 2)
        assert settings.ffmpeg_workers == 2  # min(2, cpu_count // 2)
        assert settings.message_batch_size == 50
        assert settings.media_batch_size == 3
        assert settings.memory_limit_mb == int(12 * 200)  # 20% of available
        assert settings.cache_size_limit_mb == 128
        assert settings.connection_pool_size == 50
        assert settings.forum_max_workers == 4
        assert settings.forum_batch_size == 10

    def test_conservative_profile_timeouts(self, mock_cpu_count, mock_memory):
        """Test conservative profile timeout settings."""
        mock_cpu_count.return_value = 8
        mock_memory.return_value = MagicMock(total=16 * 1024**3, available=12 * 1024**3)

        settings = PerformanceSettings.auto_configure("conservative")

        assert settings.request_timeout == 1200.0
        assert settings.large_file_timeout == 2400.0
        assert settings.huge_file_timeout == 4800.0
        assert settings.large_file_max_retries == 8
        assert settings.large_file_retry_delay == 15.0

    def test_conservative_profile_persistent_downloads(
        self, mock_cpu_count, mock_memory
    ):
        """Test conservative profile persistent download settings."""
        mock_cpu_count.return_value = 8
        mock_memory.return_value = MagicMock(total=16 * 1024**3, available=12 * 1024**3)

        settings = PerformanceSettings.auto_configure("conservative")

        assert settings.enable_persistent_download is True
        assert settings.persistent_download_min_size_mb == 1
        assert settings.persistent_max_failures == 15
        assert settings.persistent_chunk_timeout == 900
        # Parallel downloads disabled for conservative
        assert settings.enable_parallel_download is False
        assert settings.max_parallel_connections == 4
        assert settings.max_concurrent_downloads == 1


@patch("src.config.psutil.virtual_memory")
@patch("src.config.os.cpu_count")
class TestPerformanceSettingsBalancedProfile:
    """Test auto_configure with balanced profile."""

    def test_balanced_profile_basic(self, mock_cpu_count, mock_memory):
        """Test balanced profile with normal resources."""
        mock_cpu_count.return_value = 8
        mock_memory.return_value = MagicMock(total=16 * 1024**3, available=12 * 1024**3)

        settings = PerformanceSettings.auto_configure("balanced")

        assert settings.workers == 8  # min(8, cpu_count)
        assert settings.download_workers == 12  # min(12, int(cpu_count * 1.5))
        assert settings.io_workers == 16  # min(16, cpu_count * 2)
        assert settings.ffmpeg_workers == 4  # min(4, cpu_count // 2)
        assert settings.message_batch_size == 100
        assert settings.media_batch_size == 5
        assert settings.memory_limit_mb == int(12 * 400)  # 40% of available
        assert settings.cache_size_limit_mb == 256
        assert settings.connection_pool_size == 100

    def test_balanced_profile_forum_settings(self, mock_cpu_count, mock_memory):
        """Test balanced profile forum settings."""
        mock_cpu_count.return_value = 8
        mock_memory.return_value = MagicMock(total=16 * 1024**3, available=12 * 1024**3)

        settings = PerformanceSettings.auto_configure("balanced")

        assert settings.forum_parallel_enabled is True
        assert settings.forum_max_workers == 8
        assert settings.forum_batch_size == 20
        assert settings.forum_media_parallel is True


@patch("src.config.psutil.virtual_memory")
@patch("src.config.os.cpu_count")
class TestPerformanceSettingsAggressiveProfile:
    """Test auto_configure with aggressive profile."""

    def test_aggressive_profile_basic(self, mock_cpu_count, mock_memory):
        """Test aggressive profile with normal resources."""
        mock_cpu_count.return_value = 8
        mock_memory.return_value = MagicMock(total=16 * 1024**3, available=12 * 1024**3)

        settings = PerformanceSettings.auto_configure("aggressive")

        assert settings.workers == 16  # min(16, cpu_count * 2)
        assert settings.download_workers == 24  # min(24, cpu_count * 3)
        assert settings.io_workers == 32  # min(32, cpu_count * 4)
        assert settings.ffmpeg_workers == 8  # min(8, cpu_count)
        assert settings.message_batch_size == 200
        assert settings.media_batch_size == 10
        assert settings.memory_limit_mb == int(12 * 600)  # 60% of available
        assert settings.cache_size_limit_mb == 512

    def test_aggressive_profile_parallel_enabled(self, mock_cpu_count, mock_memory):
        """Test aggressive profile enables parallel downloads."""
        mock_cpu_count.return_value = 8
        mock_memory.return_value = MagicMock(total=16 * 1024**3, available=12 * 1024**3)

        settings = PerformanceSettings.auto_configure("aggressive")

        assert settings.enable_parallel_download is True
        assert settings.max_parallel_connections == 12
        assert settings.max_concurrent_downloads == 3


@patch("src.config.psutil.virtual_memory")
@patch("src.config.os.cpu_count")
class TestPerformanceSettingsCustomProfile:
    """Test auto_configure with custom profile returns defaults."""

    def test_custom_profile_returns_defaults(self, mock_cpu_count, mock_memory):
        """Test custom profile returns default PerformanceSettings."""
        mock_cpu_count.return_value = 8
        mock_memory.return_value = MagicMock(total=16 * 1024**3, available=12 * 1024**3)

        settings = PerformanceSettings.auto_configure("custom")

        # Should return defaults (same as __init__)
        assert settings.workers == 8
        assert settings.download_workers == 12
        assert settings.io_workers == 16
        assert settings.ffmpeg_workers == 4


@patch("src.config.logger")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.os.cpu_count")
class TestPerformanceSettingsLowResources:
    """Test auto_configure with low system resources."""

    def test_low_memory_logs_warning(self, mock_cpu_count, mock_memory, mock_logger):
        """Test low memory triggers warning."""
        mock_cpu_count.return_value = 4
        mock_memory.return_value = MagicMock(
            total=1.5 * 1024**3,  # 1.5 GB (below MIN_MEMORY_GB = 2)
            available=1 * 1024**3,
        )

        _ = PerformanceSettings.auto_configure("balanced")

        # Should log warning about low memory
        mock_logger.warning.assert_called()
        warning_call = str(mock_logger.warning.call_args)
        assert "1.5GB RAM" in warning_call or "minimum" in warning_call.lower()

    def test_low_available_memory_logs_warning(
        self, mock_cpu_count, mock_memory, mock_logger
    ):
        """Test low available memory triggers warning."""
        mock_cpu_count.return_value = 4
        mock_memory.return_value = MagicMock(
            total=8 * 1024**3,
            available=0.5 * 1024**3,  # Only 0.5 GB available
        )

        _ = PerformanceSettings.auto_configure("balanced")

        # Should log warning about low available memory
        mock_logger.warning.assert_called()
        warning_call = str(mock_logger.warning.call_args)
        assert "available memory" in warning_call.lower() or "0.5GB" in warning_call
