"""
Unit tests for ResourceMonitor (TIER C-4).

Tests periodic resource sampling and metrics integration.
"""

import asyncio
from unittest.mock import Mock, patch

import pytest

from src.monitoring.resource_monitor import ResourceMonitor
from src.monitoring.metrics_collector import get_metrics_collector


@pytest.mark.asyncio
async def test_resource_monitor_start_stop():
    """Test ResourceMonitor starts and stops cleanly."""
    monitor = ResourceMonitor(interval_s=0.1)
    
    await monitor.start()
    assert monitor._task is not None
    
    await asyncio.sleep(0.05)  # Let it run briefly
    
    await monitor.stop()
    assert monitor._task is None


@pytest.mark.asyncio
async def test_resource_monitor_samples_resources():
    """Test ResourceMonitor samples and records resources."""
    metrics = get_metrics_collector()
    initial_samples = len(metrics.resources.cpu_percent)
    
    monitor = ResourceMonitor(interval_s=0.1)
    
    await monitor.start()
    await asyncio.sleep(0.35)  # ~3 samples (0.1s interval)
    await monitor.stop()
    
    final_samples = len(metrics.resources.cpu_percent)
    
    # Should have at least 2 new samples
    assert final_samples > initial_samples
    assert final_samples - initial_samples >= 2



@pytest.mark.asyncio
async def test_resource_monitor_double_start_warning():
    """Test ResourceMonitor warns on double start."""
    monitor = ResourceMonitor(interval_s=0.1)

    with patch("src.monitoring.resource_monitor.logger") as mock_logger:
        await monitor.start()
        await monitor.start()  # Second start should warn
        # Ensure warning was logged
        mock_logger.warning.assert_called()
        mock_logger.warning.assert_any_call("ResourceMonitor already running")

    await monitor.stop()


@pytest.mark.asyncio
async def test_resource_monitor_stop_without_start():
    """Test ResourceMonitor stop without start is safe."""
    monitor = ResourceMonitor(interval_s=0.1)
    
    # Should not raise
    await monitor.stop()


@pytest.mark.asyncio
async def test_resource_monitor_handles_errors_gracefully():
    """Test ResourceMonitor continues after errors."""
    monitor = ResourceMonitor(interval_s=0.1)
    
    # Mock psutil to raise exception once
    with patch("psutil.disk_io_counters", side_effect=[Exception("test error"), None, None]):
        await monitor.start()
        await asyncio.sleep(0.35)  # Let it sample a few times
        await monitor.stop()
    
    # Should have survived the error and continued sampling
    metrics = get_metrics_collector()
    assert len(metrics.resources.cpu_percent) > 0


@pytest.mark.asyncio
async def test_resource_monitor_accepts_interval_seconds():
    """Test that the backward-compatible `interval_seconds` kwarg works."""
    monitor = ResourceMonitor(interval_seconds=0.1)
    await monitor.start()
    assert monitor._task is not None
    await asyncio.sleep(0.05)
    await monitor.stop()
    assert monitor._task is None


def test_resource_monitor_conflicting_intervals_raise():
    """Providing conflicting `interval_s` and `interval_seconds` should raise."""
    with pytest.raises(ValueError):
        ResourceMonitor(interval_s=0.1, interval_seconds=0.2)
