import asyncio
from types import SimpleNamespace

import pytest

from src.config import Config, ExportTarget
from src.export import exporter as exporter_module
from src.export.exporter import Exporter


class FakeTelegramManager:
    """A minimal fake TelegramManager for testing exporter integration."""

    def __init__(self, messages):
        """
        messages: iterable of objects with attributes `id` and `text`.
        """
        self._messages = list(messages)

    async def resolve_entity(self, entity_id):
        # Return a simple object with `title` so exporter can form a filename
        return SimpleNamespace(title=f"Test {entity_id}")

    async def fetch_messages(self, entity, limit=None):
        """Yield messages in order (chronological)."""
        count = 0
        for m in self._messages:
            if limit is not None and count >= limit:
                break
            count += 1
            yield m


class DummyCacheManager:
    async def get(self, key):
        # Minimal entity state with attributes exporter expects
        return SimpleNamespace(processed_message_ids=set(), last_message_id=None)

    async def set(self, key, value):
        # No-op for tests
        self._last_set = (key, value)


class DummyMediaProcessor:
    # Not used directly in these tests because we stub the processor path,
    # but provided for Exporter construction.
    async def wait_for_downloads(self, timeout=None):
        return


class DummyNoteGenerator:
    pass


@pytest.mark.asyncio
async def test_exporter_uses_async_pipeline_when_enabled(monkeypatch, tmp_path):
    """
    When ASYNC_PIPELINE is enabled on the Config, Exporter should instantiate the
    pipeline and call AsyncPipeline.run. We monkeypatch `AsyncPipeline.run` to record calls.
    """
    # Create minimal Config (satisfy required api fields) and enable pipeline
    cfg = Config(api_id=1, api_hash="a" * 32)
    cfg.export_path = tmp_path
    cfg.async_pipeline_enabled = True
    cfg.async_pipeline_fetch_workers = 1
    cfg.async_pipeline_process_workers = 1
    cfg.async_pipeline_write_workers = 1

    # Simple messages for the fake telegram manager
    msgs = [SimpleNamespace(id=i, text=f"msg-{i}") for i in range(1, 4)]
    tm = FakeTelegramManager(msgs)
    cache_mgr = DummyCacheManager()
    media_proc = DummyMediaProcessor()
    note_gen = DummyNoteGenerator()
    perf = SimpleNamespace(
        last_sample_time=0,
        sample_resources=lambda: None,
        get_peak_memory=lambda: 0,
        get_avg_cpu=lambda: 0,
    )
    exporter = Exporter(
        cfg,
        tm,
        cache_mgr,
        media_proc,
        note_gen,
        http_session=None,
        performance_monitor=perf,
    )

    # Patch AsyncPipeline.run on the class imported by exporter module
    called = {"ran": False, "args": None, "kwargs": None}

    async def fake_run(self, *args, **kwargs):
        called["ran"] = True
        called["args"] = args
        called["kwargs"] = kwargs
        # Simulate some processing stats
        return {"processed_count": 0, "errors": 0, "duration": 0.0}

    monkeypatch.setattr(exporter_module.AsyncPipeline, "run", fake_run, raising=True)

    # Use a minimal ExportTarget and run the exporter method
    tgt = ExportTarget(id="123")
    await exporter._export_regular_target(tgt)

    assert called["ran"], (
        "AsyncPipeline.run should have been called when pipeline is enabled"
    )


@pytest.mark.asyncio
async def test_exporter_does_not_use_pipeline_when_disabled(monkeypatch, tmp_path):
    """
    When ASYNC_PIPELINE is disabled, the exporter should not call AsyncPipeline.run.
    We also stub out the Exporter._process_message_parallel to avoid complex I/O.
    """
    cfg = Config(api_id=1, api_hash="a" * 32)
    cfg.export_path = tmp_path
    cfg.async_pipeline_enabled = False  # explicitly disabled

    msgs = [SimpleNamespace(id=i, text=f"msg-{i}") for i in range(1, 4)]
    tm = FakeTelegramManager(msgs)
    cache_mgr = DummyCacheManager()
    media_proc = DummyMediaProcessor()
    note_gen = DummyNoteGenerator()
    perf = SimpleNamespace(
        last_sample_time=0,
        sample_resources=lambda: None,
        get_peak_memory=lambda: 0,
        get_avg_cpu=lambda: 0,
    )
    exporter = Exporter(
        cfg,
        tm,
        cache_mgr,
        media_proc,
        note_gen,
        http_session=None,
        performance_monitor=perf,
    )

    # Patch AsyncPipeline.run to detect accidental calls
    called = {"ran": False}

    async def fake_run(self, *args, **kwargs):
        called["ran"] = True
        return {"processed_count": 0, "errors": 0, "duration": 0.0}

    monkeypatch.setattr(exporter_module.AsyncPipeline, "run", fake_run, raising=True)

    # Stub out _process_message_parallel used by the fallback batch path to avoid real I/O
    async def fake_process(
        self, message, target, media_dir, output_dir, entity_reporter
    ):
        return (f"MSG {message.id}\n", message.id, False, 0)

    monkeypatch.setattr(
        Exporter, "_process_message_parallel", fake_process, raising=True
    )

    tgt = ExportTarget(id="456")
    await exporter._export_regular_target(tgt)

    assert not called["ran"], (
        "AsyncPipeline.run should NOT be called when pipeline is disabled"
    )


@pytest.mark.asyncio
async def test_exporter_pipeline_processes_messages_when_enabled(monkeypatch, tmp_path):
    """
    With async pipeline enabled, exporter should process messages end-to-end.
    """
    cfg = Config(api_id=1, api_hash="a" * 32)
    cfg.export_path = tmp_path
    cfg.async_pipeline_enabled = True
    cfg.async_pipeline_fetch_workers = 1
    cfg.async_pipeline_process_workers = 1
    cfg.async_pipeline_write_workers = 1

    msgs = [SimpleNamespace(id=i, text=f"msg-{i}") for i in range(1, 6)]
    tm = FakeTelegramManager(msgs)
    cache_mgr = DummyCacheManager()
    media_proc = DummyMediaProcessor()
    note_gen = DummyNoteGenerator()
    perf = SimpleNamespace(
        last_sample_time=0,
        sample_resources=lambda: None,
        get_peak_memory=lambda: 0,
        get_avg_cpu=lambda: 0,
    )

    exporter = Exporter(
        cfg,
        tm,
        cache_mgr,
        media_proc,
        note_gen,
        http_session=None,
        performance_monitor=perf,
    )

    # Stub _process_message_parallel to a simple, correct 4-tuple return
    async def fake_process(
        self, message, target, media_dir, output_dir, entity_reporter
    ):
        return (f"MSG {message.id}\n", message.id, False, 0)

    monkeypatch.setattr(
        Exporter, "_process_message_parallel", fake_process, raising=True
    )

    tgt = ExportTarget(id="789")
    await exporter._export_regular_target(tgt)

    assert exporter.statistics.messages_processed == len(msgs), (
        "Pipeline-enabled exporter should process all messages"
    )
    assert exporter.statistics.pipeline_stats.get("errors", 0) == 0
