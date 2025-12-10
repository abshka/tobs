"""
Core systems manager.
Manages lifecycle of all core system components.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .core import (
    AlertLevel,
    CacheManager,
    ConnectionManager,
    PerformanceMonitor,
    ResourceState,
    get_cache_manager,
    get_connection_manager,
    get_performance_monitor,
    shutdown_cache_manager,
    shutdown_connection_manager,
    shutdown_performance_monitor,
)

logger = logging.getLogger(__name__)


class CoreSystemManager:
    """Manager of all core systems."""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        performance_profile: str = "balanced",
        health_check_interval: float = 0.1,
    ):
        # config_path preserved for API compatibility but not used
        self.config_path = config_path
        self.performance_profile = performance_profile

        # Managers
        self._cache_manager: Optional[CacheManager] = None
        self._connection_manager: Optional[ConnectionManager] = None
        self._performance_monitor: Optional[PerformanceMonitor] = None

        # State
        self._initialized = False
        self._shutdown = False

        # TaskGroup for managing background tasks
        self._task_group: Optional[asyncio.TaskGroup] = None
        self._task_group_runner: Optional[asyncio.Task] = None

        self._health_check_interval = health_check_interval
        self._sleep = asyncio.sleep  # Store reference to avoid global mocking

        # Statistics
        self._start_time = 0.0
        self._total_operations = 0
        self._successful_operations = 0
        self._failed_operations = 0

        # Adaptation configuration
        self.adaptation_enabled = True
        self.last_adaptation_time = 0.0
        self.adaptation_cooldown = 60.0  # Minimum 60 seconds between adaptations

    async def initialize(self) -> bool:
        """
        Initialize all core systems.

        Returns:
            True if initialization was successful
        """
        if self._initialized:
            logger.warning("Core systems already initialized")
            return True

        try:
            self._start_time = time.time()

            logger.info("Initializing core systems...")

            # Initialize components in correct order
            # 1. Performance monitor first
            self._performance_monitor = await get_performance_monitor(
                performance_profile=self.performance_profile
            )
            logger.info("Performance monitor initialized")

            # 2. Then cache manager
            self._cache_manager = await get_cache_manager()
            logger.info("Cache manager initialized")

            # 3. Finally connection manager
            self._connection_manager = await get_connection_manager()
            logger.info("Connection manager initialized")

            # Configure integration between components
            await self._setup_integration()

            # Start background tasks in TaskGroup
            self._task_group_runner = asyncio.create_task(self._run_background_tasks())
            logger.debug("Background task group started")

            self._initialized = True

            logger.info(
                f"Core systems initialized successfully in {time.time() - self._start_time:.2f}s"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialize core systems: {e}")
            await self._cleanup_on_failure()
            return False

    async def _run_background_tasks(self):
        """
        Start and manage background tasks in TaskGroup.

        TaskGroup automatically:
        - Manages lifecycle of all tasks
        - Aggregates exceptions from all tasks
        - Cancels all remaining tasks when exiting context
        """
        # Run initial health check synchronously
        try:
            await self._perform_health_check()
        except Exception as e:
            self._failed_operations += 1
            logger.error(f"Initial health check failed: {e}")

        try:
            async with asyncio.TaskGroup() as tg:
                self._task_group = tg
                logger.debug("TaskGroup context entered")

                # Create health check task in TaskGroup
                tg.create_task(self._health_check_loop())
                logger.debug("Health check task created in TaskGroup")

                # TaskGroup will wait for all tasks to complete
                # When exiting context, all tasks will be cancelled (if not completed)
        except* Exception as exc_group:
            # except* catches all exceptions from TaskGroup
            # exc_group contains all exceptions
            for exc in exc_group.exceptions:
                logger.error(
                    f"Background task failed: {type(exc).__name__}: {exc}", exc_info=exc
                )
                self._failed_operations += 1
        finally:
            self._task_group = None
            logger.debug("TaskGroup context exited and cleaned up")

    async def _setup_integration(self):
        """Configure integration between components."""
        # Add callbacks for monitoring
        if self._performance_monitor:
            self._performance_monitor.add_metric_callback(self._on_performance_metric)
            self._performance_monitor.add_alert_callback(self._on_performance_alert)

        logger.debug("Component integration configured")

    def _on_performance_metric(self, metrics):
        """Performance metrics handler."""
        # Can log critical metrics
        if metrics.cpu_percent > 90 or metrics.memory_percent > 90:
            logger.warning(
                f"High resource usage: CPU {metrics.cpu_percent:.1f}%, "
                f"Memory {metrics.memory_percent:.1f}%"
            )

    async def _on_performance_alert(self, alert):
        """Performance alerts handler."""
        if alert.level == AlertLevel.CRITICAL:
            logger.critical(f"Critical performance alert: {alert.message}")

            # For critical alerts, can automatically adapt
            if self.adaptation_enabled:
                await self._emergency_adaptation()

    async def _emergency_adaptation(self):
        """Emergency adaptation for critical issues."""
        if not self._performance_monitor:
            return

        current_time = time.time()
        if current_time - self.last_adaptation_time < self.adaptation_cooldown:
            return  # Too early for new adaptation

        state = self._performance_monitor.get_resource_state()

        if state == ResourceState.CRITICAL:
            logger.warning("Applying emergency performance adaptations")

            # Switch to conservative profile
            self._performance_monitor.set_performance_profile("conservative")

            # Reduce load in connection manager
            if self._connection_manager:
                # Can add methods to reduce load
                pass

            # Clear cache if needed
            if self._cache_manager:
                cache_stats = self._cache_manager.get_stats()
                if cache_stats.total_size_mb > 500:  # If cache > 500MB
                    await self._cache_manager.clear()
                    logger.info("Cache cleared due to memory pressure")

            self.last_adaptation_time = current_time

    async def _health_check_loop(self):
        """Periodic system health check."""
        logger.debug("Health check loop started")
        loop_iteration = 0

        while not self._shutdown:
            loop_iteration += 1
            try:
                # Run health check first, then sleep
                try:
                    await self._perform_health_check()
                except Exception as e:
                    # Track failed health check attempts and log
                    self._failed_operations += 1
                    logger.error(f"Health check raised exception: {e}")
                await self._sleep(self._health_check_interval)
            except asyncio.CancelledError:
                logger.debug(
                    f"Health check loop cancelled after {loop_iteration} iterations"
                )
                break
            except Exception as e:
                logger.error(
                    f"Error in health check loop iteration {loop_iteration}: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                # Continue loop despite error - don't let one failure kill the loop
                self._failed_operations += 1

    async def _perform_health_check(self):
        """Perform system health check."""
        issues = []

        # Check cache manager
        if self._cache_manager:
            try:
                # Simple check - attempt write and read
                await self._cache_manager.set(
                    "health_check", {"timestamp": time.time()}
                )
                result = await self._cache_manager.get("health_check")
                if not result:
                    issues.append("Cache manager: read/write test failed")
            except Exception as e:
                issues.append(f"Cache manager error: {e}")

        # Check connection manager
        if self._connection_manager:
            try:
                stats = self._connection_manager.get_pool_stats()
                # Check that pools respond
                if not stats:
                    issues.append("Connection manager: no pool stats available")
            except Exception as e:
                issues.append(f"Connection manager error: {e}")

        # Check performance monitor
        if self._performance_monitor:
            try:
                current_metrics = self._performance_monitor.get_current_metrics()
                if not current_metrics:
                    issues.append("Performance monitor: no current metrics")
            except Exception as e:
                issues.append(f"Performance monitor error: {e}")

        if issues:
            logger.warning(f"Health check found issues: {'; '.join(issues)}")
        else:
            logger.debug("Health check passed")

    async def shutdown(self):
        """Graceful shutdown of all systems."""
        if not self._initialized or self._shutdown:
            return

        logger.info("Shutting down core systems...")
        self._shutdown = True

        # Stop background tasks runner
        # TaskGroup automatically cancels all tasks when exiting
        if self._task_group_runner:
            if not self._task_group_runner.done():
                logger.debug("Cancelling background task group runner...")
                self._task_group_runner.cancel()
                try:
                    await self._task_group_runner
                except asyncio.CancelledError:
                    logger.debug("Background task group runner cancelled successfully")
            else:
                logger.debug("Background task group runner already done")

        # Stop components in reverse order
        try:
            await shutdown_connection_manager()
            logger.info("Connection manager shut down")
        except Exception as e:
            logger.error(f"Error shutting down connection manager: {e}")

        try:
            await shutdown_cache_manager()
            logger.info("Cache manager shut down")
        except Exception as e:
            logger.error(f"Error shutting down cache manager: {e}")

        try:
            await shutdown_performance_monitor()
            logger.info("Performance monitor shut down")
        except Exception as e:
            logger.error(f"Error shutting down performance monitor: {e}")

        self._initialized = False
        logger.info("Core systems shutdown complete")

    async def _cleanup_on_failure(self):
        """Cleanup on failed initialization."""
        try:
            await self.shutdown()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def get_comprehensive_status(self) -> Dict[str, Any]:
        """Get comprehensive status of all systems."""
        if not self._initialized:
            return {"status": "not_initialized", "uptime": 0, "components": {}}

        uptime = time.time() - self._start_time

        status: Dict[str, Any] = {
            "status": "running",
            "uptime": uptime,
            "total_operations": self._total_operations,
            "successful_operations": self._successful_operations,
            "failed_operations": self._failed_operations,
            "success_rate": (
                self._successful_operations / self._total_operations
                if self._total_operations > 0
                else 1.0
            ),
            "components": {},
        }

        # Cache manager status
        if self._cache_manager:
            try:
                cache_stats = self._cache_manager.get_stats()
                status["components"]["cache"] = {
                    "status": "active",
                    "strategy": self._cache_manager.strategy.value,
                    "total_size_mb": cache_stats.total_size_mb,
                    "hit_rate": cache_stats.hit_rate,
                    "compression_saves": cache_stats.compression_saves,
                }
            except Exception as e:
                status["components"]["cache"] = {"status": "error", "error": str(e)}

        # Connection manager status
        if self._connection_manager:
            try:
                pool_stats = self._connection_manager.get_pool_stats()
                status["components"]["connection"] = {
                    "status": "active",
                    "pools": pool_stats,
                }
            except Exception as e:
                status["components"]["connection"] = {
                    "status": "error",
                    "error": str(e),
                }

        # Performance monitor status
        if self._performance_monitor:
            try:
                perf_summary = self._performance_monitor.get_performance_summary()
                status["components"]["performance"] = {
                    "status": "active",
                    "resource_state": perf_summary["resource_state"],
                    "current_profile": perf_summary["current_profile"],
                    "active_alerts": perf_summary["active_alerts_count"],
                    "recommendations": len(perf_summary["recommendations"]),
                }
            except Exception as e:
                status["components"]["performance"] = {
                    "status": "error",
                    "error": str(e),
                }

        return status

    async def get_performance_recommendations(self) -> list[str]:
        """Get performance recommendations."""
        if not self._performance_monitor:
            return ["Performance monitor not available"]

        return self._performance_monitor.get_performance_recommendations()

    def record_operation(self, success: bool):
        """Record operation result."""
        self._total_operations += 1
        if success:
            self._successful_operations += 1
        else:
            self._failed_operations += 1

    # Methods for getting managers
    def get_cache_manager(self) -> Optional[CacheManager]:
        """Get cache manager."""
        return self._cache_manager

    def get_connection_manager(self) -> Optional[ConnectionManager]:
        """Get connection manager."""
        return self._connection_manager

    def get_performance_monitor(self) -> Optional[PerformanceMonitor]:
        """Get performance monitor."""
        return self._performance_monitor

    # Adaptation settings
    def enable_adaptation(self, enabled: bool = True):
        """Enable/disable automatic adaptation."""
        self.adaptation_enabled = enabled
        logger.info(f"Automatic adaptation {'enabled' if enabled else 'disabled'}")

    def set_adaptation_cooldown(self, seconds: float):
        """Set adaptation cooldown period."""
        self.adaptation_cooldown = max(30.0, seconds)  # Minimum 30 seconds

    async def force_cache_cleanup(self):
        """Force cache cleanup."""
        if self._cache_manager:
            await self._cache_manager.clear()
            logger.info("Cache forcibly cleared")

    def update_performance_profile(self, profile: str):
        """Update performance profile."""
        if profile not in ["conservative", "balanced", "aggressive"]:
            logger.warning(f"Unknown performance profile '{profile}', keeping current")
            return

        old_profile = self.performance_profile
        self.performance_profile = profile

        # Update performance monitor if initialized
        if self._performance_monitor:
            success = self._performance_monitor.set_performance_profile(profile)
            if success:
                logger.info(
                    f"Performance profile updated from {old_profile} to {profile}"
                )
            else:
                logger.warning(
                    f"Failed to update performance monitor profile to {profile}"
                )

        logger.info(f"Core manager performance profile set to: {profile}")

    def is_initialized(self) -> bool:
        """Check system initialization."""
        return self._initialized

    def get_uptime(self) -> float:
        """Get system uptime."""
        return time.time() - self._start_time if self._initialized else 0.0


# Global instance
_core_manager: Optional[CoreSystemManager] = None


async def initialize_core_systems(
    config_path: Optional[Path] = None,
    performance_profile: str = "balanced",
) -> CoreSystemManager:
    """
    Initialize core systems.

    Args:
        config_path: Path to configuration files
        performance_profile: Performance profile (conservative, balanced, aggressive)

    Returns:
        CoreSystemManager instance
    """
    global _core_manager

    if _core_manager is not None and _core_manager.is_initialized():
        logger.warning("Core systems already initialized")
        return _core_manager

    _core_manager = CoreSystemManager(
        config_path, performance_profile=performance_profile
    )
    success = await _core_manager.initialize()

    if not success:
        raise RuntimeError("Failed to initialize core systems")

    return _core_manager


async def shutdown_core_systems():
    """Shutdown core systems."""
    global _core_manager
    if _core_manager:
        await _core_manager.shutdown()
        _core_manager = None


def get_core_manager() -> Optional[CoreSystemManager]:
    """Get global CoreSystemManager instance."""
    return _core_manager


def is_core_initialized() -> bool:
    """Check core systems initialization."""
    return _core_manager is not None and _core_manager.is_initialized()
