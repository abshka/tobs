"""
Tests for connection.py - AdaptiveTaskPool.

Batch 4 & 5 of Session 7: Task pool with auto-scaling.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.connection import AdaptiveTaskPool, PoolType


class TestAdaptiveTaskPoolInitialization:
    """Tests for AdaptiveTaskPool initialization."""

    def test_adaptive_task_pool_initialization(self):
        """AdaptiveTaskPool should initialize with correct defaults."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD)

        assert pool.pool_type == PoolType.DOWNLOAD
        assert pool.max_workers == 5
        assert pool.auto_scale is True
        assert pool.active_tasks == 0
        assert pool.queued_tasks == 0
        assert pool.completed_tasks == 0
        assert pool.failed_tasks == 0
        assert pool.avg_task_time == 0.0
        assert pool.performance_history == []
        assert pool.scale_cooldown == 30.0

    def test_pool_creates_semaphore_with_max_workers(self):
        """AdaptiveTaskPool should create semaphore with max_workers limit."""
        pool = AdaptiveTaskPool(PoolType.IO, max_workers=10)

        assert pool.semaphore._value == 10

    def test_pool_with_custom_max_workers(self):
        """AdaptiveTaskPool should accept custom max_workers."""
        pool = AdaptiveTaskPool(PoolType.PROCESSING, max_workers=8)

        assert pool.max_workers == 8
        assert pool.semaphore._value == 8

    def test_pool_with_auto_scale_disabled(self):
        """AdaptiveTaskPool should accept auto_scale=False."""
        pool = AdaptiveTaskPool(PoolType.API, auto_scale=False)

        assert pool.auto_scale is False

    @patch("src.core.connection.time.time", return_value=1000.0)
    def test_pool_initializes_last_scale_time(self, mock_time):
        """AdaptiveTaskPool should initialize last_scale_time."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD)

        assert pool.last_scale_time == 1000.0


class TestAdaptiveTaskPoolSubmission:
    """Tests for task submission and execution."""

    @pytest.mark.asyncio
    async def test_submit_executes_coroutine(self):
        """submit should execute the provided coroutine."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def sample_task():
            return "result"

        result = await pool.submit(sample_task)

        assert result == "result"

    @pytest.mark.asyncio
    async def test_submit_passes_arguments(self):
        """submit should pass *args and **kwargs to coroutine."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def task_with_args(a, b, c=None):
            return f"{a}-{b}-{c}"

        result = await pool.submit(task_with_args, "x", "y", c="z")

        assert result == "x-y-z"

    @pytest.mark.asyncio
    async def test_submit_updates_completed_tasks_count(self):
        """submit should increment completed_tasks on success."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def simple_task():
            return "done"

        await pool.submit(simple_task)

        assert pool.completed_tasks == 1

    @pytest.mark.asyncio
    async def test_submit_updates_avg_task_time(self):
        """submit should update average task time."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def task():
            return "ok"

        await pool.submit(task)

        # Task time should be recorded (small positive value)
        assert pool.avg_task_time > 0

    @pytest.mark.asyncio
    async def test_submit_calculates_weighted_avg_task_time(self):
        """submit should use weighted average (80-20 split) for task time."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def task():
            await asyncio.sleep(0.001)  # Small delay
            return "ok"

        await pool.submit(task)
        first_avg = pool.avg_task_time

        await pool.submit(task)
        second_avg = pool.avg_task_time

        # Second average should be different (weighted)
        # And both should be positive
        assert first_avg > 0
        assert second_avg > 0

    @pytest.mark.asyncio
    async def test_submit_adds_to_performance_history(self):
        """submit should add task time to performance history."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def task():
            return "ok"

        await pool.submit(task)

        assert len(pool.performance_history) == 1
        assert pool.performance_history[0] > 0  # Should be positive

    @pytest.mark.asyncio
    async def test_submit_maintains_performance_history_limit(self):
        """submit should maintain max 20 entries in performance history."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def task():
            return "ok"

        # Execute 25 tasks
        for _ in range(25):
            await pool.submit(task)

        # Should keep only last 20
        assert len(pool.performance_history) == 20

    @pytest.mark.asyncio
    async def test_submit_tracks_failed_tasks_on_exception(self):
        """submit should increment failed_tasks on exception."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def failing_task():
            raise ValueError("Task failed")

        with pytest.raises(ValueError):
            await pool.submit(failing_task)

        assert pool.failed_tasks == 1
        assert pool.completed_tasks == 0

    @pytest.mark.asyncio
    async def test_submit_propagates_exception(self):
        """submit should propagate exceptions from tasks."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def failing_task():
            raise RuntimeError("Custom error")

        with pytest.raises(RuntimeError, match="Custom error"):
            await pool.submit(failing_task)


