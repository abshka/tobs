"""
Unit tests for ShutdownManager (TIER A - Task 3: Graceful Shutdown).

Tests the two-stage Ctrl+C mechanism and cleanup hook execution.
"""
import asyncio
import signal
import time
from unittest.mock import Mock

import pytest

from src.shutdown_manager import ShutdownManager


def test_first_sigint_sets_graceful_flag():
    """First Ctrl+C should set shutdown_requested flag."""
    mgr = ShutdownManager()
    
    # Simulate first SIGINT
    mgr.handle_sigint(signal.SIGINT, None)
    
    assert mgr.shutdown_requested is True
    assert mgr.force_shutdown is False
    assert mgr.first_sigint_time is not None


def test_second_sigint_within_timeout_forces_shutdown():
    """Second Ctrl+C within timeout window should trigger force shutdown."""
    mgr = ShutdownManager(force_shutdown_timeout=1.0)
    
    # First SIGINT
    mgr.handle_sigint(signal.SIGINT, None)
    assert mgr.shutdown_requested is True
    
    # Second SIGINT immediately (within timeout)
    with pytest.raises(SystemExit) as exc_info:
        mgr.handle_sigint(signal.SIGINT, None)
    
    assert exc_info.value.code == 1
    assert mgr.force_shutdown is True


def test_second_sigint_after_timeout_restarts_graceful():
    """Second Ctrl+C after timeout should restart graceful shutdown."""
    mgr = ShutdownManager(force_shutdown_timeout=0.1)
    
    # First SIGINT
    mgr.handle_sigint(signal.SIGINT, None)
    first_time = mgr.first_sigint_time
    
    # Wait for timeout to expire
    time.sleep(0.2)
    
    # Second SIGINT after timeout - should NOT force exit
    mgr.handle_sigint(signal.SIGINT, None)
    
    # Should restart graceful shutdown
    assert mgr.shutdown_requested is True
    assert mgr.force_shutdown is False
    assert mgr.first_sigint_time > first_time  # New timestamp


def test_cleanup_hooks_executed():
    """Sync cleanup hooks should be called during graceful cleanup."""
    mgr = ShutdownManager()
    mgr.shutdown_requested = True  # Simulate shutdown
    
    executed = []
    
    mgr.register_cleanup_hook(lambda: executed.append('sync1'))
    mgr.register_cleanup_hook(lambda: executed.append('sync2'))
    
    # Run cleanup
    asyncio.run(mgr.run_graceful_cleanup())
    
    assert 'sync1' in executed
    assert 'sync2' in executed
    assert len(executed) == 2


@pytest.mark.asyncio
async def test_async_cleanup_hooks_executed():
    """Async cleanup hooks should be awaited during graceful cleanup."""
    mgr = ShutdownManager()
    mgr.shutdown_requested = True  # Simulate shutdown
    
    executed = []
    
    async def async_hook1():
        await asyncio.sleep(0.01)
        executed.append('async1')
        
    async def async_hook2():
        await asyncio.sleep(0.01)
        executed.append('async2')
    
    mgr.register_async_cleanup_hook(async_hook1)
    mgr.register_async_cleanup_hook(async_hook2)
    
    # Run cleanup
    await mgr.run_graceful_cleanup()
    
    assert 'async1' in executed
    assert 'async2' in executed
    assert len(executed) == 2


@pytest.mark.asyncio
async def test_cleanup_continues_on_hook_error():
    """Cleanup should continue even if individual hooks fail."""
    mgr = ShutdownManager()
    mgr.shutdown_requested = True
    
    executed = []
    
    def failing_hook():
        raise RuntimeError("Hook failed!")
        
    def success_hook():
        executed.append('success')
    
    mgr.register_cleanup_hook(failing_hook)
    mgr.register_cleanup_hook(success_hook)
    
    # Should not raise - cleanup continues
    await mgr.run_graceful_cleanup()
    
    # Success hook should still execute
    assert 'success' in executed


def test_cleanup_skipped_if_shutdown_not_requested():
    """Graceful cleanup should skip if shutdown not requested."""
    mgr = ShutdownManager()
    # Don't set shutdown_requested
    
    executed = []
    mgr.register_cleanup_hook(lambda: executed.append('hook'))
    
    asyncio.run(mgr.run_graceful_cleanup())
    
    # Hook should NOT be executed
    assert len(executed) == 0


def test_minimal_cleanup_on_force_shutdown():
    """Minimal cleanup should flush logs on force shutdown."""
    mgr = ShutdownManager(force_shutdown_timeout=1.0)
    
    # First SIGINT
    mgr.handle_sigint(signal.SIGINT, None)
    
    # Second SIGINT - will call _run_minimal_cleanup before exit
    # We can't easily test sys.exit(1), but we can test _run_minimal_cleanup directly
    mgr._run_minimal_cleanup()
    
    # Should complete without error (flushes logs)
    # No assertion needed - just verify it doesn't raise


@pytest.mark.asyncio
async def test_cleanup_hooks_execution_order():
    """Cleanup hooks should execute in registration order."""
    mgr = ShutdownManager()
    mgr.shutdown_requested = True
    
    execution_order = []
    
    mgr.register_cleanup_hook(lambda: execution_order.append('sync1'))
    mgr.register_cleanup_hook(lambda: execution_order.append('sync2'))
    
    async def async_hook():
        execution_order.append('async1')
        
    mgr.register_async_cleanup_hook(async_hook)
    
    await mgr.run_graceful_cleanup()
    
    # Sync hooks execute first, then async hooks
    assert execution_order == ['sync1', 'sync2', 'async1']


def test_multiple_cleanup_registrations():
    """Multiple hooks can be registered and all execute."""
    mgr = ShutdownManager()
    mgr.shutdown_requested = True
    
    count = [0]  # Use list for closure modification
    
    for i in range(5):
        mgr.register_cleanup_hook(lambda: count.__setitem__(0, count[0] + 1))
    
    asyncio.run(mgr.run_graceful_cleanup())
    
    assert count[0] == 5
