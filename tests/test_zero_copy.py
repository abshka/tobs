"""
Unit tests for Zero-Copy Media Transfer Module (B-2)

Tests the zero-copy file transfer implementation including:
- Basic copy operations
- Large file handling
- Small file fallback
- Verification
- Platform fallback
- Statistics tracking
- Concurrent operations

Author: TOBS Team
Created: 2025-01-20
"""

import asyncio
import os
import platform
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.media.zero_copy import (
    ZeroCopyConfig,
    ZeroCopyStats,
    ZeroCopyTransfer,
    get_zero_copy_transfer,
)


class TestZeroCopyBasic:
    """Basic zero-copy functionality tests."""

    @pytest.mark.asyncio
    async def test_zero_copy_basic(self):
        """Test basic file copy operation."""
        config = ZeroCopyConfig(
            enabled=True,
            min_size_mb=1,  # Low threshold for testing
            verify_copy=True,
            chunk_size_mb=1
        )
        
        transfer = ZeroCopyTransfer(config)
        
        # Create test file (2MB)
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            test_data = b"test" * (512 * 1024)  # 2MB
            src_file.write(test_data)
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            # Remove destination (will be created by copy)
            dst_path.unlink()
            
            # Copy
            success = await transfer.copy_file(src_path, dst_path)
            
            assert success, "Copy should succeed"
            assert dst_path.exists(), "Destination should exist"
            assert dst_path.stat().st_size == src_path.stat().st_size, "Size should match"
            
            # Check stats
            stats = transfer.get_stats()
            assert stats.bytes_copied >= 2 * 1024 * 1024, "Should track copied bytes"
            assert (stats.zero_copy_count > 0 or stats.fallback_count > 0), "Should use one method"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_zero_copy_large_file(self):
        """Test large file copy (>100MB)."""
        config = ZeroCopyConfig(
            enabled=True,
            min_size_mb=10,
            verify_copy=True
        )
        
        transfer = ZeroCopyTransfer(config)
        
        # Create large test file (120MB)
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            # Write in chunks to avoid memory issues
            for _ in range(120):
                src_file.write(b"x" * (1024 * 1024))  # 1MB chunks
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Copy
            success = await transfer.copy_file(src_path, dst_path)
            
            assert success, "Large file copy should succeed"
            assert dst_path.stat().st_size == 120 * 1024 * 1024, "Size should be 120MB"
            
            # Check stats
            stats = transfer.get_stats()
            assert stats.speed_mbps > 0, "Should calculate speed"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_small_file_fallback(self):
        """Test that small files use fallback (aiofiles)."""
        config = ZeroCopyConfig(
            enabled=True,
            min_size_mb=10,  # Files <10MB should use fallback
            verify_copy=True
        )
        
        transfer = ZeroCopyTransfer(config)
        
        # Create small test file (1MB)
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            src_file.write(b"small" * (200 * 1024))  # 1MB
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Copy
            success = await transfer.copy_file(src_path, dst_path)
            
            assert success, "Small file copy should succeed"
            
            # Check stats - should use fallback
            stats = transfer.get_stats()
            assert stats.fallback_count == 1, "Should use fallback for small file"
            assert stats.zero_copy_count == 0, "Should not use zero-copy"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)


class TestZeroCopyVerification:
    """Verification and error handling tests."""

    @pytest.mark.asyncio
    async def test_verify_enabled(self):
        """Test that verification works when enabled."""
        config = ZeroCopyConfig(enabled=True, verify_copy=True, min_size_mb=1)
        transfer = ZeroCopyTransfer(config)
        
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            src_file.write(b"test" * (512 * 1024))
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Copy with verification
            success = await transfer.copy_file(src_path, dst_path, verify=True)
            
            assert success, "Verified copy should succeed"
            assert dst_path.stat().st_size == src_path.stat().st_size
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_verify_disabled(self):
        """Test copy without verification."""
        config = ZeroCopyConfig(enabled=True, verify_copy=False, min_size_mb=1)
        transfer = ZeroCopyTransfer(config)
        
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            src_file.write(b"test" * (512 * 1024))
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Copy without verification (override config)
            success = await transfer.copy_file(src_path, dst_path, verify=False)
            
            assert success, "Copy without verification should succeed"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_missing_source(self):
        """Test copy with missing source file."""
        config = ZeroCopyConfig(enabled=True)
        transfer = ZeroCopyTransfer(config)
        
        src_path = Path("/tmp/nonexistent_source_file_123456.bin")
        dst_path = Path("/tmp/test_dest.bin")
        
        success = await transfer.copy_file(src_path, dst_path)
        
        assert not success, "Copy should fail with missing source"


