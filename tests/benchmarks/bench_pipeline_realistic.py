#!/usr/bin/env python3
"""
Benchmark: realistic AsyncPipeline workload

This script runs a synthetic but configurable workload against the project's
`AsyncPipeline` (src/export/pipeline.py) to get reproducible throughput numbers.

Usage (example):
  python tests/benchmarks/bench_pipeline_realistic.py --messages 20000 --process-workers 4 --process-delay 0.001

Design:
  - FakeTelegramManager yields N messages (configurable).
  - process_fn simulates CPU/IO by awaiting `process_delay`.
  - writer_fn simulates I/O by awaiting `writer_delay`.
  - Runs the pipeline once (or multiple repetitions) and prints metrics.
  - Optionally writes JSON results to a file for later analysis.

NOTE: This is a purely synthetic benchmark (no real network I/O or Telethon).
It is intended to compare relative effects of pipeline sizing / delays and to
exercise the pipeline under controlled, repeatable circumstances.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Local import - assumes this script is run from repo root
try:
    from src.export.pipeline import AsyncPipeline
except Exception:
    # Fallback: load pipeline.py directly to avoid importing package-level dependencies
    import importlib.util
    import sys
    import pathlib

    pipeline_path = pathlib.Path(__file__).resolve().parents[2] / "src" / "export" / "pipeline.py"
    spec = importlib.util.spec_from_file_location("bench_pipeline_module", str(pipeline_path))
    bench_pipeline_module = importlib.util.module_from_spec(spec)
    sys.modules["bench_pipeline_module"] = bench_pipeline_module
    spec.loader.exec_module(bench_pipeline_module)
    AsyncPipeline = getattr(bench_pipeline_module, "AsyncPipeline")
    sys.modules["bench_pipeline_module"] = bench_pipeline_module
    spec.loader.exec_module(bench_pipeline_module)
    AsyncPipeline = getattr(bench_pipeline_module, "AsyncPipeline")
except Exception as exc:  # pragma: no cover - runtime/CI dependency note
    raise SystemExit(
        "Failed to import AsyncPipeline. Make sure you run this script from the repository root "
        "and that project imports are resolvable. Original error: " + str(exc)
    )


@dataclass(slots=True)
class BenchResult:
    n_messages: int
    fetch_workers: int
    process_workers: int
    write_workers: int
    process_delay_s: float
    writer_delay_s: float
    duration_s: float
    processed_count: int
    errors: int
    throughput_msg_s: float
    pipeline_stats: Dict[str, Any]


class FakeMessage:
    """Lightweight fake message used for benchmarking."""

    __slots__ = ("id", "text", "media")

    def __init__(self, msg_id: int) -> None:
        self.id = msg_id
        self.text = f"msg-{msg_id}"
        self.media = None


class FakeTelegramManager:
    """Simple async generator that yields N messages in chronological order."""

    def __init__(self, n_messages: int, fetch_delay: float = 0.0) -> None:
        self._n = int(n_messages)
        self._fetch_delay = float(fetch_delay)

    async def fetch_messages(self, entity, limit: Optional[int] = None):
        count = 0
        for i in range(1, self._n + 1):
            # honor limit if set (None -> no limit)
            if limit is not None and count >= limit:
                break
            if self._fetch_delay > 0:
                await asyncio.sleep(self._fetch_delay)
            count += 1
            yield FakeMessage(i)


async def _run_once(
    n_messages: int,
    fetch_workers: int,
    process_workers: int,
    write_workers: int,
    fetch_delay: float,
    process_delay: float,
    writer_delay: float,
    limit: Optional[int] = None,
) -> BenchResult:
    """
    Run one benchmark round and return measured stats.
    """
    tm = FakeTelegramManager(n_messages, fetch_delay=fetch_delay)

    # Minimal process function that simulates work
    async def process_fn(message: FakeMessage):
        # Simulate work (IO or CPU-bound can be modeled by asyncio.sleep)
        if process_delay > 0:
            await asyncio.sleep(process_delay)
        # Return a simple string (writer will accept it)
        return f"MSG {message.id}"

    async def writer_fn(item: Any):
        # Simulate quick write latency (or disk I/O)
        if writer_delay > 0:
            await asyncio.sleep(writer_delay)
        # No actual I/O for benchmark (keeps it local)

    pipeline = AsyncPipeline(
        fetch_workers=max(1, int(fetch_workers)),
        process_workers=max(1, int(process_workers)),
        write_workers=max(1, int(write_workers)),
        # Use reasonable queue sizes for synthetic jobs
        fetch_queue_size=1024,
        process_queue_size=4096,
    )

    # Warmup: allow any lazy initialization in pipeline to settle
    start_wall = time.perf_counter()
    stats = await pipeline.run(
        entity=None,
        telegram_manager=tm,
        process_fn=process_fn,
        writer_fn=writer_fn,
        limit=limit,
    )
    duration = time.perf_counter() - start_wall

    # Normalize stats: pipeline implementation may return various keys.
    processed_count = stats.get("processed_count") or stats.get("processed") or 0
    errors = stats.get("errors", 0)

    throughput = processed_count / duration if duration > 0 else float("inf")

    return BenchResult(
        n_messages=n_messages,
        fetch_workers=fetch_workers,
        process_workers=process_workers,
        write_workers=write_workers,
        process_delay_s=process_delay,
        writer_delay_s=writer_delay,
        duration_s=duration,
        processed_count=processed_count,
        errors=errors,
        throughput_msg_s=throughput,
        pipeline_stats=dict(stats),
    )


def _format_bytes(n: int) -> str:
    # Human-friendly bytes formatting (not used now, but handy for future)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024.0:
            return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


def parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AsyncPipeline synthetic benchmark",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--messages",
        "-n",
        type=int,
        default=10000,
        help="Number of messages to generate",
    )
    p.add_argument(
        "--fetch-workers", type=int, default=1, help="Number of fetch workers"
    )
    p.add_argument(
        "--process-workers",
        type=int,
        default=4,
        help="Number of process workers (0=auto not supported in bench)",
    )
    p.add_argument(
        "--write-workers", type=int, default=1, help="Number of writer workers"
    )
    p.add_argument(
        "--fetch-delay",
        type=float,
        default=0.0,
        help="Per-message fetch delay (s) to simulate network latency",
    )
    p.add_argument(
        "--process-delay",
        type=float,
        default=0.001,
        help="Per-message process delay (s) to simulate work",
    )
    p.add_argument(
        "--writer-delay",
        type=float,
        default=0.0005,
        help="Per-message writer delay (s) to simulate I/O",
    )
    p.add_argument(
        "--runs", type=int, default=1, help="Repeat runs to compute stable numbers"
    )
    p.add_argument(
        "--warmup", type=int, default=0, help="Warmup runs (ignored in runs count)"
    )
    p.add_argument(
        "--json",
        "-j",
        type=str,
        default="",
        help="Path to write JSON output (appended as list)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of messages processed (0 = none, process all)",
    )
    return p.parse_args(argv)


async def main_async(argv) -> int:
    args = parse_args(argv)
    # ensure positive workers
    process_workers = max(1, args.process_workers)
    limit = args.limit or None

    results = []
    # Warmup runs
    for i in range(args.warmup):
        print(f"[warmup {i + 1}/{args.warmup}] running...")
        await _run_once(
            args.messages,
            args.fetch_workers,
            process_workers,
            args.write_workers,
            args.fetch_delay,
            args.process_delay,
            args.writer_delay,
            limit=limit,
        )

    for run_idx in range(args.runs):
        print(
            f"[run {run_idx + 1}/{args.runs}] messages={args.messages} fetch_workers={args.fetch_workers} process_workers={process_workers} process_delay={args.process_delay}s"
        )
        bench = await _run_once(
            args.messages,
            args.fetch_workers,
            process_workers,
            args.write_workers,
            args.fetch_delay,
            args.process_delay,
            args.writer_delay,
            limit=limit,
        )
        results.append(bench)
        print(
            f"  processed: {bench.processed_count}  errors: {bench.errors}  "
            f" duration: {bench.duration_s:.4f}s  throughput: {bench.throughput_msg_s:,.0f} msg/s"
        )
        # print a few pipeline internal stats if available
        ps = bench.pipeline_stats
        if ps:
            extra = []
            if "max_fetch_q" in ps:
                extra.append(f"max_fetch_q={ps.get('max_fetch_q')}")
            if "max_write_q" in ps:
                extra.append(f"max_write_q={ps.get('max_write_q')}")
            if "max_buffered" in ps:
                extra.append(f"max_buffered={ps.get('max_buffered')}")
            if extra:
                print("   pipeline:", ", ".join(extra))

    # Optionally persist JSON results
    if args.json:
        try:
            out_path = args.json
            json_list = []
            try:
                with open(out_path, "r", encoding="utf-8") as fh:
                    json_list = json.load(fh)
            except Exception:
                json_list = []
            # append results as serializable dicts
            for b in results:
                json_list.append(
                    {
                        "n_messages": b.n_messages,
                        "fetch_workers": b.fetch_workers,
                        "process_workers": b.process_workers,
                        "write_workers": b.write_workers,
                        "process_delay_s": b.process_delay_s,
                        "writer_delay_s": b.writer_delay_s,
                        "duration_s": b.duration_s,
                        "processed_count": b.processed_count,
                        "errors": b.errors,
                        "throughput_msg_s": b.throughput_msg_s,
                        "pipeline_stats": b.pipeline_stats,
                        "timestamp": time.time(),
                    }
                )
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(json_list, fh, indent=2)
            print(f"Saved JSON results to {out_path}")
        except Exception as e:
            print("Failed to write JSON results:", e, file=sys.stderr)

    # Print a small summary
    if results:
        # best/worst/median throughput
        throughputs = [
            r.throughput_msg_s for r in results if math.isfinite(r.throughput_msg_s)
        ]
        if throughputs:
            best = max(throughputs)
            worst = min(throughputs)
            med = sorted(throughputs)[len(throughputs) // 2]
            print(
                f"\nSummary: runs={len(throughputs)}  best={best:,.0f} msg/s  median={med:,.0f} msg/s  worst={worst:,.0f} msg/s"
            )

    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        return asyncio.run(main_async(argv))
    except KeyboardInterrupt:
        print("Benchmark interrupted by user")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
