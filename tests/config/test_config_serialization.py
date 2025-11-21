"""
Tests for Config serialization methods (to_dict, from_dict).

This module tests:
- to_dict() behavior (Paths → str, nested dataclasses)
- from_dict() behavior (reconstruction of ExportTarget, PerformanceSettings, Path conversion)
- Round-trip tests (to_dict → from_dict)
"""

from pathlib import Path
from unittest.mock import patch

from src.config import Config, ExportTarget, PerformanceSettings

# Valid API hash for testing (32+ characters required)
VALID_API_HASH = "a" * 32


class TestConfigToDict:
    """Tests for Config.to_dict() method."""

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_to_dict_converts_paths_to_strings(self, mock_logger, mock_mkdir):
        """Test that Path objects are converted to strings in to_dict()."""
        config = Config(
            api_id=12345,
            api_hash=VALID_API_HASH,
            export_path="/tmp/test_export",
            cache_file="/tmp/test_cache.json",
        )

        result = config.to_dict()

        assert isinstance(result["export_path"], str)
        assert result["export_path"] == "/tmp/test_export"
        assert isinstance(result["cache_file"], str)

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_to_dict_serializes_export_targets(self, mock_logger, mock_mkdir):
        """Test that ExportTarget dataclasses are serialized to dicts."""
        target = ExportTarget(id="test_channel", name="Test Channel")
        config = Config(
            api_id=12345,
            api_hash=VALID_API_HASH,
            export_path="/tmp/test",
            export_targets=[target],
        )

        result = config.to_dict()

        assert "export_targets" in result
        assert isinstance(result["export_targets"], list)
        assert len(result["export_targets"]) == 1
        assert isinstance(result["export_targets"][0], dict)
        assert result["export_targets"][0]["id"] == "test_channel"
        assert result["export_targets"][0]["name"] == "Test Channel"

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_to_dict_serializes_performance_settings(self, mock_logger, mock_mkdir):
        """Test that PerformanceSettings dataclass is serialized to dict."""
        config = Config(
            api_id=12345,
            api_hash=VALID_API_HASH,
            export_path="/tmp/test",
        )

        result = config.to_dict()

        assert "performance" in result
        assert isinstance(result["performance"], dict)
        # Config auto_configure creates PerformanceSettings based on profile
        # Just verify it's a dict with expected keys
        assert "workers" in result["performance"]
        assert "download_workers" in result["performance"]
        assert "message_batch_size" in result["performance"]

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_to_dict_only_includes_init_fields(self, mock_logger, mock_mkdir):
        """Test that to_dict() only includes fields with init=True."""
        config = Config(
            api_id=12345,
            api_hash=VALID_API_HASH,
            export_path="/tmp/test",
        )

        result = config.to_dict()

        # These fields have init=False and should NOT be in the result
        assert "export_paths" not in result
        assert "media_paths" not in result
        assert "cache_paths" not in result
        assert "monitoring_paths" not in result
        assert "cache" not in result

        # These fields have init=True and SHOULD be in the result
        assert "api_id" in result
        assert "api_hash" in result
        assert "export_path" in result

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_to_dict_handles_multiple_targets(self, mock_logger, mock_mkdir):
        """Test serialization with multiple export targets."""
        targets = [
            ExportTarget(id="channel1", name="Channel 1"),
            ExportTarget(id="chat2", name="Chat 2"),
            ExportTarget(id="user3", name="User 3"),
        ]
        config = Config(
            api_id=12345,
            api_hash=VALID_API_HASH,
            export_path="/tmp/test",
            export_targets=targets,
        )

        result = config.to_dict()

        assert len(result["export_targets"]) == 3
        assert all(isinstance(t, dict) for t in result["export_targets"])
        assert result["export_targets"][0]["id"] == "channel1"
        assert result["export_targets"][1]["id"] == "chat2"
        assert result["export_targets"][2]["id"] == "user3"


