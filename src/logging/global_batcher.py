# -*- coding: utf-8 -*-
"""
Global batching helper for hot-path logging.

This module provides a single, process-wide `GlobalBatcher` meant to reduce
the CPU / I/O overhead caused by very frequent identical INFO/DEBUG/WARNING
logs in hot paths. Usage is intentionally simple:

    from src.logging.global_batcher import global_batcher

    global_batcher.lazy_log("INFO", "Processed 500 messages")
    # ... later (or periodically) ...
    global_batcher.flush()

Design goals:
- Thread-safe and safe to call from async contexts.
- Small memory footprint: aggregates identical (level, message) pairs.
- Background flusher (optional) that flushes periodically.
- Immediate emission for ERROR/CRITICAL levels.
- Simple metrics for monitoring (flush_count, total_logged, pending_count).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

try:
    # Prefer project logger if available
    from ..utils import logger as default_logger  # type: ignore
except Exception:  # pragma: no cover - fallback for test/minimal import environments
    default_logger = logging.getLogger("tobs.global_batcher")  # type: ignore

try:
    # Context prefix helper (optional)
    from .logging_context import get_context_prefix  # type: ignore
except Exception:  # pragma: no cover - fallback if logging_context not available

    def get_context_prefix() -> str:
        return ""


_LevelMessageKey = Tuple[str, str]


class GlobalBatcher:
    """
    Global batcher that aggregates repeated log messages.

    API:
      - lazy_log(level: str, message: str) -> None
      - flush() -> None
      - start_background_flusher(interval_s: float = None) -> None
      - stop_background_flusher(flush_on_stop: bool = True) -> None
      - get_metrics() -> Dict[str, Any]
      - set_logger(logger: logging.Logger) -> None
    """

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        flush_interval: float = 2.0,
        max_batch_count: Optional[int] = None,
    ):
        self._logger: logging.Logger = logger or default_logger
        self._flush_interval = float(flush_interval)
        self._max_batch_count = (
            int(max_batch_count) if max_batch_count is not None else None
        )

        # Internal aggregation: (LEVEL, MESSAGE) -> count
        self._batches: Dict[_LevelMessageKey, int] = {}
        self._lock = threading.Lock()

        # Background flusher control
        self._bg_stop = threading.Event()
        self._bg_thread: Optional[threading.Thread] = None

        # Metrics
        self._flush_count = 0
        self._total_logged = 0  # number of lazy_log calls (non-critical)
        self._last_flush_time: Optional[float] = None

        # Start background flusher if interval > 0
        if self._flush_interval and self._flush_interval > 0:
            self.start_background_flusher()

    # -------------------------
    # Public API
    # -------------------------
    def lazy_log(self, level: str, message: str) -> None:
        """
        Record a message for batched emission.

        - ERROR / CRITICAL messages are logged immediately.
        - INFO/DEBUG/WARNING messages are aggregated.
        """
        if not level:
            level = "INFO"
        lvl = level.upper()

        if lvl in ("ERROR", "CRITICAL"):
            # Immediately log with current context prefix
            prefix = get_context_prefix()
            self._log_immediate(lvl, f"{prefix}{message}")
            return

        # Aggregate
        key: _LevelMessageKey = (lvl, message)
        with self._lock:
            self._batches[key] = self._batches.get(key, 0) + 1
            self._total_logged += 1
            # If we exceed configured batch count threshold, flush eagerly
            if self._max_batch_count and len(self._batches) >= self._max_batch_count:
                # Call flush in a separate thread to avoid blocking caller
                t = threading.Thread(
                    target=self.flush, name="global-batcher-eager-flush", daemon=True
                )
                t.start()

    def flush(self) -> None:
        """
        Flush all batched messages synchronously.

        This will emit aggregated messages (adds multiplicity suffix when >1).
        """
        # Swap out batches under lock to minimize hold time
        with self._lock:
            batches = self._batches
            self._batches = {}

        if not batches:
            # Nothing to do
            return

        # Emit each aggregated entry
        for (level, message), count in batches.items():
            # Add multiplicity suffix when applicable
            out = f"{message} (×{count})" if count > 1 else message
            # Use current context prefix at flush time (best-effort)
            prefix = get_context_prefix()
            self._log_immediate(level, f"{prefix}{out}")

        # Update metrics
        self._flush_count += 1
        self._last_flush_time = time.time()

    def start_background_flusher(self, interval_s: Optional[float] = None) -> None:
        """
        Start a background thread that flushes periodically.

        If interval_s is not None, update the flush interval (seconds).
        Calling this repeatedly is idempotent.
        """
        if interval_s is not None:
            self._flush_interval = float(interval_s)

        if self._bg_thread is not None and self._bg_thread.is_alive():
            return

        self._bg_stop.clear()
        t = threading.Thread(
            target=self._background_flush_loop, name="global-log-batcher", daemon=True
        )
        self._bg_thread = t
        t.start()
        self._logger.debug(
            "GlobalBatcher background flusher started (interval=%.3fs)",
            self._flush_interval,
        )

    def stop_background_flusher(
        self, flush_on_stop: bool = True, timeout: float = 1.0
    ) -> None:
        """
        Stop the background flusher and optionally flush remaining messages.

        The call is idempotent.
        """
        if self._bg_thread is None:
            if flush_on_stop:
                # still flush any pending entries
                self.flush()
            return

        self._bg_stop.set()
        thread = self._bg_thread
        self._bg_thread = None
        thread.join(timeout=timeout)
        if flush_on_stop:
            try:
                self.flush()
            except Exception:
                # Best-effort
                self._logger.exception("GlobalBatcher: final flush raised an exception")

    def set_logger(self, logger: logging.Logger) -> None:
        """Replace the logger used for emissions."""
        self._logger = logger

    def set_flush_interval(self, interval_s: float) -> None:
        """Update the background flush interval (will restart flusher if running)."""
        restart = self._bg_thread is not None and self._bg_thread.is_alive()
        if restart:
            self.stop_background_flusher(flush_on_stop=False)
        self._flush_interval = float(interval_s)
        if restart:
            self.start_background_flusher()

    def set_max_batch_count(self, max_count: Optional[int]) -> None:
        """Set a threshold on number of distinct messages that triggers an eager flush."""
        self._max_batch_count = int(max_count) if max_count is not None else None

    def get_metrics(self) -> Dict[str, Any]:
        """Return simple metrics for monitoring."""
        with self._lock:
            pending = sum(self._batches.values())
            distinct = len(self._batches)
        return {
            "pending_messages": pending,
            "distinct_messages": distinct,
            "total_logged": self._total_logged,
            "flush_count": self._flush_count,
            "last_flush_time": self._last_flush_time,
            "flush_interval_s": self._flush_interval,
            "max_batch_count": self._max_batch_count,
        }

    # -------------------------
    # Internal helpers
    # -------------------------
    def _background_flush_loop(self) -> None:
        """Background thread that periodically flushes batches while not stopped."""
        interval = float(self._flush_interval)
        stop_event = self._bg_stop
        while not stop_event.wait(interval):
            try:
                # Only flush if something is pending to reduce noise
                with self._lock:
                    has_pending = bool(self._batches)
                if has_pending:
                    self.flush()
            except Exception:
                # Keep thread alive, but log the exception
                try:
                    self._logger.exception("GlobalBatcher background flusher exception")
                except Exception:
                    pass

    def _log_immediate(self, level: str, message: str) -> None:
        """Emit a single log message immediately at the given level."""
        lvl = (level or "INFO").upper()
        try:
            if lvl == "DEBUG":
                self._logger.debug(message)
            elif lvl == "INFO":
                self._logger.info(message)
            elif lvl == "WARNING":
                self._logger.warning(message)
            elif lvl == "ERROR":
                self._logger.error(message)
            elif lvl == "CRITICAL":
                self._logger.critical(message)
            else:
                # Default mapping
                self._logger.info(message)
        except Exception:
            # Suppress exceptions from logger to avoid affecting hot path
            try:
                self._logger.exception("GlobalBatcher: failed to log message")
            except Exception:
                pass

    # Context manager support
    def __enter__(self) -> "GlobalBatcher":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Stop background flusher and flush remaining messages
        self.stop_background_flusher(flush_on_stop=True)


# Module-level convenience singleton (respect LOG_BATCH_INTERVAL environment variable)
global_batcher = GlobalBatcher(
    flush_interval=float(os.getenv("LOG_BATCH_INTERVAL", "2.0"))
)


# Convenience wrappers (module-level functions)
def lazy_log(level: str, message: str) -> None:
    global_batcher.lazy_log(level, message)


def flush() -> None:
    global_batcher.flush()


def start_background_flusher(interval_s: Optional[float] = None) -> None:
    global_batcher.start_background_flusher(interval_s)


def stop_background_flusher(flush_on_stop: bool = True) -> None:
    global_batcher.stop_background_flusher(flush_on_stop)


def get_metrics() -> Dict[str, Any]:
    return global_batcher.get_metrics()


__all__ = [
    "GlobalBatcher",
    "global_batcher",
    "lazy_log",
    "flush",
    "start_background_flusher",
    "stop_background_flusher",
    "get_metrics",
]
