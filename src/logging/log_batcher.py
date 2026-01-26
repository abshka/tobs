# -*- coding: utf-8 -*-
"""
LogBatcher: lightweight, thread-safe batching helper for hot-path logging.

Purpose:
- Reduce CPU and I/O overhead of very frequent identical INFO/DEBUG/WARNING logs
  by aggregating repeated messages and emitting them periodically (or on-demand).
- Preserve immediate semantics for ERROR/CRITICAL logs (log them immediately).
- Minimal, well-tested API:
    LogBatcher(logger: Optional[logging.Logger] = None, background_interval: Optional[float] = 2.0)
    - lazy_log(level: str, message: str) -> None
    - flush() -> None
    - start_background_flusher() -> None
    - stop_background_flusher() -> None

Design notes:
- Thread-safe via `threading.Lock()` — intended to be safe in mixed sync/async code paths.
- Background flusher is optional (disabled by default) and runs in a daemon thread.
- Batches are keyed by (level, message) tuples, so different levels are kept separate.
"""

from __future__ import annotations

import logging
import time
from threading import Event, Lock, Thread
from typing import Dict, Optional, Tuple


class LogBatcher:
    """
    Simple log batcher.

    Example:
        lb = LogBatcher()
        lb.lazy_log("INFO", "hello")
        lb.lazy_log("INFO", "hello")
        lb.flush()  # emits "hello (×2)"
    """

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        background_interval: Optional[float] = None,
    ):
        """
        Args:
            logger: Optional logger to use. Defaults to `logging.getLogger("tobs.log_batcher")`.
            background_interval: If a positive float is given, a background thread will flush
                                 batches every `background_interval` seconds. If None, no background flusher.
        """
        self.logger = logger or logging.getLogger("tobs.log_batcher")
        self._lock = Lock()
        # Map (LEVEL, MESSAGE) -> count
        self._batches: Dict[Tuple[str, str], int] = {}
        # Background flush control
        self._bg_interval = (
            float(background_interval) if background_interval is not None else None
        )
        self._bg_stop = Event()
        self._bg_thread: Optional[Thread] = None
        if self._bg_interval is not None and self._bg_interval > 0:
            self.start_background_flusher()

    def lazy_log(self, level: str, message: str) -> None:
        """
        Record a log message for eventual batched emission, or emit immediately for ERROR/CRITICAL.

        Args:
            level: Log level name (e.g., "INFO", "DEBUG", "ERROR", "CRITICAL").
            message: The log message (string). Use plain strings; formatting is not applied here.
        """
        if not level:
            level = "INFO"
        lvl = level.upper()

        if lvl in ("ERROR", "CRITICAL"):
            # Always emit immediately for high-severity messages
            if lvl == "ERROR":
                self.logger.error(message)
            else:
                self.logger.critical(message)
            return

        # Batch lower-severity messages
        key = (lvl, message)
        with self._lock:
            self._batches[key] = self._batches.get(key, 0) + 1

    def flush(self) -> None:
        """
        Emit all accumulated batched log messages and clear the internal buffer.
        For entries with count > 1 the emitted text is: "<message> (×N)".
        """
        # Swap map under lock to keep hold time short
        with self._lock:
            batches = self._batches
            self._batches = {}

        for (lvl, message), count in batches.items():
            if count > 1:
                out = f"{message} (×{count})"
            else:
                out = message

            # Dispatch according to level
            if lvl == "DEBUG":
                self.logger.debug(out)
            elif lvl == "INFO":
                self.logger.info(out)
            elif lvl == "WARNING":
                self.logger.warning(out)
            else:
                # For unknown level names, default to INFO
                self.logger.info(out)

    def _background_flush_loop(self) -> None:
        """Background thread target: periodically flush batches until stopped."""
        # This loop wakes up every 'bg_interval' seconds and flushes if there are items.
        interval = float(self._bg_interval)  # type: ignore
        stop_event = self._bg_stop
        while not stop_event.wait(interval):
            # Best-effort: flush but swallow exceptions to keep thread alive
            try:
                self.flush()
            except Exception:
                # Use logger.exception to surface unexpected errors
                try:
                    self.logger.exception(
                        "LogBatcher background flusher encountered an exception"
                    )
                except Exception:
                    # If logger itself fails, avoid infinite error loops
                    pass

    def start_background_flusher(self) -> None:
        """
        Start the background flusher thread if not already running.
        Safe to call multiple times (idempotent).
        """
        if self._bg_interval is None or self._bg_interval <= 0:
            return
        if self._bg_thread is not None and self._bg_thread.is_alive():
            return
        self._bg_stop.clear()
        t = Thread(
            target=self._background_flush_loop, name="logbatcher-flusher", daemon=True
        )
        self._bg_thread = t
        t.start()

    def stop_background_flusher(self, flush_on_stop: bool = True) -> None:
        """
        Stop the background flusher if running.

        Args:
            flush_on_stop: If True, perform a final flush before returning.
        """
        if self._bg_thread is None:
            # Nothing to do
            return
        self._bg_stop.set()
        thread = self._bg_thread
        self._bg_thread = None
        # Wait briefly for thread to exit; don't block forever
        thread.join(timeout=1.0)
        if flush_on_stop:
            try:
                self.flush()
            except Exception:
                # Best-effort
                try:
                    self.logger.exception(
                        "LogBatcher flush_on_stop raised an exception"
                    )
                except Exception:
                    pass

    def __enter__(self) -> "LogBatcher":
        # No-op; keep context management for convenience (ensures background flusher can be stopped)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Stop background thread on context exit
        try:
            self.stop_background_flusher(flush_on_stop=True)
        except Exception:
            # Suppress exceptions from shutdown
            pass


__all__ = ["LogBatcher"]