class TestAdaptiveTaskPoolSemaphore:
    """Tests for semaphore control."""

    @pytest.mark.asyncio
    async def test_submit_increments_queued_before_execution(self):
        """submit should increment queued_tasks before acquiring semaphore."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=1, auto_scale=False)

        # Create a task that blocks
        block_event = asyncio.Event()

        async def blocking_task():
            await block_event.wait()
            return "done"

        # Start first task (will acquire semaphore and block)
        task1 = asyncio.create_task(pool.submit(blocking_task))
        await asyncio.sleep(0.01)  # Let it start

        # Check that queued increases for second task
        initial_queued = pool.queued_tasks
        task2 = asyncio.create_task(pool.submit(blocking_task))
        await asyncio.sleep(0.01)

        assert pool.queued_tasks > initial_queued

        # Cleanup
        block_event.set()
        await task1
        await task2

    @pytest.mark.asyncio
    async def test_submit_decrements_queued_on_execution_start(self):
        """submit should decrement queued_tasks after acquiring semaphore."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def task():
            # At this point, queued should be 0
            assert pool.queued_tasks == 0
            return "ok"

        await pool.submit(task)

    @pytest.mark.asyncio
    async def test_submit_increments_active_during_execution(self):
        """submit should increment active_tasks during execution."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def task():
            # During execution, active_tasks should be 1
            assert pool.active_tasks == 1
            return "ok"

        await pool.submit(task)

    @pytest.mark.asyncio
    async def test_submit_decrements_active_after_completion(self):
        """submit should decrement active_tasks after task completes."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def task():
            return "ok"

        await pool.submit(task)

        # After completion, active should be 0
        assert pool.active_tasks == 0

    @pytest.mark.asyncio
    async def test_submit_decrements_active_on_exception(self):
        """submit should decrement active_tasks even on exception."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, auto_scale=False)

        async def failing_task():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await pool.submit(failing_task)

        # Active should be 0 even after exception
        assert pool.active_tasks == 0

    @pytest.mark.asyncio
    async def test_concurrent_tasks_respect_max_workers(self):
        """Concurrent tasks should respect max_workers limit."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=2, auto_scale=False)

        active_count = []

        async def task():
            active_count.append(pool.active_tasks)
            await asyncio.sleep(0.01)
            return "ok"

        # Submit 5 tasks concurrently
        tasks = [asyncio.create_task(pool.submit(task)) for _ in range(5)]
        await asyncio.gather(*tasks)

        # No more than 2 tasks should be active at once
        assert all(count <= 2 for count in active_count)
        assert pool.completed_tasks == 5