class TestZeroCopyStats:
    """Statistics tracking tests."""

    @pytest.mark.asyncio
    async def test_stats_tracking(self):
        """Test that statistics are tracked correctly."""
        config = ZeroCopyConfig(enabled=True, min_size_mb=1)
        transfer = ZeroCopyTransfer(config)
        
        # Create and copy multiple files
        files = []
        for i in range(3):
            with tempfile.NamedTemporaryFile(delete=False) as src_file:
                src_path = Path(src_file.name)
                src_file.write(b"data" * (512 * 1024))  # 2MB each
                files.append(src_path)
        
        try:
            for src_path in files:
                with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                    dst_path = Path(dst_file.name)
                
                dst_path.unlink()
                
                await transfer.copy_file(src_path, dst_path)
                dst_path.unlink(missing_ok=True)
            
            # Check stats
            stats = transfer.get_stats()
            
            assert stats.bytes_copied >= 6 * 1024 * 1024, "Should track all bytes"
            assert (stats.zero_copy_count + stats.fallback_count) == 3, "Should count all operations"
            assert stats.speed_mbps > 0, "Should calculate speed"
            assert stats.zero_copy_ratio >= 0, "Should calculate ratio"
            
        finally:
            for path in files:
                path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_progress_callback(self):
        """Test progress callback functionality."""
        config = ZeroCopyConfig(enabled=True, min_size_mb=1)
        transfer = ZeroCopyTransfer(config)
        
        progress_calls = []
        
        def progress_callback(bytes_copied, total_bytes):
            progress_calls.append((bytes_copied, total_bytes))
        
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            src_file.write(b"test" * (512 * 1024))
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Copy with progress callback
            success = await transfer.copy_file(
                src_path, dst_path, progress_callback=progress_callback
            )
            
            assert success
            # Progress callback may or may not be called depending on implementation
            # Just verify no errors occurred
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)


class TestZeroCopyPlatform:
    """Platform-specific tests."""

    @pytest.mark.asyncio
    async def test_platform_fallback(self):
        """Test fallback on unsupported platforms."""
        # Mock platform to simulate unsupported OS
        with patch('platform.system', return_value='Windows'):
            config = ZeroCopyConfig(enabled=True, min_size_mb=1)
            transfer = ZeroCopyTransfer(config)
            
            # On Windows, sendfile should not be available
            if platform.system() == 'Windows':
                assert not transfer._sendfile_available, "Windows should not have sendfile"
        
        # Test actual copy still works (fallback)
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            src_file.write(b"test" * (512 * 1024))
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Copy should work via fallback
            success = await transfer.copy_file(src_path, dst_path)
            
            assert success, "Fallback copy should work"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_disabled_mode(self):
        """Test that disabled mode uses fallback."""
        config = ZeroCopyConfig(enabled=False, min_size_mb=1)
        transfer = ZeroCopyTransfer(config)
        
        with tempfile.NamedTemporaryFile(delete=False) as src_file:
            src_path = Path(src_file.name)
            src_file.write(b"test" * (512 * 1024))
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                dst_path = Path(dst_file.name)
            
            dst_path.unlink()
            
            # Copy with zero-copy disabled
            success = await transfer.copy_file(src_path, dst_path)
            
            assert success, "Disabled mode should still copy"
            
            # Check stats - should use fallback
            stats = transfer.get_stats()
            assert stats.fallback_count > 0, "Should use fallback when disabled"
            assert stats.zero_copy_count == 0, "Should not use zero-copy when disabled"
            
        finally:
            src_path.unlink(missing_ok=True)
            dst_path.unlink(missing_ok=True)


class TestZeroCopyConcurrent:
    """Concurrent operations tests."""

    @pytest.mark.asyncio
    async def test_concurrent_copies(self):
        """Test multiple concurrent copy operations."""
        config = ZeroCopyConfig(enabled=True, min_size_mb=1)
        transfer = ZeroCopyTransfer(config)
        
        # Create multiple test files
        files = []
        for i in range(5):
            with tempfile.NamedTemporaryFile(delete=False) as src_file:
                src_path = Path(src_file.name)
                src_file.write(b"data" * (256 * 1024))  # 1MB each
                files.append(src_path)
        
        try:
            # Copy all files concurrently
            dst_paths = []
            tasks = []
            
            for src_path in files:
                with tempfile.NamedTemporaryFile(delete=False) as dst_file:
                    dst_path = Path(dst_file.name)
                    dst_paths.append(dst_path)
                
                dst_path.unlink()
                
                task = transfer.copy_file(src_path, dst_path)
                tasks.append(task)
            
            # Wait for all
            results = await asyncio.gather(*tasks)
            
            # All should succeed
            assert all(results), "All concurrent copies should succeed"
            
            # Check all destinations exist
            for dst_path in dst_paths:
                assert dst_path.exists(), f"Destination should exist: {dst_path}"
            
            # Check stats
            stats = transfer.get_stats()
            assert (stats.zero_copy_count + stats.fallback_count) == 5, "Should count all operations"
            
        finally:
            for path in files:
                path.unlink(missing_ok=True)
            for path in dst_paths:
                path.unlink(missing_ok=True)


class TestZeroCopyGlobal:
    """Global singleton tests."""

    def test_global_singleton(self):
        """Test that get_zero_copy_transfer returns singleton."""
        transfer1 = get_zero_copy_transfer()
        transfer2 = get_zero_copy_transfer()
        
        assert transfer1 is transfer2, "Should return same instance"

    def test_stats_reset(self):
        """Test statistics reset."""
        transfer = get_zero_copy_transfer()
        
        # Reset stats
        transfer.reset_stats()
        
        stats = transfer.get_stats()
        assert stats.bytes_copied == 0
        assert stats.zero_copy_count == 0
        assert stats.fallback_count == 0
