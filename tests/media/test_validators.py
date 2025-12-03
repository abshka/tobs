"""
Unit tests for MediaValidator.

Tests media file validation for video, audio, and image files.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.media.validators import MediaValidator

pytestmark = pytest.mark.unit


class TestMediaValidator:
    """Tests for MediaValidator class."""

    @pytest.fixture
    def media_validator(self, io_executor):
        """Create MediaValidator instance for tests."""
        return MediaValidator(io_executor=io_executor)

    async def test_initialization(self, media_validator, io_executor):
        """Test MediaValidator initialization."""
        assert media_validator.io_executor == io_executor

    async def test_validate_file_integrity_nonexistent_file(
        self, media_validator, tmp_path
    ):
        """Test validation fails for nonexistent file."""
        nonexistent = tmp_path / "nonexistent.mp4"

        result = await media_validator.validate_file_integrity(nonexistent)

        assert result is False

    async def test_validate_file_integrity_empty_file(self, media_validator, tmp_path):
        """Test validation fails for empty file."""
        empty_file = tmp_path / "empty.mp4"
        empty_file.write_bytes(b"")

        result = await media_validator.validate_file_integrity(empty_file)

        assert result is False

    async def test_validate_file_integrity_too_small(self, media_validator, tmp_path):
        """Test validation fails for too small file."""
        small_file = tmp_path / "small.mp4"
        small_file.write_bytes(b"x" * 50)  # Less than 100 bytes

        result = await media_validator.validate_file_integrity(small_file)

        assert result is False

    async def test_validate_video_file_soft_valid_mp4(self, media_validator, tmp_path):
        """Test video validation succeeds for valid MP4."""
        video_file = tmp_path / "video.mp4"
        # Create file with valid MP4 signature
        video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1000)

        result = await media_validator.validate_file_integrity(video_file)

        assert result is True

    async def test_validate_video_file_soft_valid_mkv(self, media_validator, tmp_path):
        """Test video validation succeeds for valid MKV."""
        video_file = tmp_path / "video.mkv"
        # Create file with valid MKV signature
        video_file.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1000)

        result = await media_validator.validate_file_integrity(video_file)

        assert result is True

    async def test_validate_video_file_soft_valid_avi(self, media_validator, tmp_path):
        """Test video validation succeeds for valid AVI."""
        video_file = tmp_path / "video.avi"
        # Create file with valid AVI signature (RIFF)
        video_file.write_bytes(b"RIFF" + b"\x00" * 1000)

        result = await media_validator.validate_file_integrity(video_file)

        assert result is True

    async def test_validate_video_file_soft_invalid(self, media_validator, tmp_path):
        """Test video validation fails for invalid signature."""
        video_file = tmp_path / "video.mp4"
        # Create file without valid signature
        video_file.write_bytes(b"INVALID" + b"\x00" * 1000)

        result = await media_validator.validate_file_integrity(video_file)

        assert result is False

    async def test_validate_image_file_soft_valid_jpeg(self, media_validator, tmp_path):
        """Test image validation succeeds for valid JPEG."""
        from PIL import Image

        image_file = tmp_path / "image.jpg"
        # Create a real JPEG file using PIL
        img = Image.new("RGB", (100, 100), color="red")
        img.save(image_file, "JPEG")

        result = await media_validator.validate_file_integrity(image_file)

        assert result is True

    async def test_validate_image_file_soft_valid_png(self, media_validator, tmp_path):
        """Test image validation succeeds for valid PNG."""
        from PIL import Image

        image_file = tmp_path / "image.png"
        # Create a real PNG file using PIL
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(image_file, "PNG")

        result = await media_validator.validate_file_integrity(image_file)

        assert result is True

    async def test_validate_image_file_soft_invalid(self, media_validator, tmp_path):
        """Test image validation fails for corrupted image."""
        image_file = tmp_path / "image.jpg"
        # Create file with invalid JPEG data
        image_file.write_bytes(b"NOTJPEG" + b"\x00" * 1000)

        result = await media_validator.validate_file_integrity(image_file)

        assert result is False

    async def test_validate_audio_file_soft_valid_mp3(self, media_validator, tmp_path):
        """Test audio validation succeeds for valid MP3."""
        audio_file = tmp_path / "audio.mp3"
        # Create file with valid MP3 signature (ID3 tag or frame sync)
        audio_file.write_bytes(b"ID3" + b"\x00" * 1000)

        result = await media_validator.validate_file_integrity(audio_file)

        assert result is True

    async def test_validate_audio_file_soft_valid_m4a(self, media_validator, tmp_path):
        """Test audio validation succeeds for valid M4A (based on file size)."""
        audio_file = tmp_path / "audio.m4a"
        # M4A doesn't have signature in the list, but large files pass
        audio_file.write_bytes(b"\x00" * 15000)  # > 10KB fallback threshold

        result = await media_validator.validate_file_integrity(audio_file)

        # Should pass due to size fallback
        assert result is True

    async def test_validate_audio_file_soft_invalid(self, media_validator, tmp_path):
        """Test audio validation fails for invalid audio."""
        audio_file = tmp_path / "audio.mp3"
        # Create file without valid signature
        audio_file.write_bytes(b"NOTAUDIO" + b"\x00" * 1000)

        result = await media_validator.validate_file_integrity(audio_file)

        assert result is False

    async def test_validate_unknown_file_type(self, media_validator, tmp_path):
        """Test validation of unknown file type (basic size check)."""
        unknown_file = tmp_path / "file.xyz"
        unknown_file.write_bytes(b"x" * 1000)  # Large enough

        result = await media_validator.validate_file_integrity(unknown_file)

        # Should pass basic size check
        assert result is True

    async def test_validate_file_exception_handling(self, media_validator, tmp_path):
        """Test validation handles exceptions gracefully."""
        # Mock file operations to raise an exception
        bad_file = tmp_path / "bad.mp4"
        bad_file.write_bytes(b"x" * 1000)

        # Patch open to raise exception
        with patch("builtins.open", side_effect=OSError("Mock error")):
            result = await media_validator.validate_file_integrity(bad_file)

            # Should return False on exception
            assert result is False
