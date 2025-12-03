"""
Batch 6: Tests for Config.from_env() - environment variable parsing.

This module tests:
- Loading configuration from environment variables
- Type conversions (int, bool, float)
- Default values when env vars are not set
- EXPORT_TARGETS parsing (comma-separated list)
- Proxy configuration parsing
- Boolean parsing via _parse_bool
"""

from unittest.mock import patch

from src.config import Config, ExportTarget, _parse_bool

# Valid API hash for testing
VALID_API_HASH = "a" * 32


class TestParseBool:
    """Tests for _parse_bool utility function."""

    def test_parse_bool_with_true_string(self):
        """Test parsing various 'true' representations."""
        assert _parse_bool("true", default=False) is True
        assert _parse_bool("True", default=False) is True
        assert _parse_bool("TRUE", default=False) is True
        assert _parse_bool("yes", default=False) is True
        assert _parse_bool("Yes", default=False) is True
        assert _parse_bool("YES", default=False) is True
        assert _parse_bool("1", default=False) is True

    def test_parse_bool_with_false_string(self):
        """Test parsing various 'false' representations."""
        assert _parse_bool("false", default=True) is False
        assert _parse_bool("False", default=True) is False
        assert _parse_bool("FALSE", default=True) is False
        assert _parse_bool("no", default=True) is False
        assert _parse_bool("No", default=True) is False
        assert _parse_bool("NO", default=True) is False
        assert _parse_bool("0", default=True) is False

    def test_parse_bool_with_none_uses_default(self):
        """Test that None returns the default value."""
        assert _parse_bool(None, default=True) is True
        assert _parse_bool(None, default=False) is False

    def test_parse_bool_with_bool_returns_bool(self):
        """Test that bool values are returned as-is."""
        assert _parse_bool(True, default=False) is True
        assert _parse_bool(False, default=True) is False

    def test_parse_bool_with_invalid_string_returns_false(self):
        """Test that invalid strings return False (not the default)."""
        # This is the actual behavior - invalid strings return False
        assert _parse_bool("maybe", default=True) is False
        assert _parse_bool("???", default=False) is False