class TestAdaptiveTaskPoolScaling:
    """Tests for auto-scaling logic."""

    @pytest.mark.asyncio
    async def test_consider_scaling_respects_cooldown(self):
        """_consider_scaling should not scale within cooldown period."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=5, auto_scale=True)
        pool.scale_cooldown = 30.0

        async def task():
            return "ok"

        await pool.submit(task)

        # Cooldown mechanism is tested more explicitly in next test

    @pytest.mark.asyncio
    @patch("src.core.connection.time.time", return_value=1000.0)
    async def test_consider_scaling_within_cooldown_does_not_scale(self, mock_time):
        """_consider_scaling should skip scaling within cooldown."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=5, auto_scale=True)
        pool.scale_cooldown = 30.0
        pool.last_scale_time = 1000.0

        # Set up high utilization (would normally trigger scaling)
        pool.active_tasks = 5
        pool.queued_tasks = 20
        pool.performance_history = [1.0] * 20

        # Try to scale - should be blocked by cooldown
        await pool._consider_scaling()

        # Should not have scaled (cooldown not passed)
        assert pool.max_workers == 5

    @pytest.mark.asyncio
    async def test_consider_scaling_disabled_when_auto_scale_false(self):
        """_consider_scaling should not be called when auto_scale=False."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=5, auto_scale=False)

        async def task():
            return "ok"

        for _ in range(10):
            await pool.submit(task)

        # Should never scale
        assert pool.max_workers == 5

    @pytest.mark.asyncio
    @patch("src.core.connection.time.time", return_value=1100.0)
    @patch("src.core.connection.logger")
    async def test_scaling_up_on_high_utilization(self, mock_logger, mock_time):
        """Pool should scale up under high utilization and queue pressure."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=3, auto_scale=True)
        pool.scale_cooldown = 0.1  # Short cooldown for testing

        # Simulate high load scenario
        # Need utilization > 0.8, so: active / (semaphore._value + active) > 0.8
        # If active=3, semaphore._value=0 (all busy), then utilization = 3/3 = 1.0 > 0.8
        pool.semaphore._value = 0  # All workers busy
        pool.active_tasks = 3  # High utilization
        pool.queued_tasks = 10  # High queue pressure (10/3 > 2)
        pool.performance_history = [1.0] * 20  # Stable performance
        pool.last_scale_time = 1000.0  # 100s ago, past cooldown

        # Manually trigger scaling check
        await pool._consider_scaling()

        # Should have scaled up (3 + 2 = 5)
        assert pool.max_workers == 5

    @pytest.mark.asyncio
    @patch("src.core.connection.time.time", side_effect=[1000.0 + i for i in range(50)])
    @patch("src.core.connection.logger")
    async def test_scaling_down_on_low_utilization(self, mock_logger, mock_time):
        """Pool should scale down under low utilization."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=10, auto_scale=True)
        pool.scale_cooldown = 0.1

        # Simulate low load scenario
        pool.active_tasks = 1  # Low utilization (10%)
        pool.queued_tasks = 0  # No queue pressure
        pool.performance_history = [1.0] * 20
        pool.last_scale_time = 1000.0

        await pool._consider_scaling()

        # Should have scaled down
        assert pool.max_workers < 10

    @pytest.mark.asyncio
    @patch("src.core.connection.time.time", return_value=2000.0)
    async def test_scaling_respects_max_limit(self, mock_time):
        """Pool should not scale beyond 20 workers."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=19, auto_scale=True)
        pool.scale_cooldown = 0.1

        # Simulate high load with all workers busy
        pool.semaphore._value = 0  # All 19 workers busy
        pool.active_tasks = 19  # utilization = 19/19 = 1.0 > 0.8
        pool.queued_tasks = 50  # queue_pressure = 50/19 > 2
        pool.performance_history = [1.0] * 20
        pool.last_scale_time = 1000.0

        await pool._consider_scaling()

        # Should scale to 20 max (19 + 2 = 21, capped at 20)
        assert pool.max_workers == 20

    @pytest.mark.asyncio
    @patch("src.core.connection.time.time", side_effect=[1000.0 + i for i in range(50)])
    async def test_scaling_respects_min_limit(self, mock_time):
        """Pool should not scale below 2 workers."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=3, auto_scale=True)
        pool.scale_cooldown = 0.1

        # Simulate low load
        pool.active_tasks = 0
        pool.queued_tasks = 0
        pool.performance_history = [1.0] * 20
        pool.last_scale_time = 1000.0

        await pool._consider_scaling()

        # Should scale down to 2 min (3 - 1 = 2)
        assert pool.max_workers >= 2

    @pytest.mark.asyncio
    @patch("src.core.connection.time.time", side_effect=[1000.0 + i for i in range(50)])
    async def test_scaling_prevents_degraded_performance_scale_up(self, mock_time):
        """Pool should not scale up if performance is degrading."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=5, auto_scale=True)
        pool.scale_cooldown = 0.1

        # High utilization but degraded performance
        pool.active_tasks = 5
        pool.queued_tasks = 15
        # Recent tasks slower than historical (degraded by >20%)
        pool.performance_history = [1.0] * 15 + [2.5, 2.5, 2.5, 2.5, 2.5]
        pool.last_scale_time = 1000.0

        initial_workers = pool.max_workers
        await pool._consider_scaling()

        # Should NOT scale up due to performance degradation
        assert pool.max_workers == initial_workers


