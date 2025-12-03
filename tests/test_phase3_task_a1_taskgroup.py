"""
Tests for Phase 3 Task A.1 - TaskGroup Migration in CoreSystemManager.

Tests verify:
1. TaskGroup creation and lifecycle
2. Error handling and resilience
3. Graceful shutdown behavior

Consolidated from 40+ tests to ~12 essential tests.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.core_manager import CoreSystemManager


class TestTaskGroupLifecycle:
    """Test TaskGroup initialization and lifecycle management."""

    @pytest.mark.asyncio
    async def test_taskgroup_runner_created_on_init(self):
        """Verify TaskGroup runner is created during initialization."""
        manager = CoreSystemManager()

        assert manager._task_group_runner is None

        result = await manager.initialize()

        assert result is True
        assert manager._task_group_runner is not None
        assert isinstance(manager._task_group_runner, asyncio.Task)
        assert not manager._task_group_runner.done()

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_taskgroup_context_active_after_init(self):
        """Verify TaskGroup context is active after initialization."""
        manager = CoreSystemManager()
        await manager.initialize()

        # Give TaskGroup time to be created
        await asyncio.sleep(0.1)

        assert manager._task_group is not None

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_taskgroup_cleaned_up_on_shutdown(self):
        """Verify TaskGroup is properly cleaned up on shutdown."""
        manager = CoreSystemManager()
        await manager.initialize()

        await asyncio.sleep(0.05)
        assert manager._task_group is not None

        await manager.shutdown()

        # TaskGroup should be None after shutdown
        assert manager._task_group is None
        assert manager._task_group_runner.done()


class TestTaskGroupErrorHandling:
    """Test error handling within TaskGroup."""

    @pytest.mark.asyncio
    async def test_health_check_error_tracked(self):
        """Verify errors in health check are tracked but don't crash system."""
        manager = CoreSystemManager()
        await manager.initialize()

        initial_failed = manager._failed_operations

        # Inject error into health check
        with patch.object(
            manager, "_perform_health_check", side_effect=RuntimeError("Test error")
        ):
            await asyncio.sleep(0.2)

        # Error should be tracked
        assert manager._failed_operations > initial_failed

        # System should still be running
        assert manager._initialized is True
        assert not manager._shutdown

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_multiple_errors_dont_stop_taskgroup(self):
        """Verify TaskGroup continues despite multiple errors."""
        manager = CoreSystemManager()

        error_count = 0

        async def always_fails():
            nonlocal error_count
            error_count += 1
            raise RuntimeError(f"Error {error_count}")

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.return_value = None

            with patch.object(
                manager, "_perform_health_check", side_effect=always_fails
            ):
                await manager.initialize()
                await asyncio.sleep(0.2)

        # Multiple errors should have occurred
        assert manager._failed_operations > 0

        # System should still be operational
        assert manager._task_group_runner is not None

        await manager.shutdown()


class TestTaskGroupShutdown:
    """Test graceful shutdown of TaskGroup."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_runner(self):
        """Verify shutdown cancels the TaskGroup runner task."""
        manager = CoreSystemManager()
        await manager.initialize()

        runner = manager._task_group_runner
        assert not runner.done()

        await manager.shutdown()

        assert runner.done()
        assert manager._shutdown is True

    @pytest.mark.asyncio
    async def test_multiple_shutdown_calls_safe(self):
        """Verify calling shutdown multiple times is safe."""
        manager = CoreSystemManager()
        await manager.initialize()

        await manager.shutdown()
        assert manager._shutdown is True

        # Second call should not raise
        await manager.shutdown()
        assert manager._shutdown is True

    @pytest.mark.asyncio
    async def test_shutdown_with_pending_errors(self):
        """Verify shutdown works even with pending errors."""
        manager = CoreSystemManager()
        await manager.initialize()

        # Inject error
        with patch.object(
            manager,
            "_perform_health_check",
            side_effect=RuntimeError("Error during shutdown"),
        ):
            await asyncio.sleep(0.1)

        # Shutdown should still complete successfully
        await manager.shutdown()

        assert manager._shutdown is True
        assert not manager._initialized


class TestBackwardCompatibility:
    """Test backward compatibility after TaskGroup migration."""

    @pytest.mark.asyncio
    async def test_initialize_returns_bool(self):
        """Verify initialize() still returns boolean as expected."""
        manager = CoreSystemManager()

        result = await manager.initialize()

        assert isinstance(result, bool)
        assert result is True

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_status_methods_work(self):
        """Verify status methods still work after TaskGroup migration."""
        manager = CoreSystemManager()
        await manager.initialize()

        status = manager.get_comprehensive_status()
        assert isinstance(status, dict)

        uptime = manager.get_uptime()
        assert isinstance(uptime, float)
        assert uptime >= 0

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_performance_profiles_work(self):
        """Verify TaskGroup works with different performance profiles."""
        profiles = ["performance", "balanced", "power_saving"]

        for profile in profiles:
            manager = CoreSystemManager(performance_profile=profile)
            result = await manager.initialize()

            assert result is True
            assert manager.performance_profile == profile
            assert manager._task_group_runner is not None

            await manager.shutdown()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
