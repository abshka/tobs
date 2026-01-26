"""
System resource monitoring for TOBS.

Periodically samples CPU, memory, disk I/O, and network I/O.
Part of TIER C-4 optimization.
"""

import asyncio
import psutil

from src.monitoring.metrics_collector import get_metrics_collector

# Graceful logger import
try:
    from src.utils import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class ResourceMonitor:
    """
    Monitor system resources periodically and record to MetricsCollector.
    
    Samples:
    - CPU utilization percentage
    - Memory usage (RSS) in MB
    - Disk I/O (read + write) in MB
    - Network I/O (sent + received) in MB
    
    Typical usage:
        # Preferred usage
        monitor = ResourceMonitor(interval_s=5.0)
        # Backwards-compatible alias
        monitor = ResourceMonitor(interval_seconds=5.0)
        await monitor.start()
        # ... do work ...
        await monitor.stop()
    """
    
    def __init__(self, interval_s: float | None = None, interval_seconds: float | None = None):
        """
        Initialize resource monitor.
        
        Args:
            interval_s: Sampling interval in seconds (preferred)
            interval_seconds: Backwards-compatible alias for `interval_s` (deprecated)
        """
        # Resolve argument precedence and backward compatibility
        if interval_s is None and interval_seconds is None:
            # Default interval
            self.interval_s = 5.0
        elif interval_s is None:
            # Old callers using `interval_seconds`
            import warnings
            warnings.warn("ResourceMonitor parameter `interval_seconds` is deprecated; use `interval_s` instead", DeprecationWarning)
            self.interval_s = interval_seconds
        elif interval_seconds is None:
            self.interval_s = interval_s
        else:
            # Both provided - ensure they agree
            if abs(interval_s - interval_seconds) > 1e-9:
                raise ValueError("Conflicting interval arguments: `interval_s` != `interval_seconds`")
            self.interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._process = psutil.Process()
        self._last_disk_io = psutil.disk_io_counters()
        self._last_net_io = psutil.net_io_counters()
    
    async def start(self) -> None:
        """Start periodic monitoring loop."""
        if self._task is not None:
            logger.warning("ResourceMonitor already running")
            return
        
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"📊 ResourceMonitor started (interval={self.interval_s}s)")
    
    async def stop(self) -> None:
        """Stop monitoring loop."""
        if self._task is None:
            return
        
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        
        self._task = None
        logger.info("📊 ResourceMonitor stopped")
    
    async def _monitor_loop(self) -> None:
        """Periodic resource sampling loop."""
        metrics = get_metrics_collector()
        
        while True:
            try:
                # CPU (percentage for this process)
                cpu_pct = self._process.cpu_percent(interval=None)
                
                # Memory (RSS in MB)
                memory_mb = self._process.memory_info().rss / 1024 / 1024
                
                # Disk I/O delta (total read + write in MB)
                current_disk_io = psutil.disk_io_counters()
                if current_disk_io and self._last_disk_io:
                    disk_read_mb = (current_disk_io.read_bytes - self._last_disk_io.read_bytes) / 1024 / 1024
                    disk_write_mb = (current_disk_io.write_bytes - self._last_disk_io.write_bytes) / 1024 / 1024
                    disk_io_mb = disk_read_mb + disk_write_mb
                    self._last_disk_io = current_disk_io
                else:
                    disk_io_mb = 0.0
                
                # Network I/O delta (total sent + received in MB)
                current_net_io = psutil.net_io_counters()
                if current_net_io and self._last_net_io:
                    net_sent_mb = (current_net_io.bytes_sent - self._last_net_io.bytes_sent) / 1024 / 1024
                    net_recv_mb = (current_net_io.bytes_recv - self._last_net_io.bytes_recv) / 1024 / 1024
                    network_io_mb = net_sent_mb + net_recv_mb
                    self._last_net_io = current_net_io
                else:
                    network_io_mb = 0.0
                
                # Record to metrics
                metrics.record_resources(cpu_pct, memory_mb, disk_io_mb, network_io_mb)
                
                await asyncio.sleep(self.interval_s)
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ ResourceMonitor error: {e}")
                await asyncio.sleep(self.interval_s)
