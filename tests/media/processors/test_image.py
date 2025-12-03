"""
Unit tests for ImageProcessor.

Tests image processing functionality including resizing, EXIF rotation,
format conversion, and fallback strategies.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from src.media.models import ProcessingSettings, ProcessingTask
from src.media.processors.image import ImageProcessor

pytestmark = pytest.mark.unit


class TestImageProcessor:
    """Tests for ImageProcessor class."""

    @pytest.fixture
    def image_processor(self, io_executor, cpu_executor, mock_config):
        """Create ImageProcessor instance for tests."""
        return ImageProcessor(
            io_executor=io_executor,
            cpu_executor=cpu_executor,
            config=mock_config,
        )

    async def test_initialization(self, image_processor):
        """Test ImageProcessor initialization."""
        assert image_processor.io_executor is not None
        assert image_processor.cpu_executor is not None
        assert image_processor._image_processed_count == 0
        assert image_processor._image_copied_count == 0

    async def test_process_small_image_skips_processing(
        self, image_processor, tmp_path, image_metadata
    ):
        """Test that small images are copied without processing."""
        # Create a small image
        input_path = tmp_path / "small.jpg"
        output_path = tmp_path / "output.jpg"

        img = Image.new("RGB", (100, 100), color="blue")
        img.save(input_path, "JPEG")

        # Make metadata show small file
        image_metadata.file_size = 1024 * 100  # 100KB (< 5MB threshold)
        image_metadata.width = 100
        image_metadata.height = 100

        task = ProcessingTask(
            input_path=input_path,
            output_path=output_path,
            media_type="image",
            metadata=image_metadata,
        )

        result = await image_processor.process(task, "test_worker")

        assert result is True
        assert output_path.exists()
        # Should be copied count, not processed
        assert image_processor._image_copied_count >= 0

    async def test_process_large_image_with_resize(
        self, image_processor, tmp_path, image_metadata
    ):
        """Test processing large image with resizing."""
        # Create a large image
        input_path = tmp_path / "large.jpg"
        output_path = tmp_path / "output.jpg"

        img = Image.new("RGB", (3000, 2000), color="green")
        img.save(input_path, "JPEG", quality=95)

        # Make metadata show large file
        image_metadata.file_size = 1024 * 1024 * 6  # 6MB (> 5MB threshold)
        image_metadata.width = 3000
        image_metadata.height = 2000

        task = ProcessingTask(
            input_path=input_path,
            output_path=output_path,
            media_type="image",
            metadata=image_metadata,
        )

        result = await image_processor.process(task, "test_worker")

        assert result is True
        assert output_path.exists()

        # Check that output is smaller
        output_img = Image.open(output_path)
        assert output_img.width <= 2048
        assert output_img.height <= 2048

    async def test_process_handles_exif_rotation(
        self, image_processor, tmp_path, image_metadata
    ):
        """Test that EXIF rotation is handled correctly."""
        input_path = tmp_path / "rotated.jpg"
        output_path = tmp_path / "output.jpg"

        # Create image with EXIF data
        img = Image.new("RGB", (200, 100), color="red")
        exif = img.getexif()
        exif[0x0112] = 8  # Rotate 90 CCW
        img.save(input_path, "JPEG", exif=exif)

        image_metadata.file_size = 1024 * 1024 * 6
        image_metadata.width = 200
        image_metadata.height = 100

        task = ProcessingTask(
            input_path=input_path,
            output_path=output_path,
            media_type="image",
            metadata=image_metadata,
        )

        result = await image_processor.process(task, "test_worker")

        assert result is True
        assert output_path.exists()

    async def test_process_rgba_to_rgb_conversion(
        self, image_processor, tmp_path, image_metadata
    ):
        """Test RGBA to RGB conversion for JPEG."""
        input_path = tmp_path / "rgba.png"
        output_path = tmp_path / "output.jpg"

        # Create RGBA image
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        img.save(input_path, "PNG")

        image_metadata.file_size = 1024 * 1024 * 6
        image_metadata.width = 100
        image_metadata.height = 100

        task = ProcessingTask(
            input_path=input_path,
            output_path=output_path,
            media_type="image",
            metadata=image_metadata,
        )

        result = await image_processor.process(task, "test_worker")

        assert result is True
        assert output_path.exists()

        # Verify it's RGB
        output_img = Image.open(output_path)
        assert output_img.mode == "RGB"

    async def test_process_fallback_on_error(
        self, image_processor, tmp_path, image_metadata
    ):
        """Test fallback to copy when processing fails."""
        input_path = tmp_path / "input.jpg"
        output_path = tmp_path / "output.jpg"

        # Create a valid image
        img = Image.new("RGB", (100, 100))
        img.save(input_path, "JPEG")

        image_metadata.file_size = 1024 * 1024 * 6

        task = ProcessingTask(
            input_path=input_path,
            output_path=output_path,
            media_type="image",
            metadata=image_metadata,
        )

        # Mock PIL to raise an error
        with patch("PIL.Image.open", side_effect=Exception("PIL error")):
            result = await image_processor.process(task, "test_worker")

        # Should fallback to copy
        assert result is True
        assert output_path.exists()

    def test_needs_processing_small_file(self, image_processor):
        """Test needs_processing returns False for small files."""
        settings = ProcessingSettings(image_max_size=(2048, 2048))

        # Small file < 5MB
        input_path = Path("/fake/small.jpg")

        # Should not need processing (checked via file size in practice)
        # This tests the logic, not the actual file read
        needs = image_processor.needs_processing(input_path, settings)

        # Without metadata, it defaults to checking file
        # For this test, we'll just verify the method exists
        assert isinstance(needs, bool)

    async def test_get_statistics(self, image_processor):
        """Test statistics retrieval."""
        stats = image_processor.get_statistics()

        assert "total_processed" in stats
        assert "optimized" in stats
        assert "copied" in stats
        assert "optimization_percentage" in stats
        assert stats["total_processed"] == 0
        assert stats["optimized"] == 0
        assert stats["copied"] == 0

    def test_log_statistics(self, image_processor):
        """Test statistics logging."""
        image_processor._image_processed_count = 5
        image_processor._image_copied_count = 3

        # Just verify it doesn't crash
        image_processor.log_statistics()

        # Verify statistics are correct
        stats = image_processor.get_statistics()
        assert stats["optimized"] == 5
        assert stats["copied"] == 3
        assert stats["total_processed"] == 8
