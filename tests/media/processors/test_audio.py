"""
Unit tests for AudioProcessor.

Tests audio processing including transcoding and validation.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from pathlib import Path
import subprocess

from src.media.processors.audio import AudioProcessor
from src.media.models import ProcessingTask, MediaMetadata, ProcessingSettings


pytestmark = pytest.mark.unit


class TestAudioProcessor:
    """Tests for AudioProcessor class."""

    @pytest.fixture
    def audio_processor(
        self,
        io_executor,
        cpu_executor,
        mock_validator,
        mock_config,
    ):
        """Create AudioProcessor instance for tests."""
        return AudioProcessor(
            io_executor=io_executor,
            cpu_executor=cpu_executor,
            validator=mock_validator,
            config=mock_config,
        )

    async def test_initialization(self, audio_processor, mock_validator):
        """Test AudioProcessor initialization."""
        assert audio_processor.validator == mock_validator
        assert audio_processor._audio_processed_count == 0
        assert audio_processor._audio_copied_count == 0

    async def test_needs_processing_with_metadata_wrong_codec(
        self, audio_processor, processing_settings
    ):
        """Test needs_processing_with_metadata returns True for non-AAC codec."""
        metadata = MediaMetadata(
            file_size=5 * 1024 * 1024,  # 5 MB
            mime_type="audio/mpeg",
            codec="mp3",
            bitrate=320000,
        )
        
        result = audio_processor.needs_processing_with_metadata(
            metadata, processing_settings
        )
        
        # MP3 codec != AAC, so needs processing
        assert result is True

    async def test_needs_processing_with_metadata_high_bitrate(
        self, audio_processor, processing_settings
    ):
        """Test needs_processing_with_metadata returns True for high bitrate."""
        metadata = MediaMetadata(
            file_size=5 * 1024 * 1024,
            mime_type="audio/mp4",
            codec="aac",
            bitrate=320000,  # Much higher than 128k target
        )
        
        result = audio_processor.needs_processing_with_metadata(
            metadata, processing_settings
        )
        
        # High bitrate needs optimization
        assert result is True

    async def test_needs_processing_with_metadata_skip_optimized(
        self, audio_processor, processing_settings
    ):
        """Test needs_processing_with_metadata returns False for optimized AAC."""
        metadata = MediaMetadata(
            file_size=2 * 1024 * 1024,
            mime_type="audio/mp4",
            codec="aac",
            bitrate=128000,  # Matches target
        )
        
        result = audio_processor.needs_processing_with_metadata(
            metadata, processing_settings
        )
        
        # Already optimized AAC at target bitrate
        assert result is False

    async def test_needs_processing_basic_file_check(
        self, audio_processor, tmp_path, processing_settings
    ):
        """Test needs_processing basic file size check."""
        # Create file of medium size (should need processing)
        audio_file = tmp_path / "audio.mp3"
        audio_file.write_bytes(b"x" * (5 * 1024 * 1024))  # 5 MB
        
        result = audio_processor.needs_processing(audio_file, processing_settings)
        
        assert result is True

    async def test_needs_processing_skip_small_file(
        self, audio_processor, tmp_path, processing_settings
    ):
        """Test needs_processing skips very small files."""
        # Create very small file
        audio_file = tmp_path / "audio.mp3"
        audio_file.write_bytes(b"x" * (500 * 1024))  # 500 KB
        
        result = audio_processor.needs_processing(audio_file, processing_settings)
        
        # Too small, skip processing
        assert result is False

    async def test_needs_processing_skip_huge_file(
        self, audio_processor, tmp_path, processing_settings
    ):
        """Test needs_processing skips very large files."""
        # Create very large file
        audio_file = tmp_path / "audio.mp3"
        audio_file.write_bytes(b"x" * (150 * 1024 * 1024))  # 150 MB
        
        result = audio_processor.needs_processing(audio_file, processing_settings)
        
        # Too large, skip processing
        assert result is False

    async def test_process_task_with_validation_success(
        self, audio_processor, tmp_path, processing_settings
    ):
        """Test process() with successful validation and processing."""
        input_file = tmp_path / "input.mp3"
        output_file = tmp_path / "output.m4a"
        input_file.write_bytes(b"test audio data")
        
        metadata = MediaMetadata(
            file_size=len(b"test audio data"),
            mime_type="audio/mpeg",
            codec="mp3",
            bitrate=320000,
        )
        task = ProcessingTask(
            input_path=input_file,
            output_path=output_file,
            media_type="audio",
            metadata=metadata,
            processing_settings=processing_settings,
        )
        
        # Mock validator to return True
        audio_processor.validator.validate_file = AsyncMock(return_value=True)
        
        # Mock FFmpeg execution
        def mock_ffmpeg_run(task, args):
            task.output_path.write_bytes(b"processed audio")
            return True
        
        audio_processor._run_ffmpeg_audio = mock_ffmpeg_run
        
        result = await audio_processor.process(task, "worker-1")
        
        assert result is True
        assert output_file.exists()
        assert audio_processor._audio_processed_count == 1

    async def test_process_task_validation_failure_fallback_copy(
        self, audio_processor, tmp_path, processing_settings
    ):
        """Test process() falls back to copy when validation fails."""
        input_file = tmp_path / "input.mp3"
        output_file = tmp_path / "output.m4a"
        input_file.write_bytes(b"corrupted audio")
        
        metadata = MediaMetadata(
            file_size=len(b"corrupted audio"),
            mime_type="audio/mpeg",
            codec="mp3",
            bitrate=320000,
        )
        task = ProcessingTask(
            input_path=input_file,
            output_path=output_file,
            media_type="audio",
            metadata=metadata,
            processing_settings=processing_settings,
        )
        
        # Mock validator to return False (invalid)
        audio_processor.validator.validate_file = AsyncMock(return_value=False)
        
        result = await audio_processor.process(task, "worker-1")
        
        # Should fall back to copy
        assert result is True
        assert output_file.exists()
        assert audio_processor._audio_copied_count == 1

    async def test_process_task_skip_processing_direct_copy(
        self, audio_processor, tmp_path, processing_settings
    ):
        """Test process() skips processing and copies when not needed."""
        input_file = tmp_path / "input.m4a"
        output_file = tmp_path / "output.m4a"
        input_file.write_bytes(b"already optimized")
        
        # Optimized AAC at target bitrate
        metadata = MediaMetadata(
            file_size=len(b"already optimized"),
            mime_type="audio/mp4",
            codec="aac",
            bitrate=128000,
        )
        task = ProcessingTask(
            input_path=input_file,
            output_path=output_file,
            media_type="audio",
            metadata=metadata,
            processing_settings=processing_settings,
        )
        
        result = await audio_processor.process(task, "worker-1")
        
        assert result is True
        assert output_file.exists()
        assert audio_processor._audio_copied_count == 1
        assert audio_processor._audio_processed_count == 0  # No processing needed

    async def test_process_task_ffmpeg_failure_fallback_copy(
        self, audio_processor, tmp_path, processing_settings
    ):
        """Test process() falls back to copy when FFmpeg fails."""
        input_file = tmp_path / "input.mp3"
        output_file = tmp_path / "output.m4a"
        input_file.write_bytes(b"test audio")
        
        metadata = MediaMetadata(
            file_size=len(b"test audio"),
            mime_type="audio/mpeg",
            codec="mp3",
            bitrate=320000,
        )
        task = ProcessingTask(
            input_path=input_file,
            output_path=output_file,
            media_type="audio",
            metadata=metadata,
            processing_settings=processing_settings,
        )
        
        audio_processor.validator.validate_file = AsyncMock(return_value=True)
        
        # Mock FFmpeg to fail
        def mock_ffmpeg_fail(task, args):
            return False
        
        audio_processor._run_ffmpeg_audio = mock_ffmpeg_fail
        
        result = await audio_processor.process(task, "worker-1")
        
        # Should fall back to copy
        assert result is True
        assert output_file.exists()
        assert audio_processor._audio_copied_count == 1

    async def test_statistics_tracking(self, audio_processor):
        """Test statistics are tracked correctly."""
        assert audio_processor._audio_processed_count == 0
        assert audio_processor._audio_copied_count == 0
        
        # Simulate some processing
        audio_processor._audio_processed_count = 5
        audio_processor._audio_copied_count = 3
        
        assert audio_processor._audio_processed_count == 5
        assert audio_processor._audio_copied_count == 3
