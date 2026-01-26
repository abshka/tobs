"""
Unit tests for UnifiedThreadPool.

Tests the thread pool singleton, task submission, metrics, and shutdown.
TIER B - Task B-1
"""

import asyncio
import time
from pathlib import Path

import pytest

from src.core.thread_pool import (
    TaskPriority,
    ThreadPoolMetrics,
    UnifiedThreadPool,
    get_thread_pool,
    shutdown_thread_pool,
)


@pytest.fixture(autouse=True)
def cleanup_global_pool():
    """Cleanup global thread pool between tests."""
    yield
    shutdown_thread_pool(wait=False)
    # Reset global singleton
    import src.core.thread_pool as tp_module
    tp_module._thread_pool = None


def test_thread_pool_singleton():
    """Test that get_thread_pool returns same instance."""
    pool1 = get_thread_pool()
    pool2 = get_thread_pool()
    assert pool1 is pool2, "get_thread_pool() should return singleton"


def test_thread_pool_auto_workers():
    """Test auto-detection of worker count."""
    pool = UnifiedThreadPool(max_workers=None)
    assert pool._max_workers >= 4, "Should have at least 4 workers"
    import os
    expected_min = (os.cpu_count() or 4) * 1.5
    assert pool._max_workers >= int(expected_min), f"Should auto-tune to CPU cores * 1.5"


@pytest.mark.asyncio
async def test_submit_task_basic():
    """Test basic task submission and execution."""
    pool = UnifiedThreadPool(max_workers=2)
    
    def cpu_bound_task(x):
        return x * 2
    
    result = await pool.submit(cpu_bound_task, 21)
    assert result == 42, "Task should execute correctly"


@pytest.mark.asyncio
async def test_submit_task_with_priority():
    """Test task submission with different priorities."""
    pool = UnifiedThreadPool(max_workers=2)
    
    # Submit tasks with different priorities
    async def submit_high():
        return await pool.submit(lambda: "HIGH", priority=TaskPriority.HIGH)
    
    async def submit_normal():
        return await pool.submit(lambda: "NORMAL", priority=TaskPriority.NORMAL)
    
    async def submit_low():
        return await pool.submit(lambda: "LOW", priority=TaskPriority.LOW)
    
    results = await asyncio.gather(submit_high(), submit_normal(), submit_low())
    assert set(results) == {"HIGH", "NORMAL", "LOW"}, "All priorities should work"


@pytest.mark.asyncio
async def test_multiple_concurrent_tasks():
    """Test multiple tasks executing concurrently."""
    pool = UnifiedThreadPool(max_workers=4)
    
    def slow_task(task_id, delay=0.1):
        time.sleep(delay)
        return task_id
    
    # Submit 10 tasks
    tasks = [pool.submit(slow_task, i) for i in range(10)]
    
    start = time.time()
    results = await asyncio.gather(*tasks)
    duration = time.time() - start
    
    assert len(results) == 10, "All tasks should complete"
    assert set(results) == set(range(10)), "All task IDs should be returned"
    # With 4 workers, 10 tasks @ 0.1s each should take ~0.25s, not 1.0s (sequential)
    assert duration < 0.6, f"Parallel execution should be faster (took {duration:.2f}s)"


@pytest.mark.asyncio
async def test_task_failure_handling():
    """Test that failed tasks update metrics correctly."""
    pool = UnifiedThreadPool(max_workers=2)
    
    def failing_task():
        raise ValueError("Intentional failure")
    
    with pytest.raises(ValueError, match="Intentional failure"):
        await pool.submit(failing_task)
    
    metrics = pool.get_metrics()
    assert metrics.failed_tasks >= 1, "Failed task should be tracked"


@pytest.mark.asyncio
async def test_metrics_collection():
    """Test metrics collection during task execution."""
    pool = UnifiedThreadPool(max_workers=2)
    
    # Submit some tasks
    await pool.submit(lambda: None)
    await pool.submit(lambda: time.sleep(0.01))
    
    metrics = pool.get_metrics()
    
    assert metrics.completed_tasks >= 2, "Completed tasks should be tracked"
    assert metrics.avg_task_latency_ms >= 0, "Latency should be measured"
    assert metrics.high_priority_tasks + metrics.normal_priority_tasks >= 2


@pytest.mark.asyncio
async def test_graceful_shutdown():
    """Test graceful shutdown waits for tasks."""
    pool = UnifiedThreadPool(max_workers=2)
    
    # Submit long-running task
    task = pool.submit(lambda: time.sleep(0.2))
    
    # Start shutdown (should wait for task)
    start = time.time()
    pool.shutdown(wait=True)
    duration = time.time() - start
    
    # Should have waited for task to complete
    assert duration >= 0.1, "Shutdown should wait for running tasks"

    # Task should complete successfully (no exception)
    await task


def test_shutdown_no_wait():
    """Test immediate shutdown without waiting."""
    pool = UnifiedThreadPool(max_workers=2)
    
    # Shutdown immediately
    start = time.time()
    pool.shutdown(wait=False)
    duration = time.time() - start
    
    # Should be instant
    assert duration < 0.1, "Shutdown with wait=False should be immediate"


@pytest.mark.asyncio
async def test_env_override_max_threads():
    """Test MAX_THREADS environment variable override."""
    import os
    
    # Set ENV override
    os.environ["MAX_THREADS"] = "8"
    
    try:
        pool = UnifiedThreadPool(max_workers=None)
        assert pool._max_workers == 8, "Should respect MAX_THREADS env var"
    finally:
        # Cleanup
        del os.environ["MAX_THREADS"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
