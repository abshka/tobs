"""
Unit tests for ParallelMediaProcessor (TIER B - B-3)

Tests:
- Basic parallel processing
- Semaphore concurrency control
- Memory throttling
- Metrics tracking
- Sequential fallback
- Exception handling
"""

import asyncio
import time
from unittest.mock import Mock, AsyncMock, patch
import pytest

from src.media.parallel_processor import (
    ParallelMediaProcessor,
    ParallelMediaConfig,
    ParallelMediaMetrics,
)


# Helper classes
class MockMessage:
    """Mock Telegram message"""
    def __init__(self, msg_id: int, has_media: bool = False):
        self.id = msg_id
        self.media = Mock() if has_media else None


@pytest.mark.asyncio
async def test_parallel_processing_basic():
    """Test basic parallel processing of messages"""
    config = ParallelMediaConfig(max_concurrent=2, enable_parallel=True)
    processor = ParallelMediaProcessor(config)
    
    # Create mock messages
    messages = [MockMessage(i, has_media=True) for i in range(5)]
    
    # Mock process function with delay
    async def process_fn(msg):
        await asyncio.sleep(0.1)
        return f"processed_{msg.id}"
    
    # Process batch
    start = time.time()
    results = await processor.process_batch(messages, process_fn)
    duration = time.time() - start
    
    # Verify results
    assert len(results) == 5
    assert all(isinstance(r, str) for r in results)
    
    # With max_concurrent=2, should be ~2.5x faster than sequential (5*0.1/2 ≈ 0.25s)
    # Allow some overhead, but should be significantly faster than 0.5s (sequential)
    assert duration < 0.4, f"Expected faster than 0.4s, got {duration:.2f}s"
    
    print(f"✅ Parallel processing: {len(messages)} messages in {duration:.2f}s")


@pytest.mark.asyncio
async def test_semaphore_concurrency_limit():
    """Test that semaphore correctly limits concurrency"""
    config = ParallelMediaConfig(max_concurrent=2, enable_parallel=True)
    processor = ParallelMediaProcessor(config)
    
    # Track concurrent executions
    concurrent_count = 0
    max_concurrent_observed = 0
    lock = asyncio.Lock()
    
    async def process_fn(msg):
        nonlocal concurrent_count, max_concurrent_observed
        async with lock:
            concurrent_count += 1
            max_concurrent_observed = max(max_concurrent_observed, concurrent_count)
        
        await asyncio.sleep(0.05)
        
        async with lock:
            concurrent_count -= 1
        
        return f"processed_{msg.id}"
    
    # Create messages with media
    messages = [MockMessage(i, has_media=True) for i in range(10)]
    
    # Process
    await processor.process_batch(messages, process_fn)
    
    # Verify concurrency was limited
    assert max_concurrent_observed <= config.max_concurrent, \
        f"Expected max {config.max_concurrent}, got {max_concurrent_observed}"
    assert max_concurrent_observed >= 1, "Should have some concurrency"
    
    print(f"✅ Semaphore test: max concurrent = {max_concurrent_observed}")


@pytest.mark.asyncio
async def test_sequential_fallback():
    """Test that parallel=False uses sequential processing"""
    config = ParallelMediaConfig(max_concurrent=4, enable_parallel=False)
    processor = ParallelMediaProcessor(config)
    
    messages = [MockMessage(i, has_media=True) for i in range(5)]
    
    async def process_fn(msg):
        await asyncio.sleep(0.05)
        return f"processed_{msg.id}"
    
    start = time.time()
    results = await processor.process_batch(messages, process_fn)
    duration = time.time() - start
    
    # Sequential should take ~5*0.05 = 0.25s
    assert duration >= 0.2, f"Sequential should be slower, got {duration:.2f}s"
    assert len(results) == 5
    
    print(f"✅ Sequential fallback: {len(messages)} messages in {duration:.2f}s")


@pytest.mark.asyncio
async def test_metrics_tracking():
    """Test that metrics are correctly tracked"""
    config = ParallelMediaConfig(max_concurrent=3, enable_parallel=True)
    processor = ParallelMediaProcessor(config)
    
    # Mix of media and non-media messages
    messages = [
        MockMessage(1, has_media=True),
        MockMessage(2, has_media=False),  # No media
        MockMessage(3, has_media=True),
        MockMessage(4, has_media=True),
        MockMessage(5, has_media=False),  # No media
    ]
    
    async def process_fn(msg):
        await asyncio.sleep(0.05)
        return f"processed_{msg.id}"
    
    await processor.process_batch(messages, process_fn)
    
    # Get metrics
    metrics = processor.get_metrics()
    
    # Verify metrics
    assert metrics.total_media_processed == 3, "Should count 3 media messages"
    assert metrics.concurrent_peak >= 1, "Should have some concurrency"
    assert metrics.concurrent_peak <= config.max_concurrent
    assert metrics.avg_concurrency > 0, "Average concurrency should be positive"
    
    print(f"✅ Metrics: {metrics.total_media_processed} media, "
          f"peak={metrics.concurrent_peak}, avg={metrics.avg_concurrency:.2f}")


