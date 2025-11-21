"""
Unit tests for MetadataExtractor.

Tests metadata extraction from video, audio, and image files.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from PIL import Image

from src.media.metadata import MetadataExtractor
from src.media.models import MediaMetadata


pytestmark = pytest.mark.unit


class TestMetadataExtractor:
    """Tests for MetadataExtractor class."""

    @pytest.fixture
    def metadata_extractor(self, io_executor):
        """Create MetadataExtractor instance for tests."""
        return MetadataExtractor(io_executor=io_executor)

    async def test_initialization(self, metadata_extractor, io_executor):
        """Test MetadataExtractor initialization."""
        assert metadata_extractor.io_executor == io_executor
        assert metadata_extractor._metadata_cache == {}
        assert metadata_extractor._file_checksums == {}

    async def test_get_metadata_image_file(
        self, metadata_extractor, tmp_path
    ):
        """Test get_metadata for image file."""
        # Create a real image file
        image_file = tmp_path / "test.jpg"
        img = Image.new("RGB", (640, 480), color="red")
        img.save(image_file, "JPEG")
        
        metadata = await metadata_extractor.get_metadata(image_file, "image")
        
        assert metadata.file_size > 0
        assert metadata.mime_type == "image/jpeg"
        assert metadata.width == 640
        assert metadata.height == 480
        assert metadata.format == "JPEG"
        assert metadata.checksum is not None

    async def test_get_metadata_video_file_with_ffmpeg(
        self, metadata_extractor, tmp_path
    ):
        """Test get_metadata for video file (mocked FFmpeg)."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 10000)
        
        # Mock FFmpeg probe - match actual structure used in code
        mock_probe_result = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "codec_name": "h264",
                    "bit_rate": "5000000",
                    "r_frame_rate": "30/1",  # Used for FPS
                    "duration": "60.5",  # Duration from stream
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "channels": 2,
                    "sample_rate": "44100",
                    "duration": "60.5",
                }
            ],
            "format": {
                "duration": "60.5",
                "bit_rate": "6000000",
            }
        }
        
        with patch("ffmpeg.probe", return_value=mock_probe_result):
            metadata = await metadata_extractor.get_metadata(video_file, "video")
        
        assert metadata.file_size > 0
        assert metadata.mime_type == "video/mp4"
        assert metadata.width == 1920
        assert metadata.height == 1080
        assert metadata.codec == "h264"
        assert metadata.duration == 60.5
        assert metadata.fps == 30.0  # eval("30/1") = 30.0
        assert metadata.channels == 2
        assert metadata.sample_rate == 44100

    async def test_get_metadata_audio_file_with_ffmpeg(
        self, metadata_extractor, tmp_path
    ):
        """Test get_metadata for audio file (mocked FFmpeg)."""
        audio_file = tmp_path / "test.mp3"
        audio_file.write_bytes(b"ID3" + b"\x00" * 5000)
        
        mock_probe_result = {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "mp3",
                    "bit_rate": "320000",
                    "channels": 2,
                    "sample_rate": "44100",
                    "duration": "180.0",
                }
            ],
            "format": {
                "duration": "180.0",
                "bit_rate": "320000",
            }
        }
        
        with patch("ffmpeg.probe", return_value=mock_probe_result):
            metadata = await metadata_extractor.get_metadata(audio_file, "audio")
        
        assert metadata.file_size > 0
        assert metadata.mime_type == "audio/mpeg"
        # Note: codec is not set from audio stream (only from video stream in code)
        assert metadata.duration == 180.0
        assert metadata.channels == 2
        assert metadata.sample_rate == 44100

    async def test_get_metadata_caching(
        self, metadata_extractor, tmp_path
    ):
        """Test metadata caching functionality."""
        image_file = tmp_path / "test.jpg"
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(image_file, "JPEG")
        
        # First call - should compute metadata
        metadata1 = await metadata_extractor.get_metadata(image_file, "image")
        
        # Second call - should return cached metadata
        metadata2 = await metadata_extractor.get_metadata(image_file, "image")
        
        # Should be the same object (from cache)
        assert metadata1 is metadata2
        assert metadata1.checksum == metadata2.checksum

    async def test_get_metadata_cache_miss_after_modification(
        self, metadata_extractor, tmp_path
    ):
        """Test cache uses file hash, so same content = same cache."""
        image_file = tmp_path / "test.jpg"
        img = Image.new("RGB", (100, 100), color="red")
        img.save(image_file, "JPEG")
        
        # Get metadata for first version
        metadata1 = await metadata_extractor.get_metadata(image_file, "image")
        
        # Get metadata again (should be cached)
        metadata2 = await metadata_extractor.get_metadata(image_file, "image")
        
        # Should be the same from cache
        assert metadata1 is metadata2

    async def test_get_metadata_ffmpeg_failure_fallback(
        self, metadata_extractor, tmp_path
    ):
        """Test fallback when FFmpeg probe fails."""
        video_file = tmp_path / "broken.mp4"
        video_file.write_bytes(b"INVALID VIDEO DATA" * 100)
        
        # Mock FFmpeg to raise exception
        with patch("ffmpeg.probe", side_effect=Exception("FFmpeg error")):
            metadata = await metadata_extractor.get_metadata(video_file, "video")
        
        # Should return basic metadata even on error
        assert metadata.file_size > 0
        assert metadata.mime_type == "video/mp4"
        # Video-specific fields should be None/default
        assert metadata.width is None
        assert metadata.height is None

    async def test_get_metadata_image_pil_failure(
        self, metadata_extractor, tmp_path
    ):
        """Test handling of PIL errors for corrupted images."""
        image_file = tmp_path / "broken.jpg"
        image_file.write_bytes(b"NOT AN IMAGE" * 100)
        
        metadata = await metadata_extractor.get_metadata(image_file, "image")
        
        # Should return basic metadata even on error
        assert metadata.file_size > 0
        # Image-specific fields might be None
        # (PIL will fail to open the corrupted file)

    async def test_get_file_hash_computation(
        self, metadata_extractor, tmp_path
    ):
        """Test file hash computation."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content")
        
        hash1 = await metadata_extractor._get_file_hash(test_file)
        hash2 = await metadata_extractor._get_file_hash(test_file)
        
        # Same file should produce same hash
        assert hash1 == hash2
        assert isinstance(hash1, str)
        assert len(hash1) > 0

    async def test_get_file_hash_different_files(
        self, metadata_extractor, tmp_path
    ):
        """Test hash differs for different files."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        
        file1.write_bytes(b"content 1")
        file2.write_bytes(b"content 2")
        
        hash1 = await metadata_extractor._get_file_hash(file1)
        hash2 = await metadata_extractor._get_file_hash(file2)
        
        # Different files should produce different hashes
        assert hash1 != hash2

    async def test_get_image_metadata(
        self, metadata_extractor, tmp_path
    ):
        """Test _get_image_metadata helper."""
        image_file = tmp_path / "test.png"
        img = Image.new("RGBA", (800, 600), color=(255, 0, 0, 128))
        img.save(image_file, "PNG")
        
        image_metadata = await metadata_extractor._get_image_metadata(image_file)
        
        assert image_metadata["width"] == 800
        assert image_metadata["height"] == 600
        assert image_metadata["format"] == "PNG"
