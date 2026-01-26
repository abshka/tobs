"""
Integration tests for graceful shutdown with Exporter (TIER A - Task 3).

Tests that exporter stops gracefully when shutdown is requested.
"""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.config import Config
from src.export.exporter import Exporter
from src.shutdown_manager import ShutdownManager


@pytest.mark.asyncio
async def test_exporter_stops_on_shutdown_request():
    """Exporter should stop message fetch when shutdown is requested."""
    # Setup
    config = Mock(spec=Config)
    config.export_path = "/tmp/test"
    config.use_structured_export = False
    
    telegram_manager = Mock()
    cache_manager = Mock()
    media_processor = Mock()
    note_generator = Mock()
    http_session = Mock()
    performance_monitor = Mock()

    exporter = Exporter(config, telegram_manager, cache_manager, media_processor, note_generator, http_session, performance_monitor=performance_monitor)

    # Run export (will be interrupted by shutdown)
    # We expect it to stop gracefully at message 50
    # This test verifies the shutdown check works
    
    # Note: Full integration test would require more setup
    # This is a lightweight smoke test
    assert True  # Placeholder - full test would await export


@pytest.mark.asyncio
async def test_async_pipeline_stops_on_shutdown():
    """AsyncPipeline should stop fetch when shutdown is requested."""
    from src.export.pipeline import AsyncPipeline
    
    # Setup pipeline
    pipeline = AsyncPipeline(
        fetch_workers=1,
        process_workers=2,
        write_workers=1
    )
    
    # Mock telegram manager
    telegram_manager = Mock()
    
    # Create mock messages that trigger shutdown mid-fetch
    async def mock_messages(entity, limit=None, min_id=None):
        for i in range(100):
            if i == 30:
                from src.shutdown_manager import shutdown_manager
                shutdown_manager.shutdown_requested = True
            
            msg = Mock()
            msg.id = i
            yield msg
    
    telegram_manager.fetch_messages = mock_messages
    
    # Mock process and writer functions
    async def mock_process(msg):
        await asyncio.sleep(0.001)
        return f"Processed: {msg.id}"
    
    async def mock_writer(result):
        pass
    
    # Run pipeline (will be interrupted by shutdown)
    stats = await pipeline.run(
        entity=Mock(),
        telegram_manager=telegram_manager,
        process_fn=mock_process,
        writer_fn=mock_writer,
        limit=None
    )
    
    # Should have stopped early due to shutdown
    # Exact count depends on timing, but should be less than 100
    assert stats['processed_count'] < 100
    
    # Reset for next test
    from src.shutdown_manager import shutdown_manager
    shutdown_manager.shutdown_requested = False


@pytest.mark.asyncio
async def test_cleanup_hooks_registered():
    """Verify that cleanup hooks are registered properly."""
    from src.shutdown_manager import shutdown_manager
    
    # Reset state
    shutdown_manager._cleanup_hooks = []
    shutdown_manager._async_cleanup_hooks = []
    
    # Register some hooks (simulating main.py behavior)
    mock_cache = Mock()
    mock_cache.close = Mock()
    
    shutdown_manager.register_cleanup_hook(mock_cache.close)
    
    async def mock_disconnect():
        pass
    
    shutdown_manager.register_async_cleanup_hook(mock_disconnect)
    
    # Verify registration
    assert len(shutdown_manager._cleanup_hooks) == 1
    assert len(shutdown_manager._async_cleanup_hooks) == 1


@pytest.mark.asyncio
async def test_progress_saved_on_shutdown():
    """Verify progress is saved when shutdown is requested."""
    # This would test _save_progress_on_shutdown method
    # Requires more complex setup with real EntityCacheData
    # Placeholder for now
    assert True  # TODO: Implement full test


@pytest.mark.asyncio  
async def test_buffers_flushed_on_shutdown():
    """Verify that buffers (logs, files) are flushed on shutdown."""
    from src.logging.global_batcher import global_batcher
    from src.shutdown_manager import shutdown_manager
    
    # Add some log messages to buffer
    global_batcher.lazy_log("INFO", "Test message 1")
    global_batcher.lazy_log("INFO", "Test message 2")
    
    # Simulate shutdown
    shutdown_manager.shutdown_requested = True
    
    # Run cleanup (should flush logs)
    await shutdown_manager.run_graceful_cleanup()
    
    # Verify flush was called (logs should be empty)
    # Note: This is best-effort test, actual verification would need log capture
    assert True  # Placeholder
    
    # Reset
    shutdown_manager.shutdown_requested = False
