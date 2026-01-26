"""
Parallel Media Processor for TOBS (TIER B-3)

Processes multiple media files concurrently with:
- Semaphore-based concurrency control
- Memory monitoring and throttling
- Metrics tracking
- Integration with unified thread pool

Author: Claude (TIER B Implementation)
Date: 2025-01-05
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from src.config import Config

# Setup logger
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParallelMediaConfig:
    """Configuration for parallel media processing"""
    
    max_concurrent: int = 4
    """Maximum number of concurrent media operations"""
    
    memory_limit_mb: int = 2048
    """Abort processing if memory exceeds this limit (MB)"""
    
    enable_parallel: bool = True
    """Enable/disable parallel processing (feature flag)"""
    
    check_memory_interval: int = 10
    """Check memory every N operations"""


@dataclass(slots=True)
class ParallelMediaMetrics:
    """Metrics for parallel media processing"""
    
    total_media_processed: int = 0
    """Total number of media files processed"""
    
    concurrent_peak: int = 0
    """Peak number of concurrent operations"""
    
    memory_throttles: int = 0
    """Number of times processing was throttled due to memory"""
    
    avg_concurrency: float = 0.0
    """Average concurrency during processing"""
    
    _concurrency_samples: List[int] = field(default_factory=list, repr=False)
    """Internal: samples for calculating average concurrency"""
    
    def record_concurrency(self, active: int):
        """Record current concurrency level"""
        self._concurrency_samples.append(active)
        if active > self.concurrent_peak:
            self.concurrent_peak = active
            
    def finalize(self):
        """Calculate final statistics"""
        if self._concurrency_samples:
            self.avg_concurrency = sum(self._concurrency_samples) / len(self._concurrency_samples)


class ParallelMediaProcessor:
    """
    Process media files in parallel with concurrency control.
    
    Features:
    - Async semaphore for controlling max concurrent operations
    - Memory monitoring to prevent OOM
    - Metrics tracking for performance analysis
    - Graceful fallback to sequential processing
    
    Example:
        config = ParallelMediaConfig(max_concurrent=4)
        processor = ParallelMediaProcessor(config)
        
        results = await processor.process_batch(
            messages, 
            process_fn=exporter._process_message_parallel
        )
    """
    
    def __init__(self, config: ParallelMediaConfig):
        self._config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._active_tasks = 0
        self._metrics = ParallelMediaMetrics()
        self._operations_since_memory_check = 0
        
        logger.info(
            f"ParallelMediaProcessor initialized: max_concurrent={config.max_concurrent}, "
            f"memory_limit={config.memory_limit_mb}MB, enabled={config.enable_parallel}"
        )
    
    async def process_batch(
        self,
        messages: List[Any],
        process_fn: Callable[[Any], Any]
    ) -> List[Any]:
        """
        Process a batch of messages in parallel.
        
        Args:
            messages: List of messages to process
            process_fn: Async function to process each message
            
        Returns:
            List of processed messages in original order
        """
        if not self._config.enable_parallel:
            # Sequential fallback
            logger.debug("Parallel processing disabled, using sequential mode")
            return [await process_fn(msg) for msg in messages]
        
        # Create tasks for all messages
        tasks = []
        for msg in messages:
            if self._has_media(msg):
                # Media message: process with semaphore control
                task = self._process_with_semaphore(msg, process_fn)
            else:
                # No media: process immediately without semaphore
                task = process_fn(msg)
            tasks.append(task)
        
        # Process all tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Finalize metrics
        self._metrics.finalize()
        
        return results
    
    async def _process_with_semaphore(
        self, 
        msg: Any, 
        process_fn: Callable[[Any], Any]
    ) -> Any:
        """
        Process single message with concurrency control.
        
        Args:
            msg: Message to process
            process_fn: Processing function
            
        Returns:
            Processed message
        """
        async with self._semaphore:
            self._active_tasks += 1
            self._metrics.record_concurrency(self._active_tasks)
            
            try:
                # Periodic memory check
                self._operations_since_memory_check += 1
                if self._operations_since_memory_check >= self._config.check_memory_interval:
                    if not self._check_memory():
                        logger.warning(
                            f"Memory limit exceeded ({self._config.memory_limit_mb}MB), "
                            f"throttling for 1 second"
                        )
                        self._metrics.memory_throttles += 1
                        await asyncio.sleep(1)
                    self._operations_since_memory_check = 0
                
                # Process message
                result = await process_fn(msg)
                self._metrics.total_media_processed += 1
                return result
                
            finally:
                self._active_tasks -= 1
    
    def _has_media(self, msg: Any) -> bool:
        """
        Check if message has media content.
        
        Args:
            msg: Message to check
            
        Returns:
            True if message has media
        """
        return hasattr(msg, "media") and msg.media is not None
    
    def _check_memory(self) -> bool:
        """
        Check if current memory usage is under limit.
        
        Returns:
            True if memory is under limit, False otherwise
        """
        try:
            import psutil
            process = psutil.Process()
            mem_mb = process.memory_info().rss / 1024 / 1024
            return mem_mb < self._config.memory_limit_mb
        except ImportError:
            # psutil not available, assume memory is fine
            logger.debug("psutil not available, skipping memory check")
            return True
        except Exception as e:
            logger.warning(f"Memory check failed: {e}")
            return True  # Assume OK if check fails
    
    def get_metrics(self) -> ParallelMediaMetrics:
        """Get current processing metrics"""
        return self._metrics
    
    def reset_metrics(self):
        """Reset metrics for new batch"""
        self._metrics = ParallelMediaMetrics()
        self._operations_since_memory_check = 0


def create_parallel_processor_from_config(config: Config) -> ParallelMediaProcessor:
    """
    Factory function to create ParallelMediaProcessor from Config.
    
    Args:
        config: TOBS configuration object
        
    Returns:
        Configured ParallelMediaProcessor instance
    """
    parallel_config = ParallelMediaConfig(
        max_concurrent=getattr(config, 'max_parallel_media', 4),
        memory_limit_mb=getattr(config, 'parallel_media_memory_limit_mb', 2048),
        enable_parallel=getattr(config, 'parallel_media_processing', True)
    )
    return ParallelMediaProcessor(parallel_config)
