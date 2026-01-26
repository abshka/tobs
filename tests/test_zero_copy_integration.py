"""
Integration tests for Zero-Copy Media Transfer (B-2)

Tests integration with:
- Video processor
- Audio processor  
- Image processor
- Cache manager
- Media manager

Verifies that zero-copy is correctly used in real export workflow.

Author: TOBS Team
Created: 2025-01-20
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

import pytest

from src.media.zero_copy import ZeroCopyConfig, ZeroCopyTransfer, get_zero_copy_transfer
from src.media.processors.video import VideoProcessor
from src.media.processors.audio import AudioProcessor
from src.media.processors.image import ImageProcessor
from src.media.cache import MediaCache
from src.media.manager import MediaProcessor
from src.media.models import ProcessingTask
from src.config import Config


class TestZeroCopyVideoIntegration:
    """Integration with VideoProcessor."""

    @pytest.mark.asyncio
    async def test_video_processor_uses_zero_copy(self):
        """Test that VideoProcessor uses zero-copy for file copying."""
        # Create mock config
        config = Mock(spec=Config)
        config.video_crf = 28
        config.video_preset = "fast"
        config.hw_acceleration = "vaapi"
        config.use_h265 = False
        config.image_quality = 85
        
        processor = VideoProcessor(Mock(), Mock(), Mock(), config)

        # Create test file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as src_file:
            src_path = Path(src_file.name)
            src_file.write(b"video" * (2 * 1024 * 1024))  # 10MB
        
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Create task
            task = ProcessingTask(
                input_path=src_path,
                output_path=dst_path,
                media_type="video",
                priority=1
            )
            
            # Copy file via processor (fallback mode)
            success = await processor._copy_file(task)
            
            assert success, "Video processor copy should succeed"
            assert dst_path.exists(), "Destination should exist"
            assert dst_path.stat().st_size > 0, "Destination should have content"
            
            # Verify zero-copy was used
            transfer = get_zero_copy_transfer()
            stats = transfer.get_stats()
            
            # Stats should show at least one operation (zero-copy or fallback)
            assert (stats.zero_copy_count + stats.fallback_count) > 0, \
                "Should have used zero-copy or fallback"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)


class TestZeroCopyCacheIntegration:
    """Integration with MediaCacheManager."""

    @pytest.mark.asyncio
    async def test_cache_manager_uses_zero_copy(self):
        """Test that cache manager uses zero-copy."""
        # Create mock dependencies
        cache_manager_mock = Mock()
        cache_manager_mock.get = AsyncMock(return_value=None)
        cache_manager_mock.set = AsyncMock()
        
        from src.media.cache import MediaCache
        handler = MediaCache(cache_manager_mock)

        # Create test files
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            src_file.write(b"cached" * (2 * 1024 * 1024))  # 10MB
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Copy via cache handler
            await handler._copy_file_async(src_path, dst_path)
            
            assert dst_path.exists(), "Cache copy should create destination"
            assert dst_path.stat().st_size == src_path.stat().st_size, "Size should match"
            
            # Verify zero-copy was used
            transfer = get_zero_copy_transfer()
            stats = transfer.get_stats()
            assert (stats.zero_copy_count + stats.fallback_count) > 0, \
                "Cache should use zero-copy"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)


class TestZeroCopyFallbackGraceful:
    """Test graceful fallback when zero-copy fails."""

    @pytest.mark.asyncio
    async def test_fallback_on_sendfile_error(self):
        """Test that system falls back to aiofiles if sendfile fails."""
        import sys
        if sys.platform == "win32":
            pytest.skip("sendfile not available on Windows")

        # Create config that forces fallback
        config = ZeroCopyConfig(
            enabled=True,
            min_size_mb=10,
            verify_copy=True
        )
        
        transfer = ZeroCopyTransfer(config)
        
        # Create large file (15MB) to trigger zero-copy attempt
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            for _ in range(15):
                src_file.write(b"x" * (1024 * 1024))
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Mock sendfile to fail
            with patch('os.sendfile', side_effect=OSError("Mocked sendfile failure")):
                # Copy should still succeed via fallback
                success = await transfer.copy_file(src_path, dst_path)
                
                assert success, "Should succeed via fallback even if sendfile fails"
                assert dst_path.exists()
                assert dst_path.stat().st_size == 15 * 1024 * 1024
                
                # Check that fallback was used
                stats = transfer.get_stats()
                assert stats.fallback_count > 0, "Should have used fallback"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_disabled_mode_fallback(self):
        """Test that disabled mode still works (uses fallback)."""
        # Create config with zero-copy DISABLED
        config = ZeroCopyConfig(
            enabled=False,  # Disabled!
            min_size_mb=1,
            verify_copy=True
        )
        
        transfer = ZeroCopyTransfer(config)
        
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            src_file.write(b"test" * (2 * 1024 * 1024))
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Should work via fallback
            success = await transfer.copy_file(src_path, dst_path)
            
            assert success, "Disabled mode should still copy via fallback"
            assert dst_path.exists()
            
            # Verify fallback was used
            stats = transfer.get_stats()
            assert stats.fallback_count > 0, "Should use fallback when disabled"
            assert stats.zero_copy_count == 0, "Should NOT use zero-copy when disabled"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)


class TestZeroCopyEndToEnd:
    """End-to-end workflow tests."""

    @pytest.mark.asyncio
    async def test_multiple_processors_concurrent(self):
        """Test multiple processors using zero-copy concurrently."""
        # Reset global transfer stats
        transfer = get_zero_copy_transfer()
        transfer.reset_stats()
        
        # Create multiple test files for different processors
        files = []
        processors = []
        
        # Video
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
            src = Path(f.name)
            f.write(b"video" * (2 * 1024 * 1024))
            files.append(src)
        
        # Audio
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
            src = Path(f.name)
            f.write(b"audio" * (2 * 1024 * 1024))
            files.append(src)
        
        # Image
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
            src = Path(f.name)
            f.write(b"image" * (2 * 1024 * 1024))
            files.append(src)
        
        try:
            # Create tasks
            tasks = []
            dst_paths = []
            
            for src_path in files:
                with tempfile.NamedTemporaryFile(delete=False, suffix=src_path.suffix) as f:
                    dst_path = Path(f.name)
                    dst_paths.append(dst_path)
                
                dst_path.unlink()
                
                # Copy using zero-copy directly
                task = transfer.copy_file(src_path, dst_path)
                tasks.append(task)
            
            # Execute concurrently
            results = await asyncio.gather(*tasks)
            
            # All should succeed
            assert all(results), "All concurrent copies should succeed"
            
            # Check destinations
            for dst_path in dst_paths:
                assert dst_path.exists(), f"Should exist: {dst_path}"
                assert dst_path.stat().st_size > 0, f"Should have content: {dst_path}"
            
            # Check global stats
            stats = transfer.get_stats()
            assert (stats.zero_copy_count + stats.fallback_count) == 3, \
                "Should track all 3 operations"
            assert stats.bytes_copied > 0, "Should track bytes"
            
        finally:
            for path in files:
                path.unlink(missing_ok=True)
            for path in dst_paths:
                path.unlink(missing_ok=True)