@pytest.mark.asyncio
async def test_memory_throttling():
    """Test memory throttling when limit is exceeded"""
    config = ParallelMediaConfig(
        max_concurrent=2,
        memory_limit_mb=1,  # Very low limit to trigger throttle
        check_memory_interval=1,  # Check every message
        enable_parallel=True
    )
    processor = ParallelMediaProcessor(config)
    
    # Mock memory check to return high usage
    with patch.object(processor, '_check_memory', return_value=False):
        messages = [MockMessage(i, has_media=True) for i in range(5)]
        
        async def process_fn(msg):
            await asyncio.sleep(0.01)
            return f"processed_{msg.id}"
        
        start = time.time()
        results = await processor.process_batch(messages, process_fn)
        duration = time.time() - start
        
        # Should still complete successfully
        assert len(results) == 5
        
        # Should have throttled (added 1s delays)
        metrics = processor.get_metrics()
        assert metrics.memory_throttles > 0, "Should have throttled due to memory"
        
        print(f"✅ Memory throttling: {metrics.memory_throttles} throttles in {duration:.2f}s")


@pytest.mark.asyncio
async def test_exception_handling():
    """Test that exceptions in processing are handled gracefully"""
    config = ParallelMediaConfig(max_concurrent=2, enable_parallel=True)
    processor = ParallelMediaProcessor(config)
    
    messages = [MockMessage(i, has_media=True) for i in range(5)]
    
    # Process function that fails on message 3
    async def process_fn(msg):
        if msg.id == 3:
            raise ValueError(f"Simulated error for message {msg.id}")
        await asyncio.sleep(0.01)
        return f"processed_{msg.id}"
    
    results = await processor.process_batch(messages, process_fn)
    
    # Should get 5 results (4 successful + 1 exception)
    assert len(results) == 5
    
    # Check results
    successful = [r for r in results if not isinstance(r, Exception)]
    failed = [r for r in results if isinstance(r, Exception)]
    
    assert len(successful) == 4, "Should have 4 successful"
    assert len(failed) == 1, "Should have 1 failed"
    assert isinstance(failed[0], ValueError)
    
    print(f"✅ Exception handling: {len(successful)} successful, {len(failed)} failed")


@pytest.mark.asyncio
async def test_no_media_messages():
    """Test that messages without media are processed immediately"""
    config = ParallelMediaConfig(max_concurrent=2, enable_parallel=True)
    processor = ParallelMediaProcessor(config)
    
    # All messages without media
    messages = [MockMessage(i, has_media=False) for i in range(5)]
    
    async def process_fn(msg):
        await asyncio.sleep(0.01)
        return f"processed_{msg.id}"
    
    start = time.time()
    results = await processor.process_batch(messages, process_fn)
    duration = time.time() - start
    
    # Should process quickly without semaphore (all in parallel)
    assert len(results) == 5
    assert duration < 0.1, "No media should be fast"
    
    # Metrics should show 0 media processed
    metrics = processor.get_metrics()
    assert metrics.total_media_processed == 0
    
    print(f"✅ No media test: {len(messages)} messages in {duration:.2f}s")


@pytest.mark.asyncio
async def test_metrics_reset():
    """Test that metrics can be reset between batches"""
    config = ParallelMediaConfig(max_concurrent=2, enable_parallel=True)
    processor = ParallelMediaProcessor(config)
    
    messages = [MockMessage(i, has_media=True) for i in range(3)]
    
    async def process_fn(msg):
        await asyncio.sleep(0.01)
        return f"processed_{msg.id}"
    
    # First batch
    await processor.process_batch(messages, process_fn)
    metrics1 = processor.get_metrics()
    assert metrics1.total_media_processed == 3
    
    # Reset
    processor.reset_metrics()
    metrics2 = processor.get_metrics()
    assert metrics2.total_media_processed == 0
    assert metrics2.concurrent_peak == 0
    
    # Second batch
    await processor.process_batch(messages, process_fn)
    metrics3 = processor.get_metrics()
    assert metrics3.total_media_processed == 3
    
    print("✅ Metrics reset test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
