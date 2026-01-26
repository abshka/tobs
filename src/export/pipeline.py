# -*- coding: utf-8 -*-
"""
Async pipeline implementation for TOBS export stage (first-pass, robust, test-friendly).

Provides a simple 3-stage pipeline:
  fetch -> process -> write

Design goals for this first pass:
- Preserve message ordering (chronological) in the final write stage.
- Use bounded queues to provide backpressure.
- Keep a clear, testable API:
    AsyncPipeline(...).run(entity, telegram_manager, process_fn, writer_fn, limit=None)
- Failures in `process_fn` are recorded but do not stop the pipeline; failing messages are
  skipped in output while being counted in `errors`.
- Return a small stats dict including processed_count, errors and duration.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

# Prefer project logger if available; fall back to standard logging.
try:
    from ..utils import logger  # type: ignore
except Exception:  # pragma: no cover - fallback for minimal test environments
    import logging as _logging

    logger = _logging.getLogger(__name__)


ProcessFn = Callable[[Any], Awaitable[Any]]
WriterFn = Callable[[Any], Awaitable[None]]


class AsyncPipeline:
    """
    A compact asynchronous pipeline that fetches messages, processes them concurrently,
    and writes them in chronological order.

    Notes on guarantees:
    - The pipeline assigns sequence numbers (1-based) to fetched messages to preserve order.
    - For each fetched message there will be exactly one processed "result" placed into the
      process->write queue (either a success value returned by process_fn or a failure marker).
    - The writer will skip failed messages but will always advance ordering so it never deadlocks.
    """

    def __init__(
        self,
        *,
        fetch_workers: int = 1,
        process_workers: int = 4,
        write_workers: int = 1,
        fetch_queue_size: int = 64,
        process_queue_size: int = 256,
    ):
        # Stage parallelism
        self.fetch_workers = int(fetch_workers)
        self.process_workers = int(process_workers)
        self.write_workers = int(
            write_workers
        )  # currently we only use one writer to preserve ordering
        # Queue sizes (bounded for backpressure)
        self.fetch_queue_size = int(fetch_queue_size)
        self.process_queue_size = int(process_queue_size)

    async def run(
        self,
        entity: Any,
        telegram_manager: Any,
        process_fn: ProcessFn,
        writer_fn: WriterFn,
        limit: Optional[int] = None,
        min_id: int = 0,  # TIER B-4: Resume from this message ID
    ) -> Dict[str, Any]:
        """
        Run the pipeline for a single entity.

        Args:
            entity: Passed to telegram_manager.fetch_messages(entity, limit=limit, min_id=min_id).
            telegram_manager: Object with an async generator method `fetch_messages(entity, limit=..., min_id=...)`.
            process_fn: async callable: process_fn(message) -> processed_result (e.g., str or (str, media_count)).
            writer_fn: async callable: writer_fn(processed_result) -> None
            limit: Optional limit for fetching messages.
            min_id: Resume from this message ID (skip messages with id <= min_id). Default: 0 (start from beginning).

        Returns:
            stats dict: {'processed_count': int, 'errors': int, 'duration': float}
        """
        # 🚀 TIER C-4: Get metrics collector
        try:
            from ..monitoring import get_metrics_collector
            metrics = get_metrics_collector()
        except Exception:
            metrics = None  # Graceful degradation if monitoring not available
        
        fetch_q: asyncio.Queue = asyncio.Queue(maxsize=self.fetch_queue_size)
        write_q: asyncio.Queue = asyncio.Queue(maxsize=self.process_queue_size)

        # Stats and control
        processed_count = 0
        errors = 0
        last_seq = 0  # total number of messages fetched (set by fetcher)
        start_time = time.time()

        # Observability counters (max observed queue lengths)
        max_fetch_q = 0
        max_write_q = 0
        max_buffered = 0

        # Lightweight instrumentation timings (seconds, best-effort)
        # These accumulate approximate duration spent in each stage.
        fetch_time_total = 0.0
        process_time_total = 0.0
        write_time_total = 0.0

        # Internal helpers -------------------------------------------------
        async def _fetcher():
            nonlocal last_seq, max_fetch_q, fetch_time_total
            seq = 0
            fetch_start = time.time()
            try:
                # Use the standard fetch_messages interface with min_id for resume
                async for message in telegram_manager.fetch_messages(
                    entity, limit=limit, min_id=min_id  # TIER B-4: Skip already processed
                ):
                    # Check for graceful shutdown (TIER A - Task 3)
                    from src.shutdown_manager import shutdown_manager
                    if shutdown_manager.shutdown_requested:
                        logger.info("🛑 AsyncPipeline: shutdown requested, stopping fetch")
                        break
                    
                    seq += 1
                    # Backpressure: will await if queue is full
                    await fetch_q.put((seq, message))
                    # Track observed max queue size
                    if fetch_q.qsize() > max_fetch_q:
                        max_fetch_q = fetch_q.qsize()
                last_seq = seq
                fetch_time_total += time.time() - fetch_start
                logger.debug(
                    f"AsyncPipeline.fetcher: fetched {last_seq} messages (min_id={min_id}, max_fetch_queue={max_fetch_q}, fetch_time={fetch_time_total:.3f}s)"
                )
            except Exception as e:
                # Unexpected fetch-time exception - log and re-raise to fail the run
                logger.exception(f"AsyncPipeline.fetcher error: {e}")
                raise

        async def _processor_worker(worker_idx: int):
            nonlocal errors, max_write_q, process_time_total
            while True:
                # Check for graceful shutdown before getting next item
                from src.shutdown_manager import shutdown_manager
                if shutdown_manager.shutdown_requested:
                    logger.debug(f"Processor worker {worker_idx}: shutdown requested, exiting")
                    break
                    
                seq_msg = await fetch_q.get()
                try:
                    seq, message = seq_msg
                    if seq is None:
                        # sentinel received -> exit
                        fetch_q.task_done()
                        logger.debug("Processor received sentinel, exiting")
                        break

                    process_start = time.time()
                    try:
                        processed = await process_fn(message)
                        # Ensure a consistent representation - we leave it as returned
                        await write_q.put((seq, processed, None))
                        # Track observed write queue size
                        if write_q.qsize() > max_write_q:
                            max_write_q = write_q.qsize()
                    except Exception as e:
                        # Put a failure marker into write queue so writer can advance ordering
                        await write_q.put((seq, None, e))
                        # Also check write queue size on failure marker
                        if write_q.qsize() > max_write_q:
                            max_write_q = write_q.qsize()
                        errors += 1
                    finally:
                        # Record processing duration (best-effort across concurrent workers)
                        process_time_total += time.time() - process_start
                        fetch_q.task_done()
                except asyncio.CancelledError:
                    # Propagate cancellation
                    raise
                except Exception:
                    # Protect worker loop from crashing; record and continue
                    logger.exception("AsyncPipeline.processor_worker: unexpected error")
                    fetch_q.task_done()

        async def _writer():
            """
            Single writer that preserves ordering using sequence numbers.
            It buffers out-of-order processed items until the next expected sequence arrives.
            """
            nonlocal processed_count, max_buffered, write_time_total
            expected_seq = 1
            buffer: dict[int, Tuple[Optional[Any], Optional[Exception]]] = {}

            while True:
                item = await write_q.get()
                try:
                    seq, processed_value, exc = item
                    if seq is None:
                        # writer sentinel; break the loop after ensuring buffer flushed
                        write_q.task_done()
                        logger.debug("Writer received sentinel, finishing")
                        break

                    # Store by sequence
                    buffer[seq] = (processed_value, exc)
                    # Track the maximum buffered out-of-order items
                    if len(buffer) > max_buffered:
                        max_buffered = len(buffer)

                    # Flush in-order while possible
                    while expected_seq in buffer:
                        pv, pe = buffer.pop(expected_seq)
                        if pe is not None:
                            # processing failed for this seq: skip write but counted already in `errors`
                            logger.debug(
                                f"Writer skipping failed message seq={expected_seq}: {pe}"
                            )
                        elif pv is not None:
                            try:
                                writer_start = time.time()
                                await writer_fn(pv)
                                processed_count += 1
                                write_time_total += time.time() - writer_start
                            except Exception:
                                # A writer error is considered fatal for the write of that item;
                                # log and continue (we don't re-enqueue)
                                logger.exception(
                                    f"Writer function failed for seq={expected_seq}"
                                )
                        expected_seq += 1

                    write_q.task_done()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("AsyncPipeline.writer: unexpected error")
                    write_q.task_done()

        # Launch tasks
        tasks = []
        fetch_task = asyncio.create_task(_fetcher())
        tasks.append(fetch_task)

        proc_tasks = []
        for i in range(self.process_workers):
            t = asyncio.create_task(_processor_worker(i))
            proc_tasks.append(t)
            tasks.append(t)

        writer_task = asyncio.create_task(_writer())
        tasks.append(writer_task)

        # Orchestrate termination carefully
        try:
            # Wait for the fetcher to finish fetching all messages
            await fetch_task

            # Wait until processors have consumed all fetched items
            await fetch_q.join()

            # Send sentinel to processors so they exit cleanly
            for _ in range(self.process_workers):
                await fetch_q.put((None, None))

            # Wait for processors to exit
            await asyncio.gather(*proc_tasks, return_exceptions=False)

            # At this point all processors have placed processed results into write_q.
            # Wait until writer has processed all of them.
            await write_q.join()

            # Send a sentinel to writer to allow it to break its loop and exit
            await write_q.put((None, None, None))
            await writer_task

            duration = time.time() - start_time
            stats = {
                "processed_count": processed_count,
                "errors": errors,
                "duration": duration,
                "fetch_time": fetch_time_total,
                "process_time": process_time_total,
                "write_time": write_time_total,
                "max_fetch_queue_len": max_fetch_q,
                "max_write_queue_len": max_write_q,
                "max_writer_buffered": max_buffered,
            }
            # Convenience averages (safe guards)
            if processed_count > 0:
                stats["avg_process_time_per_message"] = (
                    process_time_total / processed_count
                )
                stats["avg_write_time_per_message"] = write_time_total / processed_count
            else:
                stats["avg_process_time_per_message"] = 0.0
                stats["avg_write_time_per_message"] = 0.0

            # 📊 TIER C-4: Record stage metrics
            if metrics:
                try:
                    # Record each pipeline stage
                    metrics.record_stage("pipeline_fetch", fetch_time_total, last_seq)
                    metrics.record_stage("pipeline_process", process_time_total, processed_count)
                    metrics.record_stage("pipeline_write", write_time_total, processed_count)
                except Exception as e:
                    logger.debug(f"Failed to record pipeline metrics: {e}")

            logger.debug(f"AsyncPipeline.completed: {stats}")
            return stats

        finally:
            # Ensure tasks are cancelled if any remain (safety net)
            for t in tasks:
                if not t.done():
                    t.cancel()
            # Best-effort gather to silence warnings
            await asyncio.gather(
                *[t for t in tasks if not t.done()], return_exceptions=True
            )