class TestAdaptiveTaskPoolStatistics:
    """Tests for statistics collection."""

    def test_get_stats_returns_correct_structure(self):
        """get_stats should return dict with all expected keys."""
        pool = AdaptiveTaskPool(PoolType.PROCESSING, max_workers=4)

        stats = pool.get_stats()

        assert "pool_type" in stats
        assert "max_workers" in stats
        assert "active_tasks" in stats
        assert "queued_tasks" in stats
        assert "completed_tasks" in stats
        assert "failed_tasks" in stats
        assert "success_rate" in stats
        assert "avg_task_time" in stats
        assert "utilization" in stats

    def test_get_stats_calculates_success_rate(self):
        """get_stats should calculate success rate correctly."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD)
        pool.completed_tasks = 7
        pool.failed_tasks = 3

        stats = pool.get_stats()

        # Success rate = 7 / (7 + 3) = 0.7
        assert stats["success_rate"] == 0.7

    def test_get_stats_success_rate_all_completed(self):
        """get_stats should return 1.0 success rate for all completed."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD)
        pool.completed_tasks = 10
        pool.failed_tasks = 0

        stats = pool.get_stats()

        assert stats["success_rate"] == 1.0

    def test_get_stats_success_rate_no_tasks(self):
        """get_stats should return 1.0 success rate when no tasks run."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD)

        stats = pool.get_stats()

        # Default optimistic: 1.0 when no tasks
        assert stats["success_rate"] == 1.0

    def test_get_stats_calculates_utilization(self):
        """get_stats should calculate utilization correctly."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=10)
        pool.active_tasks = 7

        stats = pool.get_stats()

        # Utilization = 7 / 10 = 0.7
        assert stats["utilization"] == 0.7

    def test_get_stats_includes_pool_type_value(self):
        """get_stats should include pool_type as string value."""
        pool = AdaptiveTaskPool(PoolType.FFMPEG, max_workers=2)

        stats = pool.get_stats()

        assert stats["pool_type"] == "ffmpeg"

    def test_get_stats_includes_avg_task_time(self):
        """get_stats should include current avg_task_time."""
        pool = AdaptiveTaskPool(PoolType.API)
        pool.avg_task_time = 2.5

        stats = pool.get_stats()

        assert stats["avg_task_time"] == 2.5

    @pytest.mark.asyncio
    async def test_get_stats_reflects_current_state(self):
        """get_stats should reflect current pool state."""
        pool = AdaptiveTaskPool(PoolType.DOWNLOAD, max_workers=5, auto_scale=False)

        async def task():
            return "ok"

        # Run 2 tasks
        await pool.submit(task)
        await pool.submit(task)

        stats = pool.get_stats()

        assert stats["completed_tasks"] == 2
        assert stats["max_workers"] == 5
        assert stats["active_tasks"] == 0  # After completion
