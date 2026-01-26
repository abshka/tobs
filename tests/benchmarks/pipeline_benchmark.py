"""
Simple benchmark for the AsyncPipeline.

This benchmark is skipped by default in test runs. To run it manually:

    RUN_PIPELINE_BENCHMARK=1 pytest tests/benchmarks/pipeline_benchmark.py -q

Environment variables:
- RUN_PIPELINE_BENCHMARK=1  -> enable the benchmark
- PIPELINE_BENCH_N         -> number of messages to process (default: 2000)
- PIPELINE_PROCESS_WORKERS -> number of process workers used in pipeline (default: 4)

Note: This is intended as a local developer benchmark (not CI). Keep it lightweight
or skip it by default to avoid lengthy test runs.
"""

import os
import time
from types import SimpleNamespace
from typing import List

import pytest

from src.export.pipeline import AsyncPipeline

# Skip by default unless explicitly enabled
RUN_BENCH = os.getenv("RUN_PIPELINE_BENCHMARK", "0") == "1"


class FakeMessage:
    def __init__(self, msg_id: int, text: str = "x" * 40):
        self.id = msg_id
        self.text = text
        self.media = None
        self.sender = None


class FakeTelegramManager:
    def __init__(self, messages: List[FakeMessage], delay: float = 0.0):
        self._messages = list(messages)
        self._delay = delay

    async def fetch_messages(self, entity, limit=None):
        count = 0
        for m in self._messages:
            if limit is not None and count >= limit:
                break
            if self._delay:
                # optional artificial delay
                await asyncio.sleep(self._delay)
            count += 1
            yield m


@pytest.mark.skipif(
    not RUN_BENCH,
    reason="Benchmark disabled by default. Set RUN_PIPELINE_BENCHMARK=1 to run.",
)
@pytest.mark.asyncio
async def test_pipeline_throughput():
    """Benchmark pipeline throughput (prints results)."""
    import asyncio

    N = int(os.getenv("PIPELINE_BENCH_N", "2000"))
    proc_workers = int(os.getenv("PIPELINE_PROCESS_WORKERS", "4"))

    messages = [FakeMessage(i) for i in range(1, N + 1)]
    tm = FakeTelegramManager(messages)

    pipeline = AsyncPipeline(
        fetch_workers=1,
        process_workers=proc_workers,
        write_workers=1,
        fetch_queue_size=1024,
        process_queue_size=4096,
    )

    written = 0

    async def process_fn(message: FakeMessage):
        # Minimal processing: format a short line
        return (f"MSG {message.id}\\n", message.id, False, 0)

    async def writer_fn(result):
        nonlocal written
        # Writer does minimal work (no file I/O)
        written += 1

    # Run the pipeline benchmark
    t0 = time.monotonic()
    stats = await pipeline.run(
        entity="benchmark",
        telegram_manager=tm,
        process_fn=process_fn,
        writer_fn=writer_fn,
        limit=None,
    )
    t1 = time.monotonic()

    elapsed = t1 - t0
    msg_s = written / elapsed if elapsed > 0 else float("inf")

    print("PIPELINE BENCHMARK RESULTS")
    print(f"  Messages produced: {N}")
    print(f"  Messages written:  {written}")
    print(f"  Duration:          {elapsed:.3f}s")
    print(f"  Throughput:        {msg_s:.1f} msg/s")
    print(f"  Pipeline stats:    {stats}")

    # Baseline sequential processing (no concurrency) for comparison
    written_seq = 0
    t0_seq = time.monotonic()
    for m in messages:
        processed = await process_fn(m)
        await writer_fn(processed)
        written_seq += 1
    t1_seq = time.monotonic()
    elapsed_seq = t1_seq - t0_seq
    msg_s_seq = written_seq / elapsed_seq if elapsed_seq > 0 else float("inf")

    print("SEQUENTIAL (baseline) RESULTS")
    print(f"  Duration:   {elapsed_seq:.3f}s")
    print(f"  Throughput: {msg_s_seq:.1f} msg/s")
