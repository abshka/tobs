# tests for native LogBatcher integration in Exporter
# Verifies that:
#  - Exporter creates a LogBatcher instance when available (native path)
#  - Exporter falls back to internal in-memory batching when LogBatcher import fails
#
# Tests use light-weight stubs for optional external dependencies so they are
# robust in environments where heavy deps (telethon, aiohttp, etc.) may be missing.
#
# Note: These are unit tests and intentionally avoid network / disk / Telethon usage.

import importlib
import os
import sys
import types
import logging
from types import SimpleNamespace
from pathlib import Path

import pytest


def _ensure_stub(module_name: str, attrs: dict | None = None):
    """
    Insert a minimal stub module into sys.modules if not present.
    Returns the module object.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]
    m = types.ModuleType(module_name)
    if attrs:
        for k, v in attrs.items():
            setattr(m, k, v)
    sys.modules[module_name] = m
    return m


@pytest.fixture(autouse=True)
def minimal_import_environment(monkeypatch):
    """
    Ensure a minimal set of optional modules are present so importing
    src.export.exporter succeeds even when heavy external deps are absent.
    """
    # Prevent background flush threads from starting during tests
    monkeypatch.setenv("LOG_BATCH_INTERVAL", "0")

    # Lightweight stubs for common optional dependencies that may not be installed
    _ensure_stub("psutil")
    _ensure_stub("aiofiles")
    _ensure_stub("aiohttp")

    # rich: provide print() proxy and a minimal rich.progress.Progress context manager
    rich = _ensure_stub("rich", {"print": lambda *a, **k: None})
    progress = types.ModuleType("rich.progress")

    class DummyProgress:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add_task(self, *args, **kwargs):
            return 1

        def update(self, *args, **kwargs):
            return None

    progress.Progress = DummyProgress
    progress.SpinnerColumn = type("SpinnerColumn", (), {})
    progress.TextColumn = type("TextColumn", (), {})
    progress.BarColumn = type("BarColumn", (), {})
    progress.TimeRemainingColumn = type("TimeRemainingColumn", (), {})
    sys.modules["rich.progress"] = progress

    # Telethon stubs (errors and a few TL function names) to avoid import errors
    tele = _ensure_stub("telethon")
    tele_errors = types.ModuleType("telethon.errors")
    tele_errors.RPCError = Exception
    sys.modules["telethon.errors"] = tele_errors
    tl = _ensure_stub("telethon.tl")
    funcs = types.ModuleType("telethon.tl.functions")
    funcs.InvokeWithTakeoutRequest = object
    sys.modules["telethon.tl.functions"] = funcs
    acct = types.ModuleType("telethon.tl.functions.account")
    acct.FinishTakeoutSessionRequest = object
    acct.InitTakeoutSessionRequest = object
    sys.modules["telethon.tl.functions.account"] = acct

    # Ensure our module stubs are available for import by the exporter module
    # Stop any global batcher background flusher and clear pending batches to make tests deterministic
    try:
        from src.logging.global_batcher import global_batcher
        # Stop periodic background flusher and flush current entries (best-effort)
        global_batcher.stop_background_flusher(flush_on_stop=True)
        global_batcher.flush()
    except Exception:
        # If global_batcher isn't available, ignore - tests will use fallback behavior
        pass

    yield

    # Teardown: no explicit cleanup required; monkeypatch will restore env


def test_exporter_creates_logbatcher_native(monkeypatch):
    """
    If LogBatcher is importable, Exporter.__init__ should create a per-exporter
    LogBatcher instance (and not rely on the adapter).
    """
    # Import Exporter after environment stubbing
    exporter_module = importlib.import_module("src.export.exporter")
    Exporter = exporter_module.Exporter

    # Minimal config object with required attributes used during construction
    class DummyConfig:
        def __init__(self):
            self.export_path = Path(".")
            self.performance = SimpleNamespace(workers=4)
            self.media_download = False
            self.async_media_download = False
            self.enable_shard_fetch = False

    # Create lightweight dependencies
    tm = SimpleNamespace()  # telegram_manager stub
    cache_manager = SimpleNamespace(set=lambda *a, **k: None)
    media_processor = SimpleNamespace()

    # Instantiate exporter (should attach LogBatcher)
    exp = Exporter(DummyConfig(), tm, cache_manager, media_processor)

    # Exporter should have a `log_batcher` attribute and expose lazy_log/flush via its public methods
    assert hasattr(exp, "log_batcher"), "Exporter must have 'log_batcher' attribute after native refactor"
    assert exp.log_batcher is not None, "log_batcher should be initialized when available"

    # LogBatcher should expose expected methods
    assert callable(getattr(exp.log_batcher, "lazy_log", None))
    assert callable(getattr(exp.log_batcher, "flush", None))

    # Ensure no leftover batches from previous tests and then test native batching
    exp.log_batcher.flush()
    exp._lazy_log("INFO", "test-message-native")
    # Internal representation in LogBatcher is a (LEVEL, MESSAGE) -> count mapping
    batches = getattr(exp.log_batcher, "_batches", None)
    assert batches is not None, "LogBatcher must expose a _batches dict for testing"
    # Check the tuple key exists with count 1
    assert batches.get(("INFO", "test-message-native")) == 1

    # Calling flush via exporter should delegate to LogBatcher and clear internal batches
    exp._flush_log_batch()
    assert not getattr(exp.log_batcher, "_batches"), "LogBatcher batches should be empty after flush"


def test_exporter_fallback_uses_internal_batching(monkeypatch, caplog):
    """
    Simulate LogBatcher import failure and assert Exporter falls back to immediate logging behavior.
    """
    # Ensure the src.logging.log_batcher module exists but lacks the LogBatcher symbol
    empty_mod = types.ModuleType("src.logging.log_batcher")
    monkeypatch.setitem(sys.modules, "src.logging.log_batcher", empty_mod)

    # Re-import exporter module (module already loaded; but Exporter imports LogBatcher inside __init__)
    exporter_module = importlib.import_module("src.export.exporter")
    Exporter = exporter_module.Exporter

    class DummyConfig:
        def __init__(self):
            self.export_path = Path(".")
            self.performance = SimpleNamespace(workers=4)
            self.media_download = False
            self.async_media_download = False
            self.enable_shard_fetch = False

    tm = SimpleNamespace()
    cache_manager = SimpleNamespace(set=lambda *a, **k: None)
    media_processor = SimpleNamespace()

    # Instantiate exporter: this time the attempt to import LogBatcher should fail and it should fall back
    exp = Exporter(DummyConfig(), tm, cache_manager, media_processor)

    # log_batcher should be None in fallback mode
    assert getattr(exp, "log_batcher", None) is None

    # Use fallback behavior: messages should be logged immediately at INFO level
    import logging as _logging
    caplog.set_level(_logging.INFO)

    exp._lazy_log("INFO", "fallback-one")
    assert any("fallback-one" in r.getMessage() for r in caplog.records), "INFO messages should be emitted immediately when no batcher is present"

    # ERROR should also be logged immediately
    exp._lazy_log("ERROR", "fallback-urgent")
    assert any("fallback-urgent" in r.getMessage() for r in caplog.records if r.levelno >= _logging.ERROR), "ERROR messages should be emitted immediately"

    # flush should be a no-op when no batcher is present (ensure no exception)
    exp._flush_log_batch()
