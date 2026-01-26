"""
Tests for slotted dataclasses (TIER C-2).

Validates that all dataclasses properly use slots=True for memory optimization
and that they maintain expected behavior.
"""

import sys
from dataclasses import fields

import pytest

# Import all slotted dataclasses for testing
from src.config import Config, ExportTarget, PerformanceSettings, TranscriptionConfig
from src.core.performance import (
    ComponentStats,
    PerformanceAlert,
    PerformanceProfile,
    SystemMetrics,
)
from src.core.thread_pool import ThreadPoolMetrics
from src.export_reporter import EntityReport, ExportMetrics, SystemInfo
from src.hot_zones_manager import HotZone, SlowChunkRecord
from src.media.download_queue import DownloadTask, QueueStats
from src.media.lazy_loader import LazyMediaMetadata
from src.media.models import MediaMetadata, ProcessingSettings, ProcessingTask
from src.media.parallel_processor import ParallelMediaConfig, ParallelMediaMetrics
from src.media.processors.transcription import TranscriptionResult
from src.media.zero_copy import ZeroCopyConfig, ZeroCopyStats
from src.ui.output_manager import ProgressUpdate


# List of all slotted dataclasses to test
SLOTTED_DATACLASSES = [
    # src/config.py
    ExportTarget,
    PerformanceSettings,
    TranscriptionConfig,
    Config,
    # src/export_reporter.py
    ExportMetrics,
    SystemInfo,
    EntityReport,
    # src/media/models.py
    MediaMetadata,
    ProcessingSettings,
    ProcessingTask,
    # src/core/performance.py
    SystemMetrics,
    PerformanceAlert,
    PerformanceProfile,
    ComponentStats,
    # src/hot_zones_manager.py
    HotZone,
    SlowChunkRecord,
    # src/media/zero_copy.py
    ZeroCopyConfig,
    ZeroCopyStats,
    # src/core/thread_pool.py
    ThreadPoolMetrics,
    # src/media/processors/transcription.py
    TranscriptionResult,
    # src/media/parallel_processor.py
    ParallelMediaConfig,
    ParallelMediaMetrics,
    # src/media/download_queue.py
    DownloadTask,
    QueueStats,
    # src/ui/output_manager.py
    ProgressUpdate,
    # src/media/lazy_loader.py
    LazyMediaMetadata,
]


