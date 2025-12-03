"""
Unit tests for media data models.

Tests the MediaMetadata, ProcessingSettings, and ProcessingTask dataclasses.
"""

import time
from pathlib import Path

import pytest

from src.media.models import MediaMetadata, ProcessingSettings, ProcessingTask


class TestMediaMetadata:
    """Tests for MediaMetadata dataclass."""

    def test_creation_minimal(self):
        """Test creating MediaMetadata with minimal fields."""
        metadata = MediaMetadata(
            file_size=1024,
            mime_type="video/mp4",
        )

        assert metadata.file_size == 1024
        assert metadata.mime_type == "video/mp4"
        assert metadata.duration is None
        assert metadata.width is None
        assert metadata.checksum is None

    def test_creation_full_video(self):
        """Test creating MediaMetadata for video with all fields."""
        metadata = MediaMetadata(
            file_size=1024 * 1024 * 10,
            mime_type="video/mp4",
            duration=120.5,
            width=1920,
            height=1080,
            format="h264",
            bitrate=5000000,
            codec="h264",
            fps=30.0,
            checksum="abc123",
        )

        assert metadata.file_size == 1024 * 1024 * 10
        assert metadata.duration == 120.5
        assert metadata.width == 1920
        assert metadata.height == 1080
        assert metadata.fps == 30.0

    def test_creation_audio(self):
        """Test creating MediaMetadata for audio."""
        metadata = MediaMetadata(
            file_size=1024 * 1024 * 5,
            mime_type="audio/mpeg",
            duration=180.0,
            bitrate=320000,
            codec="mp3",
            channels=2,
            sample_rate=44100,
        )

        assert metadata.mime_type == "audio/mpeg"
        assert metadata.channels == 2
        assert metadata.sample_rate == 44100

    def test_creation_image(self):
        """Test creating MediaMetadata for image."""
        metadata = MediaMetadata(
            file_size=1024 * 1024 * 2,
            mime_type="image/jpeg",
            width=4000,
            height=3000,
            format="JPEG",
        )

        assert metadata.width == 4000
        assert metadata.height == 3000
        assert metadata.format == "JPEG"


class TestProcessingSettings:
    """Tests for ProcessingSettings dataclass."""

    def test_default_settings(self):
        """Test default processing settings."""
        settings = ProcessingSettings()

        assert settings.max_video_resolution == (1920, 1080)
        assert settings.max_video_bitrate == 2000
        assert settings.max_audio_bitrate == 128
        assert settings.video_codec == "libx264"
        assert settings.audio_codec == "aac"
        assert settings.image_quality == 85
        assert settings.enable_hardware_acceleration is True
        assert settings.prefer_vp9 is False

    def test_custom_settings(self):
        """Test custom processing settings."""
        settings = ProcessingSettings(
            max_video_resolution=(1280, 720),
            max_video_bitrate=1500,
            video_codec="libx265",
            enable_hardware_acceleration=False,
            aggressive_compression=True,
        )

        assert settings.max_video_resolution == (1280, 720)
        assert settings.max_video_bitrate == 1500
        assert settings.video_codec == "libx265"
        assert settings.enable_hardware_acceleration is False
        assert settings.aggressive_compression is True


class TestProcessingTask:
    """Tests for ProcessingTask dataclass."""

    def test_creation_minimal(self, tmp_path: Path):
        """Test creating ProcessingTask with minimal fields."""
        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output.mp4"

        task = ProcessingTask(
            input_path=input_path,
            output_path=output_path,
            media_type="video",
        )

        assert task.input_path == input_path
        assert task.output_path == output_path
        assert task.media_type == "video"
        assert task.priority == 5  # Default
        assert task.attempts == 0
        assert task.max_attempts == 3

    def test_creation_with_metadata(
        self, tmp_path: Path, video_metadata: MediaMetadata
    ):
        """Test creating ProcessingTask with metadata."""
        task = ProcessingTask(
            input_path=tmp_path / "input.mp4",
            output_path=tmp_path / "output.mp4",
            media_type="video",
            metadata=video_metadata,
        )

        assert task.metadata is not None
        assert task.metadata.mime_type == "video/mp4"
        assert task.metadata.width == 1920

    def test_created_at_timestamp(self, tmp_path: Path):
        """Test that created_at is set automatically."""
        before = time.time()

        task = ProcessingTask(
            input_path=tmp_path / "input.mp4",
            output_path=tmp_path / "output.mp4",
            media_type="video",
        )

        after = time.time()

        assert before <= task.created_at <= after

    def test_custom_priority(self, tmp_path: Path):
        """Test task with custom priority."""
        task = ProcessingTask(
            input_path=tmp_path / "input.mp4",
            output_path=tmp_path / "output.mp4",
            media_type="video",
            priority=10,
        )

        assert task.priority == 10

    def test_attempts_tracking(self, tmp_path: Path):
        """Test attempts tracking."""
        task = ProcessingTask(
            input_path=tmp_path / "input.mp4",
            output_path=tmp_path / "output.mp4",
            media_type="video",
            max_attempts=5,
        )

        assert task.attempts == 0
        assert task.max_attempts == 5

        task.attempts += 1
        assert task.attempts == 1

    def test_with_processing_settings(
        self, tmp_path: Path, processing_settings: ProcessingSettings
    ):
        """Test task with custom processing settings."""
        task = ProcessingTask(
            input_path=tmp_path / "input.mp4",
            output_path=tmp_path / "output.mp4",
            media_type="video",
            processing_settings=processing_settings,
        )

        assert task.processing_settings is not None
        assert task.processing_settings.max_video_bitrate == 2000
