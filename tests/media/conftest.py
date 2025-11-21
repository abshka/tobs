"""
Fixtures specific to media processing tests.
"""

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.media.models import MediaMetadata, ProcessingSettings, ProcessingTask


@pytest.fixture
def processing_settings() -> ProcessingSettings:
    """Default processing settings for tests."""
    return ProcessingSettings(
        max_video_resolution=(1920, 1080),
        max_video_bitrate=2000,
        max_audio_bitrate=128,
        video_codec="libx264",
        audio_codec="aac",
        image_quality=85,
        image_max_size=(2048, 2048),
        enable_hardware_acceleration=False,  # Disable for tests
        prefer_vp9=False,
        aggressive_compression=False,
    )


@pytest.fixture
def video_metadata() -> MediaMetadata:
    """Sample video metadata."""
    return MediaMetadata(
        file_size=1024 * 1024 * 10,  # 10 MB
        mime_type="video/mp4",
        duration=60.0,
        width=1920,
        height=1080,
        format="h264",
        bitrate=5000000,
        codec="h264",
        fps=30.0,
        checksum="abc123",
    )


@pytest.fixture
def audio_metadata() -> MediaMetadata:
    """Sample audio metadata."""
    return MediaMetadata(
        file_size=1024 * 1024 * 5,  # 5 MB
        mime_type="audio/mpeg",
        duration=180.0,
        bitrate=320000,
        codec="mp3",
        channels=2,
        sample_rate=44100,
        checksum="def456",
    )


@pytest.fixture
def image_metadata() -> MediaMetadata:
    """Sample image metadata."""
    return MediaMetadata(
        file_size=1024 * 1024 * 3,  # 3 MB
        mime_type="image/jpeg",
        width=4000,
        height=3000,
        format="JPEG",
        checksum="ghi789",
    )


@pytest.fixture
def processing_task(tmp_path: Path, video_metadata: MediaMetadata) -> ProcessingTask:
    """Sample processing task."""
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    
    # Create a dummy input file
    input_path.write_bytes(b"dummy video content")
    
    return ProcessingTask(
        input_path=input_path,
        output_path=output_path,
        media_type="video",
        priority=5,
        metadata=video_metadata,
        attempts=0,
        max_attempts=3,
    )


@pytest.fixture
def io_executor() -> ThreadPoolExecutor:
    """IO executor for tests."""
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test_io")
    yield executor
    executor.shutdown(wait=True)


@pytest.fixture
def cpu_executor() -> ThreadPoolExecutor:
    """CPU executor for tests."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test_cpu")
    yield executor
    executor.shutdown(wait=True)


@pytest.fixture
def mock_hw_detector() -> MagicMock:
    """Mock hardware acceleration detector."""
    detector = MagicMock()
    detector.available_encoders = {
        "vaapi": False,
        "nvidia": False,
        "intel": False,
        "amd": False,
    }
    detector.detect_hardware_acceleration = AsyncMock(
        return_value=detector.available_encoders
    )
    detector.get_best_video_encoder = Mock(return_value="libx264")
    return detector


@pytest.fixture
def mock_metadata_extractor() -> AsyncMock:
    """Mock metadata extractor."""
    extractor = AsyncMock()
    extractor.get_metadata = AsyncMock()
    return extractor


@pytest.fixture
def mock_validator() -> AsyncMock:
    """Mock media validator."""
    validator = AsyncMock()
    validator.validate_file = AsyncMock(return_value=True)
    return validator


@pytest.fixture
def sample_video_file(tmp_path: Path) -> Path:
    """Create a sample video file for testing."""
    video_file = tmp_path / "sample_video.mp4"
    # Create a minimal valid MP4 file (simplified)
    video_file.write_bytes(b"ftypisom" + b"\x00" * 1024)
    return video_file


@pytest.fixture
def sample_audio_file(tmp_path: Path) -> Path:
    """Create a sample audio file for testing."""
    audio_file = tmp_path / "sample_audio.mp3"
    # Create a minimal MP3 file
    audio_file.write_bytes(b"\xff\xfb" + b"\x00" * 1024)
    return audio_file


@pytest.fixture
def sample_image_file(tmp_path: Path) -> Path:
    """Create a sample image file for testing."""
    from PIL import Image
    
    image_file = tmp_path / "sample_image.jpg"
    # Create a simple test image
    img = Image.new("RGB", (100, 100), color="red")
    img.save(image_file, "JPEG")
    return image_file


@pytest.fixture
def mock_connection_manager() -> MagicMock:
    """Mock connection manager with semaphore."""
    manager = MagicMock()
    manager.client = AsyncMock()
    manager.semaphore = AsyncMock()
    manager.semaphore.__aenter__ = AsyncMock()
    manager.semaphore.__aexit__ = AsyncMock()
    return manager


@pytest.fixture
def sample_message() -> MagicMock:
    """Create a sample Telegram message with file."""
    message = MagicMock()
    message.id = 12345
    message.file = MagicMock()
    message.file.size = 10 * 1024 * 1024  # 10 MB
    message.file.name = "sample_video.mp4"
    message.file.mime_type = "video/mp4"
    return message


@pytest.fixture
def mock_progress_queue() -> AsyncMock:
    """Mock progress queue for download tracking."""
    queue = AsyncMock()
    queue.put = AsyncMock()
    return queue


@pytest.fixture
def mock_config() -> MagicMock:
    """Mock configuration object."""
    config = MagicMock()
    config.max_video_resolution = (1920, 1080)
    config.max_video_bitrate = 2000
    config.max_audio_bitrate = 128
    config.video_codec = "libx264"
    config.audio_codec = "aac"
    config.image_quality = 85
    config.image_max_size = (2048, 2048)
    config.enable_hardware_acceleration = False
    config.prefer_vp9 = False
    config.aggressive_compression = False
    return config
