"""
Prefetch Batch Processor - Pipeline optimization for message export.

Implements producer-consumer pattern to overlap network fetching with message processing:
- Producer: Fetches batches from Telegram API in background
- Consumer: Processes batches while next batch is being fetched
- Result: Hides network latency behind processing time
"""

import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

from ..utils import logger


@dataclass
class PrefetchMetrics:
    """Metrics for prefetch performance analysis."""
    
    batches_fetched: int = 0
    batches_processed: int = 0
    queue_wait_time: float = 0.0  # Time spent waiting for batch (queue empty)
    queue_full_time: float = 0.0  # Time producer spent blocked (queue full)
    prefetch_enabled_time: float = 0.0  # Time prefetch was active
    
    def get_queue_utilization(self) -> float:
        """
        Calculate queue utilization (0-1).
        High utilization = good prefetch, low = processing faster than fetch.
        """
        if self.prefetch_enabled_time == 0:
            return 0.0
        return 1.0 - (self.queue_wait_time / self.prefetch_enabled_time)
    
    def get_efficiency(self) -> float:
        """
        Calculate prefetch efficiency (0-1).
        1.0 = perfect overlap, 0.0 = no benefit.
        """
        if self.batches_processed == 0:
            return 0.0
        return min(1.0, self.get_queue_utilization())


class PrefetchBatchProcessor:
    """
    Producer-consumer pipeline for batch prefetching.
    
    Architecture:
        Producer (background task):
            async for message in fetch_messages():
                batch.append(message)
                if len(batch) >= batch_size:
                    await queue.put(batch)  # May block if queue full
        
        Consumer (main loop):
            while True:
                batch = await processor.get_next_batch()  # May block if queue empty
                process_batch(batch)
    
    Configuration:
        - queue_size: Max batches in queue (default=2 for double-buffering)
        - batch_size: Messages per batch (default=100)
    """
    
    def __init__(
        self,
        batch_size: int = 100,
        queue_size: int = 2,
    ):
        """
        Initialize prefetch processor.
        
        Args:
            batch_size: Number of messages per batch
            queue_size: Max batches to prefetch (2 = double-buffering)
        """
        self.batch_size = batch_size
        self.queue_size = queue_size
        
        # Producer-consumer queue
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        
        # Background producer task
        self._producer_task: Optional[asyncio.Task] = None
        
        # Metrics
        self.metrics = PrefetchMetrics()
        
        # State tracking
        self._started = False
        self._stopped = False
        self._producer_done = False
        
        logger.info(
            f"🚀 PrefetchBatchProcessor initialized: "
            f"batch_size={batch_size}, queue_size={queue_size}"
        )
    
    async def start_producer(
        self,
        message_iterator: AsyncIterator,
        skip_condition=None,
    ):
        """
        Start background producer task.
        
        Args:
            message_iterator: Async iterator yielding messages
            skip_condition: Optional callable to skip messages (msg) -> bool
        """
        if self._started:
            logger.warning("Producer already started, ignoring")
            return
        
        self._started = True
        self.metrics.prefetch_enabled_time = time.time()
        
        async def producer():
            """Background task: fetch batches and queue them."""
            try:
                current_batch = []
                
                async for message in message_iterator:
                    # Check skip condition
                    if skip_condition and skip_condition(message):
                        continue
                    
                    current_batch.append(message)
                    
                    # When batch reaches target size, queue it
                    if len(current_batch) >= self.batch_size:
                        # Track time spent waiting for queue space
                        queue_start = time.time()
                        await self._queue.put(current_batch)
                        queue_wait = time.time() - queue_start
                        
                        if queue_wait > 0.1:  # Log if blocked >100ms
                            self.metrics.queue_full_time += queue_wait
                            logger.debug(
                                f"⏸️ Producer blocked {queue_wait:.2f}s "
                                f"(queue full, processing slow)"
                            )
                        
                        self.metrics.batches_fetched += 1
                        current_batch = []
                
                # Queue final partial batch
                if current_batch:
                    await self._queue.put(current_batch)
                    self.metrics.batches_fetched += 1
                
                # Signal completion with None sentinel
                await self._queue.put(None)
                self._producer_done = True
                
                logger.info(
                    f"✅ Producer finished: {self.metrics.batches_fetched} batches fetched"
                )
                
            except asyncio.CancelledError:
                logger.info("🛑 Producer cancelled")
                self._producer_done = True
                # Put sentinel to unblock consumer
                try:
                    await self._queue.put(None)
                except:
                    pass
                raise
            except Exception as e:
                logger.error(f"❌ Producer error: {e}")
                self._producer_done = True
                # Put sentinel
                try:
                    await self._queue.put(None)
                except:
                    pass
                raise
        
        # Start producer in background
        self._producer_task = asyncio.create_task(producer())
        logger.info("🚀 Producer task started")
    
    async def get_next_batch(self) -> Optional[List]:
        """
        Get next batch from queue (consumer side).
        
        Returns:
            List of messages, or None if producer finished
        """
        if not self._started:
            raise RuntimeError("Producer not started. Call start_producer() first.")
        
        # Track time waiting for batch
        wait_start = time.time()
        batch = await self._queue.get()
        wait_time = time.time() - wait_start
        
        # None sentinel = producer finished
        if batch is None:
            logger.info("🏁 Received completion signal from producer")
            return None
        
        # Track metrics
        self.metrics.batches_processed += 1
        
        if wait_time > 0.05:  # Log if waited >50ms
            self.metrics.queue_wait_time += wait_time
            logger.debug(
                f"⏳ Consumer waited {wait_time:.2f}s for batch "
                f"(queue empty, fetching slow)"
            )
        
        return batch
    
    async def stop(self):
        """Stop producer and clean up."""
        if self._stopped:
            return
        
        self._stopped = True
        
        # Cancel producer task if running
        if self._producer_task and not self._producer_task.done():
            self._producer_task.cancel()
            try:
                await self._producer_task
            except asyncio.CancelledError:
                pass
        
        # Drain queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        
        # Calculate final metrics
        if self.metrics.prefetch_enabled_time > 0:
            total_time = time.time() - self.metrics.prefetch_enabled_time
            self.metrics.prefetch_enabled_time = total_time
        
        logger.info(
            f"📊 Prefetch metrics: "
            f"fetched={self.metrics.batches_fetched}, "
            f"processed={self.metrics.batches_processed}, "
            f"utilization={self.metrics.get_queue_utilization():.1%}, "
            f"efficiency={self.metrics.get_efficiency():.1%}"
        )
        
        if self.metrics.queue_wait_time > 1.0:
            logger.warning(
                f"⚠️ Consumer waited {self.metrics.queue_wait_time:.1f}s total "
                f"(prefetch not keeping up with processing)"
            )
        
        if self.metrics.queue_full_time > 1.0:
            logger.warning(
                f"⚠️ Producer blocked {self.metrics.queue_full_time:.1f}s total "
                f"(processing not keeping up with prefetch)"
            )
