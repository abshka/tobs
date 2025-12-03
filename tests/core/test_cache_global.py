"""
Unit tests for global CacheManager functions.

Tests cover:
- get_cache_manager(): Singleton instance creation and retrieval
- shutdown_cache_manager(): Graceful shutdown and cleanup
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.core.cache import CacheManager, get_cache_manager, shutdown_cache_manager

# ============================================================================
# get_cache_manager() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_cache_manager_creates_instance_on_first_call():
    """Creates singleton instance on first call."""
    # Reset global state
    import src.core.cache as cache_module

    cache_module._cache_manager = None

    manager = await get_cache_manager()

    assert manager is not None
    assert isinstance(manager, CacheManager)

    # Cleanup
    await shutdown_cache_manager()


@pytest.mark.asyncio
async def test_get_cache_manager_returns_same_instance():
    """Returns same instance on subsequent calls."""
    # Reset global state
    import src.core.cache as cache_module

    cache_module._cache_manager = None

    manager1 = await get_cache_manager()
    manager2 = await get_cache_manager()

    assert manager1 is manager2

    # Cleanup
    await shutdown_cache_manager()


@pytest.mark.asyncio
async def test_get_cache_manager_creates_temp_directory():
    """Creates temp directory for cache."""
    # Reset global state
    import src.core.cache as cache_module

    cache_module._cache_manager = None

    with patch("src.core.cache.Path.mkdir") as mock_mkdir:
        with patch.object(CacheManager, "start", new_callable=AsyncMock):
            manager = await get_cache_manager()

    # Should have called mkdir with exist_ok=True
    assert mock_mkdir.called
    call_kwargs = mock_mkdir.call_args[1] if mock_mkdir.call_args else {}
    assert call_kwargs.get("exist_ok") is True

    # Cleanup
    await shutdown_cache_manager()


@pytest.mark.asyncio
async def test_get_cache_manager_starts_manager():
    """Starts the manager (calls start())."""
    # Reset global state
    import src.core.cache as cache_module

    cache_module._cache_manager = None

    start_called = False

    async def mock_start(self):
        nonlocal start_called
        start_called = True

    with patch.object(CacheManager, "start", mock_start):
        manager = await get_cache_manager()

    assert start_called

    # Cleanup
    await shutdown_cache_manager()


@pytest.mark.asyncio
async def test_get_cache_manager_uses_correct_cache_path():
    """Uses correct cache path in temp directory."""
    # Reset global state
    import src.core.cache as cache_module

    cache_module._cache_manager = None

    with patch.object(CacheManager, "start", new_callable=AsyncMock):
        manager = await get_cache_manager()

    # Cache path should be in temp directory
    cache_path_str = str(manager.cache_path)
    assert "tobs_cache" in cache_path_str
    assert "cache.json" in cache_path_str

    # Cleanup
    await shutdown_cache_manager()


# ============================================================================
# shutdown_cache_manager() TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_shutdown_cache_manager_shuts_down_existing_manager():
    """Shuts down existing manager."""
    # Reset global state and create manager
    import src.core.cache as cache_module

    cache_module._cache_manager = None

    with patch.object(CacheManager, "start", new_callable=AsyncMock):
        manager = await get_cache_manager()

    shutdown_called = False

    async def track_shutdown():
        nonlocal shutdown_called
        shutdown_called = True

    with patch.object(
        manager, "shutdown", new_callable=AsyncMock, side_effect=track_shutdown
    ):
        await shutdown_cache_manager()

    assert shutdown_called


@pytest.mark.asyncio
async def test_shutdown_cache_manager_sets_global_to_none():
    """Sets global _cache_manager to None."""
    # Reset global state and create manager
    import src.core.cache as cache_module

    cache_module._cache_manager = None

    with patch.object(CacheManager, "start", new_callable=AsyncMock):
        await get_cache_manager()

    with patch.object(CacheManager, "shutdown", new_callable=AsyncMock):
        await shutdown_cache_manager()

    assert cache_module._cache_manager is None


@pytest.mark.asyncio
async def test_shutdown_cache_manager_handles_no_manager():
    """Handles case when no manager exists."""
    # Reset global state
    import src.core.cache as cache_module

    cache_module._cache_manager = None

    # Should not raise
    await shutdown_cache_manager()

    assert cache_module._cache_manager is None


@pytest.mark.asyncio
async def test_shutdown_cache_manager_allows_recreation():
    """Allows recreation after shutdown."""
    # Reset global state
    import src.core.cache as cache_module

    cache_module._cache_manager = None

    with patch.object(CacheManager, "start", new_callable=AsyncMock):
        with patch.object(CacheManager, "shutdown", new_callable=AsyncMock):
            # Create and shutdown
            manager1 = await get_cache_manager()
            await shutdown_cache_manager()

            # Create again
            manager2 = await get_cache_manager()

            # Should be different instances
            assert manager1 is not manager2

            # Cleanup
            await shutdown_cache_manager()
