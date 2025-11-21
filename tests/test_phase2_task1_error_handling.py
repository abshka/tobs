"""
Tests for Phase 2 Task 1: Background Task Error Handling

This module tests the error handling in CoreSystemManager's background tasks,
specifically focusing on TaskGroup-based error aggregation and health check resilience.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.core_manager import CoreSystemManager

logger = logging.getLogger(__name__)


class TestCoreSystemManagerInitialization:
    """Test CoreSystemManager initialization and basic lifecycle."""

    @pytest.mark.asyncio
    async def test_manager_initializes_successfully(self):
        """Test that CoreSystemManager initializes without errors."""
        manager = CoreSystemManager()

        result = await manager.initialize()

        assert result is True
        assert manager._initialized is True
        assert manager._shutdown is False

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_manager_creates_background_task_runner(self):
        """Test that background task runner is created on initialization."""
        manager = CoreSystemManager()

        await manager.initialize()

        assert manager._task_group_runner is not None
        assert isinstance(manager._task_group_runner, asyncio.Task)
        assert not manager._task_group_runner.done()

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_double_initialization_is_safe(self):
        """Test that calling initialize() twice is safe."""
        manager = CoreSystemManager()

        result1 = await manager.initialize()
        result2 = await manager.initialize()

        assert result1 is True
        assert result2 is True

        await manager.shutdown()


class TestHealthCheckErrorResilience:
    """Test that health check loop handles errors gracefully."""

    @pytest.mark.asyncio
    async def test_health_check_error_doesnt_crash_system(self, caplog):
        """Test that errors in health check don't crash the system."""
        caplog.set_level(logging.ERROR)

        manager = CoreSystemManager()
        await manager.initialize()

        initial_failed = manager._failed_operations

        # Inject error into health check
        with patch.object(
            manager,
            "_perform_health_check",
            side_effect=RuntimeError("Test health check error"),
        ):
            # Wait for health check to run
            await asyncio.sleep(0.2)

        # System should still be running
        assert manager._initialized is True
        assert not manager._shutdown

        # Failed operations should be incremented
        assert manager._failed_operations > initial_failed

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_health_check_continues_after_error(self, caplog):
        """Test that health check loop continues after encountering an error."""
        caplog.set_level(logging.ERROR)

        manager = CoreSystemManager()
        await manager.initialize()

        call_count = 0

        async def mock_perform_check():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("First check fails")
            # Subsequent checks succeed

        # Mock sleep to make loop run faster
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.return_value = None

            with patch.object(
                manager, "_perform_health_check", side_effect=mock_perform_check
            ):
                # Let multiple iterations run
                await asyncio.sleep(0.15)

        # System should still be operational
        assert manager._initialized is True
        assert manager._task_group_runner is not None

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_errors_dont_crash_system(self, caplog):
        """Test that multiple sequential errors don't crash the system."""
        caplog.set_level(logging.ERROR)

        manager = CoreSystemManager()
        await manager.initialize()

        error_count = 0

        async def always_fails():
            nonlocal error_count
            error_count += 1
            raise RuntimeError(f"Error #{error_count}")

        # Mock sleep to make loop run fast
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.return_value = None

            with patch.object(
                manager, "_perform_health_check", side_effect=always_fails
            ):
                # Wait for multiple error iterations
                await asyncio.sleep(0.2)

        # System should still be running despite multiple errors
        assert manager._initialized is True
        assert manager._failed_operations > 0

        await manager.shutdown()


class TestShutdownBehavior:
    """Test proper shutdown and cleanup behavior."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_background_tasks(self):
        """Test that shutdown properly cancels background task runner."""
        manager = CoreSystemManager()
        await manager.initialize()

        runner = manager._task_group_runner
        assert runner is not None
        assert not runner.done()

        await manager.shutdown()

        # Runner should be done after shutdown
        assert runner.done()
        assert manager._shutdown is True
        assert manager._initialized is False

    @pytest.mark.asyncio
    async def test_multiple_shutdown_calls_are_safe(self):
        """Test that calling shutdown() multiple times is safe."""
        manager = CoreSystemManager()
        await manager.initialize()

        # First shutdown
        await manager.shutdown()
        assert manager._shutdown is True

        # Second shutdown should not raise
        await manager.shutdown()
        assert manager._shutdown is True

    @pytest.mark.asyncio
    async def test_shutdown_with_pending_errors(self, caplog):
        """Test that shutdown works even when errors are pending."""
        caplog.set_level(logging.ERROR)

        manager = CoreSystemManager()
        await manager.initialize()

        # Inject error
        with patch.object(
            manager,
            "_perform_health_check",
            side_effect=RuntimeError("Error during shutdown"),
        ):
            await asyncio.sleep(0.1)

        # Shutdown should still work
        await manager.shutdown()

        assert manager._shutdown is True
        assert not manager._initialized


class TestStatisticsTracking:
    """Test error statistics tracking."""

    @pytest.mark.asyncio
    async def test_failed_operations_counter_increments(self):
        """Test that failed operations counter increments on errors."""
        manager = CoreSystemManager()

        initial_count = manager._failed_operations
        assert initial_count == 0

        await manager.initialize()

        # Inject error
        with patch.object(
            manager, "_perform_health_check", side_effect=RuntimeError("Test error")
        ):
            await asyncio.sleep(0.2)

        # Counter should have incremented
        assert manager._failed_operations > initial_count

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_statistics_available_after_errors(self):
        """Test that statistics methods work after errors occur."""
        manager = CoreSystemManager()
        await manager.initialize()

        # Cause an error
        with patch.object(
            manager, "_perform_health_check", side_effect=RuntimeError("Test error")
        ):
            await asyncio.sleep(0.1)

        # Statistics should still be accessible
        status = manager.get_comprehensive_status()
        assert isinstance(status, dict)
        assert "failed_operations" in status

        uptime = manager.get_uptime()
        assert isinstance(uptime, float)
        assert uptime >= 0

        await manager.shutdown()


class TestHealthCheckFunctionality:
    """Test actual health check functionality."""

    @pytest.mark.asyncio
    async def test_health_check_validates_components(self, caplog):
        """Test that health check validates all core components."""
        caplog.set_level(logging.DEBUG)

        manager = CoreSystemManager()
        await manager.initialize()

        # Let at least one health check run
        await asyncio.sleep(0.5)

        # Health check should have logged
        assert any(
            "health check" in record.message.lower() for record in caplog.records
        )

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_health_check_detects_component_issues(self, caplog):
        """Test that health check detects when components have issues."""
        caplog.set_level(logging.WARNING)

        manager = CoreSystemManager()
        await manager.initialize()

        # Break cache manager temporarily
        if manager._cache_manager:
            original_get = manager._cache_manager.get
            manager._cache_manager.get = AsyncMock(
                side_effect=RuntimeError("Cache broken")
            )

            # Wait for health check to detect the issue
            await asyncio.sleep(0.5)

            # Restore
            manager._cache_manager.get = original_get

        # Should have logged the issue
        assert any(
            "health check" in record.message.lower()
            for record in caplog.records
            if record.levelname == "WARNING"
        )

        await manager.shutdown()
