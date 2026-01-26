# tests for the AsyncPipeline (TDD-first)
# These tests assert expected behavior of a 3-stage pipeline:
#  - ordering preservation (chronological)
#  - backpressure / stability under slow processing
#  - robustness when processing raises for single messages
#
# NOTE: These tests are intentionally written before the pipeline implementation.
# They assume an AsyncPipeline class that provides:
#     AsyncPipeline(**kwargs)
#     await pipeline.run(entity, telegram_manager, process_fn, writer_fn, limit=None)
#
# The `process_fn` is an async callable that takes (message) and returns either
# a `str` or a `(str, media_count)` tuple. The `writer_fn` is an async callable
# that accepts whatever `process_fn` returns.
#
# The tests are kept small and deterministic to avoid flaky timeouts.

import asyncio
import time
from typing import Any, Dict, List, Tuple

import pytest

# Import the pipeline under test (will fail until implemented - expected)
from src.export.pipeline import AsyncPipeline  # noqa: E402


class FakeMessage:
    """Minimal stand-in for a Telegram message object used in tests."""

    def __init__(self, msg_id: int, text: str = "", date: Any = None):
        self.id = msg_id
        self.text = text or f"msg-{msg_id}"
        self.date = date
        # Test hook: set this attribute to cause process_fn to raise
        self.raise_on_process = False
        # Minimal media placeholder
        self.media = None
        self.sender = None


class FakeTelegramManager:
    """Provides an async generator interface compatible with fetch_messages."""

    def __init__(self, messages: List[FakeMessage], delay_per_message: float = 0.0):
        self._messages = list(messages)
        self._delay = float(delay_per_message)

    async def fetch_messages(self, entity, limit: int = None, min_id: int = None):
        """Yield messages in the order provided (chronological)."""
        count = 0
        for m in self._messages:
            if limit is not None and count >= limit:
                break
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            count += 1
            yield m


async def _run_pipeline_and_collect(
    messages: List[FakeMessage],
    *,
    process_delay: float = 0.0,
    fetch_workers: int = 1,
    process_workers: int = 2,
    write_workers: int = 1,
    fetch_queue_size: int = 8,
    process_queue_size: int = 8,
    limit: int = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """
    Helper: runs the pipeline with a FakeTelegramManager and a simple process_fn/writer.
    Returns collected output lines and a stats dict returned by pipeline.run(...)
    """
    tm = FakeTelegramManager(messages)

    collected: List[str] = []
    # Stats about exceptions captured inside writer for extra assurance
    _errors: List[Exception] = []

    async def process_fn(message: FakeMessage):
        # Simulate a CPU-bound or IO-bound processing step
        if process_delay > 0:
            await asyncio.sleep(process_delay)
        if getattr(message, "raise_on_process", False):
            raise ValueError(f"Processing failed for message {message.id}")
        # Return tuple (formatted_text, media_count) to match exporter._process_message_parallel
        return (f"MSG {message.id}", 0)

    async def writer_fn(processed: Any):
        # Accept either (text, media_count) or str
        if isinstance(processed, tuple):
            text, _ = processed
        else:
            text = str(processed)
        collected.append(text)

    pipeline = AsyncPipeline(
        fetch_workers=fetch_workers,
        process_workers=process_workers,
        write_workers=write_workers,
        fetch_queue_size=fetch_queue_size,
        process_queue_size=process_queue_size,
    )

    # Run and return whatever the pipeline returns (expects dict-like stats)
    stats = await pipeline.run(
        entity="fake-entity",
        telegram_manager=tm,
        process_fn=process_fn,
        writer_fn=writer_fn,
        limit=limit,
    )
    # Add captured errors count to stats if not present (makes assertions robust)
    if "errors" not in stats:
        stats["errors"] = 0
    if "processed" not in stats and "processed_count" in stats:
        stats["processed"] = stats["processed_count"]
    return collected, stats


@pytest.mark.asyncio
async def test_pipeline_preserves_order():
    """
    Ensure messages are output in the same chronological order as produced by the fetcher.
    """
    messages = [FakeMessage(i) for i in range(1, 6)]
    collected, stats = await _run_pipeline_and_collect(
        messages,
        process_delay=0.0,
        fetch_workers=1,
        process_workers=2,
        write_workers=1,
        fetch_queue_size=4,
        process_queue_size=4,
    )

    expected = [f"MSG {i}" for i in range(1, 6)]
    assert collected == expected, f"Expected order {expected}, got {collected}"
    assert stats.get("processed", 0) == 5


@pytest.mark.asyncio
async def test_pipeline_backpressure():
    """
    When processing is intentionally slow and queue capacities are small, the pipeline
    must complete successfully and process all messages without unbounded memory growth.
    This test measures rough timing to ensure backpressure (i.e., total time should be >=
    total_work / process_workers).
    """
    messages = [FakeMessage(i) for i in range(1, 21)]
    # process_delay=0.01s * 20 messages = 0.2s total work
    process_delay = 0.01
    start = time.time()
    collected, stats = await _run_pipeline_and_collect(
        messages,
        process_delay=process_delay,
        fetch_workers=1,
        process_workers=2,
        write_workers=1,
        fetch_queue_size=2,  # intentionally small to exercise backpressure
        process_queue_size=2,
    )
    elapsed = time.time() - start

    assert len(collected) == 20
    # Minimal expected time (approx): total_work / process_workers
    expected_min = (len(messages) * process_delay) / max(1, 2)
    assert elapsed >= (expected_min * 0.9), (
        f"Elapsed {elapsed:.3f}s is unexpectedly low (expected >= {expected_min * 0.9:.3f}s). "
        "This likely indicates missing backpressure behavior."
    )


@pytest.mark.asyncio
async def test_pipeline_handles_processing_exceptions():
    """
    If processing of a single message raises, the pipeline should continue and finish
    processing other messages. The failing message should be counted in errors.
    """
    messages = [FakeMessage(i) for i in range(1, 6)]
    # Mark message id==3 to raise in process_fn
    messages[2].raise_on_process = True

    collected, stats = await _run_pipeline_and_collect(
        messages,
        process_delay=0.0,
        fetch_workers=1,
        process_workers=2,
        write_workers=1,
        fetch_queue_size=4,
        process_queue_size=4,
    )

    # The failing message (3) should not be present in the written output
    assert "MSG 3" not in collected
    assert len(collected) == 4, f"Expected 4 successful writes, got {len(collected)}"
    assert stats.get("errors", 0) >= 1, (
        "Pipeline should have recorded at least one processing error"
    )