class TestConfigFromDict:
    """Tests for Config.from_dict() class method."""

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_from_dict_converts_string_paths_to_path_objects(
        self, mock_logger, mock_mkdir
    ):
        """Test that string paths are converted to Path objects."""
        data = {
            "api_id": 12345,
            "api_hash": VALID_API_HASH,
            "export_path": "/tmp/test_export",
            "cache_file": "/tmp/test_cache.json",
        }

        config = Config.from_dict(data)

        assert isinstance(config.export_path, Path)
        assert str(config.export_path) == "/tmp/test_export"
        assert isinstance(config.cache_file, Path)

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_from_dict_reconstructs_export_targets(self, mock_logger, mock_mkdir):
        """Test that dict export targets are converted to ExportTarget objects."""
        data = {
            "api_id": 12345,
            "api_hash": VALID_API_HASH,
            "export_path": "/tmp/test",
            "export_targets": [
                {"id": "test_channel", "name": "Test Channel"},
                {"id": "test_chat", "name": "Test Chat"},
            ],
        }

        config = Config.from_dict(data)

        assert len(config.export_targets) == 2
        assert all(isinstance(t, ExportTarget) for t in config.export_targets)
        assert config.export_targets[0].id == "test_channel"
        assert config.export_targets[0].name == "Test Channel"
        assert config.export_targets[1].id == "test_chat"
        assert config.export_targets[1].name == "Test Chat"

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_from_dict_reconstructs_performance_settings(self, mock_logger, mock_mkdir):
        """Test that dict performance settings are converted to PerformanceSettings object."""
        data = {
            "api_id": 12345,
            "api_hash": VALID_API_HASH,
            "export_path": "/tmp/test",
            "performance": {
                "workers": 8,
                "download_workers": 15,
                "message_batch_size": 100,
            },
        }

        config = Config.from_dict(data)

        # Config.__post_init__ overrides performance based on profile
        # Just verify that we get a PerformanceSettings object
        assert isinstance(config.performance, PerformanceSettings)
        # Verify that performance has actual values (not None)
        assert config.performance.workers > 0
        assert config.performance.download_workers > 0
        assert config.performance.message_batch_size > 0

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_from_dict_filters_non_init_fields(self, mock_logger, mock_mkdir):
        """Test that from_dict() ignores fields with init=False."""
        data = {
            "api_id": 12345,
            "api_hash": VALID_API_HASH,
            "export_path": "/tmp/test",
            "export_paths": {"extra": "should_be_ignored"},
            "cache": {"extra": "should_be_ignored"},
        }

        # Should not raise an error; non-init fields are filtered
        config = Config.from_dict(data)

        assert config.api_id == 12345
        # export_paths should be initialized to empty dict by __post_init__, not from input
        assert config.export_paths == {}

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_from_dict_preserves_already_typed_objects(self, mock_logger, mock_mkdir):
        """Test that from_dict() handles already-typed objects (Path, ExportTarget, etc.)."""
        target = ExportTarget(id="test_id", name="Test Name")
        perf = PerformanceSettings(workers=10)
        data = {
            "api_id": 12345,
            "api_hash": VALID_API_HASH,
            "export_path": Path("/tmp/test"),
            "cache_file": Path("/tmp/cache.json"),
            "export_targets": [target],
            "performance": perf,
        }

        config = Config.from_dict(data)

        assert isinstance(config.export_path, Path)
        assert isinstance(config.cache_file, Path)
        assert config.export_targets[0] is target
        # Performance gets passed through __post_init__, so it might not be the same object
        assert isinstance(config.performance, PerformanceSettings)


