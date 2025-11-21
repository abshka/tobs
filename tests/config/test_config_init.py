"""
Tests for Config initialization and validation.

Tests cover:
- Required field validation
- Path setup and creation
- System requirements validation
- Performance profile assignment
- Error handling for invalid configurations
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import Config, ExportTarget
from src.exceptions import ConfigError


class TestConfigRequiredFieldValidation:
    """Test _validate_required_fields method."""

    def test_missing_api_id_raises_error(self):
        """Test missing API_ID raises ConfigError."""
        with pytest.raises(ConfigError, match="API_ID and API_HASH must be set"):
            Config(api_id=0, api_hash="a" * 32)

    def test_missing_api_hash_raises_error(self):
        """Test missing API_HASH raises ConfigError."""
        with pytest.raises(ConfigError, match="API_ID and API_HASH must be set"):
            Config(api_id=12345, api_hash="")

    def test_invalid_api_id_negative_raises_error(self):
        """Test negative API_ID raises ConfigError."""
        with pytest.raises(ConfigError, match="Invalid API_ID.*Must be positive integer"):
            Config(api_id=-1, api_hash="a" * 32)

    def test_short_api_hash_raises_error(self):
        """Test API_HASH shorter than 32 chars raises ConfigError."""
        with pytest.raises(ConfigError, match="Invalid API_HASH length.*Must be at least 32"):
            Config(api_id=12345, api_hash="tooshort")


@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigPathSetup:
    """Test _setup_paths method."""

    def test_paths_made_absolute(self, mock_mkdir, mock_memory, mock_disk):
        """Test that export_path and cache_file are made absolute."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        mock_disk.return_value = MagicMock(free=50 * 1024**3)

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            export_path=Path("./exports"),
            cache_file=Path("./cache.json")
        )

        assert config.export_path.is_absolute()
        assert config.cache_file.is_absolute()

    def test_directories_created(self, mock_mkdir, mock_memory, mock_disk):
        """Test that Path.mkdir is called for export and cache directories."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        mock_disk.return_value = MagicMock(free=50 * 1024**3)

        _ = Config(
            api_id=12345,
            api_hash="a" * 32,
        )

        # mkdir should be called at least twice (export_path and cache_file.parent)
        assert mock_mkdir.call_count >= 2
        # Verify parents=True and exist_ok=True are used
        for call in mock_mkdir.call_args_list:
            assert call[1].get("parents") is True
            assert call[1].get("exist_ok") is True

    def test_mkdir_oserror_raises_config_error(self, mock_mkdir, mock_memory, mock_disk):
        """Test OSError during mkdir raises ConfigError."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        mock_disk.return_value = MagicMock(free=50 * 1024**3)
        mock_mkdir.side_effect = OSError("Permission denied")

        with pytest.raises(ConfigError, match="Failed to create path"):
            Config(api_id=12345, api_hash="a" * 32)


@patch("src.config.logger")
@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigSystemRequirements:
    """Test _validate_system_requirements method."""

    def test_low_total_memory_logs_warning(self, mock_mkdir, mock_memory, mock_disk, mock_logger):
        """Test low total memory logs warning."""
        mock_memory.return_value = MagicMock(
            total=1.5 * 1024**3,  # 1.5 GB (below MIN_MEMORY_GB = 2)
            available=1 * 1024**3
        )
        mock_disk.return_value = MagicMock(free=50 * 1024**3)

        _ = Config(api_id=12345, api_hash="a" * 32)

        # Should log warning about low memory
        mock_logger.warning.assert_called()
        warning_text = str(mock_logger.warning.call_args)
        assert "1.5GB RAM" in warning_text or "minimum" in warning_text.lower()

    def test_insufficient_disk_space_logs_error(self, mock_mkdir, mock_memory, mock_disk, mock_logger):
        """Test insufficient disk space logs error (caught by try-except)."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        # Need to make disk_usage return object with .free attribute
        disk_mock = MagicMock()
        disk_mock.free = 0.5 * 1024**3  # 0.5 GB (below MIN_FREE_DISK_GB = 1)
        mock_disk.return_value = disk_mock

        # The ConfigError is raised but caught by the except block in _validate_system_requirements
        # So config initializes but logs a warning
        _ = Config(api_id=12345, api_hash="a" * 32)
        
        # Should have logged warning about the exception
        mock_logger.warning.assert_called()
        warning_text = str(mock_logger.warning.call_args_list)
        assert "Could not validate system requirements" in warning_text or "Insufficient disk space" in warning_text

    def test_system_validation_runs_without_errors(self, mock_mkdir, mock_memory, mock_disk, mock_logger):
        """Test system validation completes without raising exceptions."""
        mock_memory.return_value = MagicMock(
            total=8 * 1024**3,
            available=6 * 1024**3
        )
        disk_mock = MagicMock()
        disk_mock.free = 50 * 1024**3
        mock_disk.return_value = disk_mock

        # Should initialize successfully
        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            performance_profile="balanced"
        )
        
        # Verify config was created
        assert config.api_id == 12345
        assert config.performance.memory_limit_mb > 0


@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigPerformanceProfileAssignment:
    """Test performance profile assignment in __post_init__."""

    def test_performance_profile_auto_configured(self, mock_mkdir, mock_memory, mock_disk):
        """Test performance settings are auto-configured from profile."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        mock_disk.return_value = MagicMock(free=50 * 1024**3)

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            performance_profile="conservative"
        )

        # Performance should be configured for conservative profile
        assert config.performance.workers == 4  # Conservative profile value
        assert config.performance.message_batch_size == 50

    def test_balanced_profile_default(self, mock_mkdir, mock_memory, mock_disk):
        """Test balanced profile is default."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        mock_disk.return_value = MagicMock(free=50 * 1024**3)

        config = Config(api_id=12345, api_hash="a" * 32)

        # Default should be balanced
        assert config.performance_profile == "balanced"


@patch("src.config.logger")
@patch("src.config.psutil.disk_usage")
@patch("src.config.psutil.virtual_memory")
@patch("src.config.Path.mkdir")
class TestConfigSuccessfulInitialization:
    """Test successful Config initialization with valid inputs."""

    def test_minimal_config_initialization(self, mock_mkdir, mock_memory, mock_disk, mock_logger):
        """Test Config initializes with minimal required fields."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        mock_disk.return_value = MagicMock(free=50 * 1024**3)

        config = Config(api_id=12345, api_hash="a" * 32)

        assert config.api_id == 12345
        assert config.api_hash == "a" * 32
        assert config.session_name == "tobs_session"
        assert config.performance_profile == "balanced"
        mock_logger.info.assert_called()  # Should log configuration

    def test_config_with_export_targets(self, mock_mkdir, mock_memory, mock_disk, mock_logger):
        """Test Config with export targets initializes correctly."""
        mock_memory.return_value = MagicMock(total=8 * 1024**3, available=6 * 1024**3)
        mock_disk.return_value = MagicMock(free=50 * 1024**3)

        targets = [
            ExportTarget(id="@channel1", name="Channel 1"),
            ExportTarget(id="-100123456", name="Channel 2")
        ]

        config = Config(
            api_id=12345,
            api_hash="a" * 32,
            export_targets=targets
        )

        assert len(config.export_targets) == 2
        assert config.export_targets[0].id == "@channel1"