class TestConfigFromEnv:
    """Tests for Config.from_env() class method."""

    def make_getenv_mock(self, **overrides):
        """Helper to create a getenv mock with standard defaults + overrides."""

        def getenv_side_effect(key, default=None):
            # Always include these minimal required values
            env_vars = {
                "API_ID": "12345",
                "API_HASH": VALID_API_HASH,
                "EXPORT_PATH": "/tmp/test_export",
                "CACHE_FILE": "/tmp/test_cache.json",  # Avoid Path(None) error
            }
            # Merge with overrides
            env_vars.update(overrides)
            return env_vars.get(key, default)

        return getenv_side_effect

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_loads_basic_credentials(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test loading basic API credentials from environment."""

        mock_getenv.side_effect = self.make_getenv_mock(
            PHONE_NUMBER="+1234567890",
            SESSION_NAME="test_session",
        )

        config = Config.from_env()

        assert config.api_id == 12345
        assert config.api_hash == VALID_API_HASH
        assert config.phone_number == "+1234567890"
        assert config.session_name == "test_session"

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_uses_defaults_when_not_set(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test that default values are used when env vars are not set."""

        # Only provide minimal required values
        mock_getenv.side_effect = self.make_getenv_mock()

        config = Config.from_env()

        # Check defaults
        assert config.session_name == "tobs_session"
        assert config.media_subdir == "media"
        assert config.cache_subdir == "cache"
        assert config.log_level == "INFO"
        assert config.only_new is False
        assert config.media_download is True

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_parses_boolean_values(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test boolean parsing from environment variables."""

        mock_getenv.side_effect = self.make_getenv_mock(
            USE_ENTITY_FOLDERS="false",
            USE_STRUCTURED_EXPORT="true",
            ONLY_NEW="yes",
            MEDIA_DOWNLOAD="no",
            EXPORT_COMMENTS="1",
        )

        config = Config.from_env()

        assert config.use_entity_folders is False
        assert config.use_structured_export is True
        assert config.only_new is True
        assert config.media_download is False
        assert config.export_comments is True

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_parses_integer_values(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test integer parsing from environment variables."""

        mock_getenv.side_effect = self.make_getenv_mock(
            API_ID="99999",
            IMAGE_QUALITY="95",
            VIDEO_CRF="20",
            DIALOG_FETCH_LIMIT="50",
            MAX_FILE_SIZE_MB="5000",
            MAX_TOTAL_SIZE_GB="500",
        )

        config = Config.from_env()

        assert config.api_id == 99999
        assert config.image_quality == 95
        assert config.video_crf == 20
        assert config.dialog_fetch_limit == 50
        assert config.max_file_size_mb == 5000
        assert config.max_total_size_gb == 500

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_parses_float_values(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test float parsing from environment variables."""

        mock_getenv.side_effect = self.make_getenv_mock(
            MAX_ERROR_RATE="0.05",
        )

        config = Config.from_env()

        assert config.max_error_rate == 0.05

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_parses_export_targets(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test parsing comma-separated EXPORT_TARGETS."""

        mock_getenv.side_effect = self.make_getenv_mock(
            EXPORT_TARGETS="channel1,chat2,user3",
        )

        config = Config.from_env()

        assert len(config.export_targets) == 3
        assert all(isinstance(t, ExportTarget) for t in config.export_targets)
        assert config.export_targets[0].id == "channel1"
        assert config.export_targets[1].id == "chat2"
        assert config.export_targets[2].id == "user3"

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_handles_empty_export_targets(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test that empty EXPORT_TARGETS results in empty list."""

        mock_getenv.side_effect = self.make_getenv_mock(
            EXPORT_TARGETS="",
        )

        config = Config.from_env()

        assert config.export_targets == []

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_parses_proxy_configuration(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test parsing proxy configuration from environment."""

        mock_getenv.side_effect = self.make_getenv_mock(
            PROXY_TYPE="socks5",
            PROXY_ADDR="127.0.0.1",
            PROXY_PORT="9050",
        )

        config = Config.from_env()

        assert config.proxy_type == "socks5"
        assert config.proxy_addr == "127.0.0.1"
        assert config.proxy_port == 9050

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_handles_invalid_proxy_port(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test that invalid proxy port defaults to None."""

        mock_getenv.side_effect = self.make_getenv_mock(
            PROXY_TYPE="socks5",
            PROXY_ADDR="127.0.0.1",
            PROXY_PORT="not_a_number",
        )

        config = Config.from_env()

        assert config.proxy_port is None

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_parses_performance_profile(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test parsing performance profile from environment."""

        mock_getenv.side_effect = self.make_getenv_mock(
            PERFORMANCE_PROFILE="aggressive",
        )

        config = Config.from_env()

        assert config.performance_profile == "aggressive"

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_handles_invalid_performance_profile(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test that invalid performance profile falls back to 'balanced'."""

        mock_getenv.side_effect = self.make_getenv_mock(
            PERFORMANCE_PROFILE="invalid_profile",
        )

        config = Config.from_env()

        # Should fall back to balanced and log a warning
        assert config.performance_profile == "balanced"
        # Verify warning was logged
        mock_logger.warning.assert_called()

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_loads_dotenv_if_file_exists(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test that load_dotenv is called if .env file exists."""
        with patch("src.config.Path.exists", return_value=True):
            mock_getenv.side_effect = self.make_getenv_mock()

            _ = Config.from_env()

            # Verify load_dotenv was called
            mock_load_dotenv.assert_called_once()

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_skips_dotenv_if_file_missing(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test that load_dotenv is not called if .env file doesn't exist."""
        with patch("src.config.Path.exists", return_value=False):
            mock_getenv.side_effect = self.make_getenv_mock()

            _ = Config.from_env()

            # Verify load_dotenv was NOT called
            mock_load_dotenv.assert_not_called()

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    @patch("src.config.load_dotenv")
    @patch("src.config.os.getenv")
    def test_from_env_comprehensive(
        self, mock_getenv, mock_load_dotenv, mock_logger, mock_mkdir
    ):
        """Test comprehensive configuration from environment."""

        mock_getenv.side_effect = self.make_getenv_mock(
            API_ID="88888",
            PHONE_NUMBER="+9876543210",
            SESSION_NAME="comprehensive_session",
            EXPORT_PATH="/tmp/comprehensive_export",
            MEDIA_SUBDIR="custom_media",
            CACHE_SUBDIR="custom_cache",
            MONITORING_SUBDIR="custom_monitoring",
            USE_ENTITY_FOLDERS="true",
            USE_STRUCTURED_EXPORT="false",
            ONLY_NEW="no",
            MEDIA_DOWNLOAD="yes",
            EXPORT_COMMENTS="true",
            IMAGE_QUALITY="92",
            VIDEO_CRF="22",
            VIDEO_PRESET="medium",
            HW_ACCELERATION="cuda",
            USE_H265="yes",
            CACHE_FILE="/tmp/custom_cache.json",
            LOG_LEVEL="DEBUG",
            DIALOG_FETCH_LIMIT="30",
            PERFORMANCE_PROFILE="conservative",
            PROXY_TYPE="http",
            PROXY_ADDR="proxy.example.com",
            PROXY_PORT="8080",
            ENABLE_PERFORMANCE_MONITORING="false",
            PERFORMANCE_LOG_INTERVAL="120",
            MAX_ERROR_RATE="0.15",
            ERROR_COOLDOWN_TIME="450",
            MAX_FILE_SIZE_MB="3000",
            MAX_TOTAL_SIZE_GB="150",
            EXPORT_TARGETS="target1,target2,target3",
        )

        config = Config.from_env()

        # Verify comprehensive settings
        assert config.api_id == 88888
        assert config.phone_number == "+9876543210"
        assert config.session_name == "comprehensive_session"
        assert config.media_subdir == "custom_media"
        assert config.use_entity_folders is True
        assert config.use_structured_export is False
        assert config.only_new is False
        assert config.media_download is True
        assert config.export_comments is True
        assert config.image_quality == 92
        assert config.video_crf == 22
        assert config.video_preset == "medium"
        assert config.hw_acceleration == "cuda"
        assert config.use_h265 is True
        assert config.log_level == "DEBUG"
        assert config.dialog_fetch_limit == 30
        assert config.performance_profile == "conservative"
        assert config.proxy_type == "http"
        assert config.proxy_addr == "proxy.example.com"
        assert config.proxy_port == 8080
        assert config.enable_performance_monitoring is False
        assert config.performance_log_interval == 120
        assert config.max_error_rate == 0.15
        assert config.error_cooldown_time == 450
        assert config.max_file_size_mb == 3000
        assert config.max_total_size_gb == 150
        assert len(config.export_targets) == 3