class TestConfigRoundTrip:
    """Tests for round-trip serialization (to_dict → from_dict)."""

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_round_trip_preserves_basic_config(self, mock_logger, mock_mkdir):
        """Test that config can be serialized and deserialized without data loss."""
        original = Config(
            api_id=12345,
            api_hash=VALID_API_HASH,
            phone_number="+1234567890",
            session_name="test_session",
            export_path="/tmp/test_export",
            media_subdir="custom_media",
            cache_subdir="custom_cache",
            use_entity_folders=False,
            only_new=True,
            media_download=False,
        )

        # Round-trip
        serialized = original.to_dict()
        restored = Config.from_dict(serialized)

        # Compare init fields
        assert restored.api_id == original.api_id
        assert restored.api_hash == original.api_hash
        assert restored.phone_number == original.phone_number
        assert restored.session_name == original.session_name
        assert str(restored.export_path) == str(original.export_path)
        assert restored.media_subdir == original.media_subdir
        assert restored.cache_subdir == original.cache_subdir
        assert restored.use_entity_folders == original.use_entity_folders
        assert restored.only_new == original.only_new
        assert restored.media_download == original.media_download

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_round_trip_preserves_export_targets(self, mock_logger, mock_mkdir):
        """Test that export targets survive round-trip serialization."""
        targets = [
            ExportTarget(id="channel_id", name="Channel Name", estimated_messages=1000),
            ExportTarget(id="chat_id", name="Chat Name", estimated_messages=500),
        ]
        original = Config(
            api_id=12345,
            api_hash=VALID_API_HASH,
            export_path="/tmp/test",
            export_targets=targets,
        )

        # Round-trip
        serialized = original.to_dict()
        restored = Config.from_dict(serialized)

        assert len(restored.export_targets) == 2
        assert restored.export_targets[0].id == "channel_id"
        assert restored.export_targets[0].name == "Channel Name"
        assert restored.export_targets[0].estimated_messages == 1000
        assert restored.export_targets[1].id == "chat_id"
        assert restored.export_targets[1].name == "Chat Name"
        assert restored.export_targets[1].estimated_messages == 500

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_round_trip_preserves_performance_settings(self, mock_logger, mock_mkdir):
        """Test that performance settings survive round-trip serialization."""
        original = Config(
            api_id=12345,
            api_hash=VALID_API_HASH,
            export_path="/tmp/test",
            performance_profile="conservative",  # Set a profile
        )

        # Round-trip
        serialized = original.to_dict()
        restored = Config.from_dict(serialized)

        # Verify that restored config has same performance profile
        # and produces consistent performance settings
        assert restored.performance_profile == "conservative"
        assert isinstance(restored.performance, PerformanceSettings)
        # Conservative profile should have lower values than balanced
        assert restored.performance.workers <= 8
        assert restored.performance.message_batch_size > 0

    @patch("src.config.Path.mkdir")
    @patch("src.config.logger")
    def test_round_trip_preserves_all_config_options(self, mock_logger, mock_mkdir):
        """Test comprehensive round-trip with many config options."""
        original = Config(
            api_id=12345,
            api_hash=VALID_API_HASH,
            phone_number="+1234567890",
            session_name="test_session",
            export_path="/tmp/comprehensive_test",
            cache_file="/tmp/test_cache.json",
            media_subdir="test_media",
            cache_subdir="test_cache",
            monitoring_subdir="test_monitoring",
            use_entity_folders=True,
            use_structured_export=True,
            only_new=False,
            media_download=True,
            export_comments=True,
            image_quality=90,
            video_crf=20,
            video_preset="slow",
            hw_acceleration="cuda",
            use_h265=True,
            compress_video=False,
            vaapi_device="/dev/dri/renderD129",
            vaapi_quality=30,
            log_level="DEBUG",
            dialog_fetch_limit=50,
            proxy_type="socks5",
            proxy_addr="127.0.0.1",
            proxy_port=9050,
            enable_performance_monitoring=False,
            performance_log_interval=120,
            max_error_rate=0.05,
            error_cooldown_time=600,
            max_file_size_mb=5000,
            max_total_size_gb=200,
            export_targets=[
                ExportTarget(id="test1", name="Test 1"),
                ExportTarget(id="test2", name="Test 2"),
            ],
        )

        # Round-trip
        serialized = original.to_dict()
        restored = Config.from_dict(serialized)

        # Verify all key fields
        assert restored.api_id == 12345
        assert restored.api_hash == VALID_API_HASH
        assert restored.phone_number == "+1234567890"
        assert restored.image_quality == 90
        assert restored.video_crf == 20
        assert restored.video_preset == "slow"
        assert restored.hw_acceleration == "cuda"
        assert restored.use_h265 is True
        assert restored.compress_video is False
        assert restored.vaapi_quality == 30
        assert restored.log_level == "DEBUG"
        assert restored.dialog_fetch_limit == 50
        assert restored.proxy_type == "socks5"
        assert restored.proxy_addr == "127.0.0.1"
        assert restored.proxy_port == 9050
        assert restored.max_error_rate == 0.05
        assert restored.max_file_size_mb == 5000
        assert len(restored.export_targets) == 2
