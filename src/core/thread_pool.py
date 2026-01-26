"""
Unified Thread Pool для TOBS.

Централизованный thread pool для всех CPU-bound и I/O операций
в приложении. Устраняет контеншн между множественными локальными пулами.

Version: 1.0.0
Created: 2025-01-05 (TIER B - Task B-1)
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Optional

from loguru import logger


class TaskPriority(IntEnum):
    """Priority levels for task execution."""
    
    LOW = 0      # Low priority (e.g., deferred cleanup)
    NORMAL = 1   # Normal priority (default)
    HIGH = 2     # High priority (e.g., user-facing operations)


@dataclass(slots=True)
class ThreadPoolMetrics:
    """Metrics for monitoring thread pool usage."""
    
    active_threads: int = 0
    queue_size: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    avg_task_latency_ms: float = 0.0
    total_execution_time_s: float = 0.0
    
    # Task counters by priority
    high_priority_tasks: int = 0
    normal_priority_tasks: int = 0
    low_priority_tasks: int = 0


class UnifiedThreadPool:
    """
    Unified thread pool singleton for all CPU-bound operations.
    
    Replaces multiple local ThreadPoolExecutors with DUAL pools:
    - Critical pool (70%): HIGH priority tasks (I/O, user-facing)
    - Standard pool (30%): NORMAL/LOW priority tasks (FFmpeg, cleanup)
    
    This architecture prevents low-priority tasks from blocking critical operations.
    
    Example:
        pool = get_thread_pool()
        result = await pool.submit(cpu_bound_func, arg1, arg2, priority=TaskPriority.HIGH)
    """
    
    def __init__(self, max_workers: Optional[int] = None):
        """
        Initialize unified thread pool with priority separation.
        
        Args:
            max_workers: Maximum threads (None = auto-detect from CPU cores)
        """
        self._max_workers = max_workers or self._default_workers()
        
        # Split workers: 70% critical, 30% standard
        critical_workers = max(int(self._max_workers * 0.7), 2)
        standard_workers = max(self._max_workers - critical_workers, 1)
        
        self._critical_executor = ThreadPoolExecutor(
            max_workers=critical_workers,
            thread_name_prefix="tobs_critical"
        )
        self._standard_executor = ThreadPoolExecutor(
            max_workers=standard_workers,
            thread_name_prefix="tobs_standard"
        )
        
        # Metrics tracking
        self._metrics = ThreadPoolMetrics()
        self._task_start_times: dict[int, float] = {}
        self._task_counter = 0
        
        logger.info(
            f"🧵 UnifiedThreadPool initialized: {critical_workers} critical + "
            f"{standard_workers} standard = {self._max_workers} total workers"
        )
    
    def _default_workers(self) -> int:
        """
        Auto-tune worker count based on CPU cores, RAM, and workload type.
        
        Rules:
        - I/O-heavy workload: CPU cores * 1.5 (overlap I/O wait)
        - Memory constrained: Reduce to CPU cores (avoid thrashing)
        - Minimum: 4 workers (2 critical + 2 standard)
        
        Returns:
            Optimal worker count for mixed I/O + CPU workload
        """
        cpu_count = os.cpu_count() or 4
        
        # Check ENV override
        env_max = os.getenv("MAX_THREADS")
        if env_max:
            try:
                return int(env_max)
            except ValueError:
                logger.warning(f"Invalid MAX_THREADS={env_max}, using auto-detect")
        
        # Check available RAM
        try:
            import psutil
            mem = psutil.virtual_memory()
            available_gb = mem.available / (1024 ** 3)
            
            if available_gb < 4.0:
                # Low memory: conservative thread count
                optimal = cpu_count
                logger.warning(
                    f"⚠️ Low memory ({available_gb:.1f}GB free), "
                    f"using conservative {optimal} threads"
                )
                return max(optimal, 4)
        except ImportError:
            pass  # psutil not available, continue with default
        
        # Mixed I/O + CPU workload: CPU cores * 1.5
        optimal = int(cpu_count * 1.5)
        return max(optimal, 4)  # Minimum 4 workers
    
    async def submit(
        self,
        fn: Callable,
        *args,
        priority: TaskPriority = TaskPriority.NORMAL,
        **kwargs
    ) -> Any:
        """
        Submit task to thread pool with priority routing.
        
        HIGH priority tasks go to critical pool (70% of workers).
        NORMAL/LOW priority tasks go to standard pool (30% of workers).
        
        This prevents low-priority tasks (FFmpeg, cleanup) from blocking
        critical I/O operations (disk writes, network).
        
        Args:
            fn: Callable to execute in thread pool
            *args: Positional arguments for fn
            priority: Task priority (affects which pool is used)
            **kwargs: Keyword arguments for fn
            
        Returns:
            Result from fn execution
            
        Raises:
            Exception: Any exception raised by fn
        """
        task_id = self._task_counter
        self._task_counter += 1
        
        # Track metrics
        self._task_start_times[task_id] = time.time()
        self._update_priority_counter(priority)
        
        # Route to appropriate executor based on priority
        if priority == TaskPriority.HIGH:
            executor = self._critical_executor
        else:
            executor = self._standard_executor
        
        try:
            # Execute in thread pool
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                executor, 
                lambda: fn(*args, **kwargs)
            )
            
            # Update success metrics
            self._metrics.completed_tasks += 1
            self._update_latency(task_id)
            
            return result
            
        except Exception as e:
            # Update failure metrics
            self._metrics.failed_tasks += 1
            logger.error(f"Task {task_id} failed: {e}")
            raise
        finally:
            # Cleanup task tracking
            self._task_start_times.pop(task_id, None)
    
    def _update_priority_counter(self, priority: TaskPriority):
        """Update task counter for given priority."""
        if priority == TaskPriority.HIGH:
            self._metrics.high_priority_tasks += 1
        elif priority == TaskPriority.LOW:
            self._metrics.low_priority_tasks += 1
        else:
            self._metrics.normal_priority_tasks += 1
    
    def _update_latency(self, task_id: int):
        """Update average task latency."""
        start_time = self._task_start_times.get(task_id)
        if start_time:
            latency_ms = (time.time() - start_time) * 1000
            
            # Running average
            total = self._metrics.completed_tasks
            current_avg = self._metrics.avg_task_latency_ms
            self._metrics.avg_task_latency_ms = (
                (current_avg * (total - 1) + latency_ms) / total
            )
            
            # Update total execution time
            self._metrics.total_execution_time_s += latency_ms / 1000
    
    def get_metrics(self) -> ThreadPoolMetrics:
        """
        Get current thread pool metrics.
        
        Returns:
            ThreadPoolMetrics with current stats
        """
        # Update active threads count
        self._metrics.active_threads = len(self._task_start_times)
        
        # Estimate queue size (pending tasks in both pools)
        try:
            critical_queue = self._critical_executor._work_queue.qsize()
            standard_queue = self._standard_executor._work_queue.qsize()
            self._metrics.queue_size = critical_queue + standard_queue
        except AttributeError:
            # Fallback if _work_queue is not accessible
            self._metrics.queue_size = 0
        
        return self._metrics
    
    def shutdown(self, wait: bool = True):
        """
        Shutdown thread pools gracefully.
        
        Args:
            wait: Wait for all tasks to complete before shutdown
        """
        logger.info(f"🛑 Shutting down UnifiedThreadPool (wait={wait})...")
        
        try:
            # Shutdown both pools
            self._critical_executor.shutdown(wait=wait)
            self._standard_executor.shutdown(wait=wait)
            
            # Log final metrics
            metrics = self.get_metrics()
            logger.info(
                f"📊 Final metrics: "
                f"{metrics.completed_tasks} completed, "
                f"{metrics.failed_tasks} failed, "
                f"avg latency: {metrics.avg_task_latency_ms:.2f}ms"
            )
        except Exception as e:
            logger.error(f"Error during thread pool shutdown: {e}")


# Global singleton instance
_thread_pool: Optional[UnifiedThreadPool] = None


def get_thread_pool() -> UnifiedThreadPool:
    """
    Get or create global thread pool singleton.
    
    Returns:
        Global UnifiedThreadPool instance
    """
    global _thread_pool
    if _thread_pool is None:
        _thread_pool = UnifiedThreadPool()
    return _thread_pool


def shutdown_thread_pool(wait: bool = True):
    """
    Shutdown global thread pool.
    
    Args:
        wait: Wait for all tasks to complete
    """
    global _thread_pool
    if _thread_pool is not None:
        _thread_pool.shutdown(wait=wait)
        _thread_pool = None