class TestSlottedDataclasses:
    """Test suite for slotted dataclasses."""

    @pytest.mark.parametrize("dataclass_type", SLOTTED_DATACLASSES)
    def test_has_slots(self, dataclass_type):
        """Test that dataclass has __slots__ defined."""
        assert hasattr(
            dataclass_type, "__slots__"
        ), f"{dataclass_type.__name__} should have __slots__"

    @pytest.mark.parametrize("dataclass_type", SLOTTED_DATACLASSES)
    def test_no_dict(self, dataclass_type):
        """Test that instances don't have __dict__ (memory optimization)."""
        # Create instance with minimal required fields
        field_list = fields(dataclass_type)
        required_fields = {
            f.name: self._get_default_value(f) for f in field_list if f.default == f.default_factory == dataclass_type
        }

        # For dataclasses with all optional fields or defaults
        try:
            if required_fields:
                # Try to create with only required fields
                # This is complex, so we'll use a simpler approach
                pass
            instance = dataclass_type.__new__(dataclass_type)
        except Exception:
            # If we can't create a minimal instance, skip this specific test
            pytest.skip(f"Cannot create minimal instance of {dataclass_type.__name__}")

        # Check that __dict__ is not accessible
        assert not hasattr(
            instance, "__dict__"
        ), f"{dataclass_type.__name__} instances should not have __dict__"

    def _get_default_value(self, field):
        """Get a default value for a field type."""
        if field.type == int:
            return 0
        elif field.type == str:
            return ""
        elif field.type == float:
            return 0.0
        elif field.type == bool:
            return False
        return None

    def test_simple_dataclass_slots_behavior(self):
        """Test slots behavior with a simple dataclass (MediaMetadata)."""
        # Create instance
        metadata = MediaMetadata(file_size=1024, mime_type="image/jpeg")

        # Should have defined attributes
        assert metadata.file_size == 1024
        assert metadata.mime_type == "image/jpeg"

        # Should not have __dict__
        assert not hasattr(metadata, "__dict__")

        # Should not allow adding new attributes dynamically
        with pytest.raises(AttributeError):
            metadata.new_attribute = "value"  # type: ignore

    def test_dataclass_with_methods_and_slots(self):
        """Test that dataclass with methods works correctly with slots."""
        # ComponentStats has methods like record_call
        stats = ComponentStats(name="test_component")

        # Should work normally
        assert stats.name == "test_component"
        assert stats.calls_total == 0

        # Methods should work
        stats.record_call(duration=1.5, success=True)
        assert stats.calls_total == 1
        assert stats.calls_successful == 1

        # Should not have __dict__
        assert not hasattr(stats, "__dict__")

    def test_dataclass_with_property_and_slots(self):
        """Test that @property works with slotted dataclasses."""
        # ComponentStats has @property success_rate
        stats = ComponentStats(name="test", calls_total=10, calls_successful=8)

        # Property should work
        assert stats.success_rate == 0.8

        # Should not have __dict__
        assert not hasattr(stats, "__dict__")

    def test_dataclass_with_post_init_and_slots(self):
        """Test that __post_init__ works with slotted dataclasses."""
        # SystemInfo has __post_init__
        sys_info = SystemInfo()

        # __post_init__ should have run
        assert sys_info.platform != ""
        assert sys_info.python_version != ""

        # Should not have __dict__
        assert not hasattr(sys_info, "__dict__")

    def test_memory_optimization_measurement(self):
        """Measure memory savings from slots (informational test)."""
        # This test demonstrates the memory savings but doesn't assert
        # Create multiple instances to see memory difference

        # Create 1000 instances of MediaMetadata
        instances = [
            MediaMetadata(file_size=i, mime_type="test") for i in range(1000)
        ]

        # Check first instance
        assert not hasattr(instances[0], "__dict__")

        # Calculate approximate memory per instance
        # With slots: ~56 bytes base + field storage
        # Without slots: ~280 bytes (__dict__ overhead) + field storage
        # This test just verifies slots are active, not exact memory

    def test_dataclass_with_default_factory_and_slots(self):
        """Test that field(default_factory=...) works with slots."""
        # ExportMetrics uses field(default_factory=...)
        metrics = ExportMetrics()

        # Default factory should work
        assert isinstance(metrics.errors, list)
        assert isinstance(metrics.warnings, list)
        assert len(metrics.errors) == 0

        # Should not have __dict__
        assert not hasattr(metrics, "__dict__")

        # Should not allow new attributes
        with pytest.raises(AttributeError):
            metrics.new_field = "value"  # type: ignore


class TestSlottedDataclassesIntegration:
    """Integration tests for slotted dataclasses in real scenarios."""

    def test_config_creation_with_slots(self):
        """Test creating Config with slots."""
        config = Config(api_id=12345, api_hash="a" * 32)

        assert config.api_id == 12345
        assert config.api_hash == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert not hasattr(config, "__dict__")

    def test_performance_settings_with_slots(self):
        """Test PerformanceSettings with all its fields."""
        settings = PerformanceSettings()

        # Check defaults
        assert settings.workers == 8
        assert settings.media_download_workers == 8  # Auto-tuned on init
        assert not hasattr(settings, "__dict__")

        # Can modify existing fields
        settings.workers = 16
        assert settings.workers == 16

    def test_download_task_with_property_and_slots(self):
        """Test DownloadTask with @property methods and slots."""
        from pathlib import Path
        from unittest.mock import Mock

        mock_message = Mock()
        task = DownloadTask(
            task_id="test_123",
            message_id=123,
            message=mock_message,
            entity_id="test_entity",
            output_path=Path("/tmp/test"),
            media_type="photo",
        )

        # Properties should work
        assert task.duration is None  # Not completed yet
        assert task.wait_time > 0  # Time since creation

        # Should not have __dict__
        assert not hasattr(task, "__dict__")
