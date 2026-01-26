#!/usr/bin/env python3
"""
Benchmark: Exporter end-to-end with AsyncPipeline enabled vs disabled.

This script runs a synthetic export workload using the project's Exporter and
compares total wall-clock time and derived throughput when the AsyncPipeline is
enabled vs when the exporter uses the legacy sequential/batch path.

Usage:
    python tobs/tests/benchmarks/bench_exporter_pipeline.py \
        --messages 5000 --runs 3 --process-workers 4 --process-delay 0.001 \
        --writer-delay 0.0005 --out results.json

Notes:
- This is a synthetic benchmark: Exporter internals that would otherwise
  perform real network or media work are stubbed (the exporter processing
  function is replaced with a lightweight sleep to emulate CPU/I/O cost).
- The script tries to be robust to missing dev dependencies, but it is
  expected to be run in the project's dev environment (so imports succeed).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import shutil
import statistics

# Ensure repository root is on sys.path so `import src.*` works when executing this file
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Provide lightweight stubs for optional system modules (psutil, aiofiles) to make the bench
# script self-contained in minimal environments
try:
    import psutil  # type: ignore
except Exception:

    class _StubVM:
        total = 8 * 1024**3
        # Provide an 'available' attribute to emulate psutil.virtual_memory().available
        available = total

    class _StubDU:
        free = 10 * 1024**3

    class _psutil_stub:
        @staticmethod
        def cpu_count():
            return 4

        @staticmethod
        def virtual_memory():
            return _StubVM()

        @staticmethod
        def disk_usage(path):
            return _StubDU()

    sys.modules["psutil"] = _psutil_stub()

# Provide a minimal aiofiles shim so the bench can run without installing aiofiles.
try:
    import aiofiles  # type: ignore
except Exception:
    import types

    class _FakeAiofilesOpen:
        def __init__(self, path, mode="r", encoding=None):
            self._path = path
            self._mode = mode
            self._encoding = encoding
            self._file = None

        async def __aenter__(self):
            # Open the underlying file synchronously but expose async methods on this wrapper.
            self._file = open(
                self._path,
                self._mode,
                encoding=self._encoding if "b" not in self._mode else None,
            )
            return self

        async def __aexit__(self, exc_type, exc, tb):
            try:
                if self._file:
                    self._file.close()
            except Exception:
                pass

        async def write(self, data):
            # Write synchronously to keep shim simple and deterministic for benchmarks.
            self._file.write(data)
            try:
                self._file.flush()
            except Exception:
                pass

        async def flush(self):
            try:
                if self._file:
                    self._file.flush()
            except Exception:
                pass

        async def close(self):
            """Close the underlying file synchronously inside an async method.

            The real `aiofiles` file objects provide an async `close` method that can
            be awaited; exporter logic awaits `self._file.close()` on exit. This
            async wrapper ensures that awaiting `.close()` works in the bench shim.
            """
            try:
                if self._file:
                    self._file.close()
            except Exception:
                pass

        def __await__(self):
            """Make this wrapper awaitable so `await aiofiles.open(...)` returns the wrapper.

            The awaited coroutine returns `self`, allowing code like:
                f = await aiofiles.open(path, mode='w')
                async with f: ...
            to behave as if aiofiles.open returned an awaitable context manager.
            """

            async def _return_self():
                return self

            return _return_self().__await__()

    def _aiofiles_open(path, mode="r", encoding=None):
        # Factory returning an awaitable wrapper instance.
        # The returned _FakeAiofilesOpen is awaitable via its __await__ method;
        # the actual opening of the underlying file is handled in __aenter__ so
        # that `async with (await aiofiles.open(...))` works correctly.
        return _FakeAiofilesOpen(path, mode=mode, encoding=encoding)

    aiofiles_mod = types.ModuleType("aiofiles")
    aiofiles_mod.open = _aiofiles_open

    # Provide a minimal aiofiles.os submodule to satisfy imports like `import aiofiles.os`
    aiofiles_os = types.ModuleType("aiofiles.os")
    import os as _os

    # Expose a small subset of os functionality used by the codebase
    aiofiles_os.makedirs = _os.makedirs
    aiofiles_os.remove = _os.remove
    aiofiles_os.path = _os.path
    aiofiles_os.rename = _os.rename
    aiofiles_os.replace = getattr(_os, "replace", _os.rename)
    aiofiles_os.stat = _os.stat
    aiofiles_os.listdir = _os.listdir

    # Attach os submodule to the aiofiles shim and register it in sys.modules
    aiofiles_mod.os = aiofiles_os

    sys.modules["aiofiles"] = aiofiles_mod
    sys.modules["aiofiles.os"] = aiofiles_os

# Minimal aiohttp stub for bench environments without aiohttp installed.
# Provides a `ClientSession` async context manager with lightweight `get`/`post` responses.
try:
    import aiohttp  # type: ignore
except Exception:
    import types

    class _FakeResponse:
        def __init__(self, status=200, content=b"", text_val=""):
            self.status = status
            self._content = content
            self._text = text_val

        async def read(self):
            return self._content

        async def text(self):
            return self._text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

    class _FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        async def get(self, *args, **kwargs):
            return _FakeResponse()

        async def post(self, *args, **kwargs):
            return _FakeResponse()

        async def close(self):
            return None

    aiohttp_stub = types.ModuleType("aiohttp")
    aiohttp_stub.ClientSession = _FakeClientSession
    sys.modules["aiohttp"] = aiohttp_stub

# Minimal bs4 (BeautifulSoup) stub for environments without bs4 installed.
# Provides `BeautifulSoup(markup, features=None)` with a very small `get_text` behavior
# sufficient for bench scripts that only need text extraction. Also provides a
# minimal `NavigableString` to satisfy imports that expect that symbol.
try:
    from bs4 import BeautifulSoup, NavigableString  # type: ignore
except Exception:

    class _SimpleSoup:
        def __init__(self, markup, features=None):
            self._markup = markup or ""

        def get_text(self, separator: str = "", strip: bool = False):
            text = self._markup
            if strip:
                text = text.strip()
            if separator:
                # Very naive tag removal: replace tags with separator
                import re

                text = re.sub(r"<[^>]+>", separator, text)
            else:
                import re

                text = re.sub(r"<[^>]+>", "", text)
            return text

    class _SimpleNavigableString(str):
        """Lightweight placeholder for bs4.NavigableString"""

        pass

    def _bs4_BeautifulSoup(markup, features=None):
        return _SimpleSoup(markup, features=features)

    import types as _types_bs4

    _bs4_mod = _types_bs4.ModuleType("bs4")
    _bs4_mod.BeautifulSoup = _bs4_BeautifulSoup
    _bs4_mod.NavigableString = _SimpleNavigableString
    sys.modules["bs4"] = _bs4_mod

    # Minimal loguru stub to allow `from loguru import logger` in bench environments
    # when the real dependency is not installed. Provides a small subset of loguru API
    # (logger.debug/info/warning/error/exception/critical, bind/add no-ops).
    try:
        from loguru import logger  # type: ignore
    except Exception:
        import logging as _logging
        import types as _types_loguru

        _loguru_mod = _types_loguru.ModuleType("loguru")

        class _LoguruLogger:
            def __init__(self):
                # Use standard logging under the hood for simplicity in test envs
                self._logger = _logging.getLogger("loguru_stub")
                # Ensure at least INFO level by default to avoid spam
                if not self._logger.handlers:
                    handler = _logging.StreamHandler()
                    handler.setFormatter(
                        _logging.Formatter("%(levelname)s: %(message)s")
                    )
                    self._logger.addHandler(handler)
                    self._logger.setLevel(_logging.INFO)

            def debug(self, msg, *args, **kwargs):
                try:
                    self._logger.debug(msg)
                except Exception:
                    pass

            def info(self, msg, *args, **kwargs):
                try:
                    self._logger.info(msg)
                except Exception:
                    pass

            def warning(self, msg, *args, **kwargs):
                try:
                    self._logger.warning(msg)
                except Exception:
                    pass

            def error(self, msg, *args, **kwargs):
                try:
                    self._logger.error(msg)
                except Exception:
                    pass

            def exception(self, msg, *args, **kwargs):
                try:
                    self._logger.exception(msg)
                except Exception:
                    try:
                        self._logger.error(msg)
                    except Exception:
                        pass

            def critical(self, msg, *args, **kwargs):
                try:
                    self._logger.critical(msg)
                except Exception:
                    pass

            # Minimal no-op compatibility methods commonly used with loguru
            def bind(self, *args, **kwargs):
                return self

            def add(self, *args, **kwargs):
                return None

        _loguru_mod.logger = _LoguruLogger()
        sys.modules["loguru"] = _loguru_mod

# Ensure `rich` is available with a minimal API if not installed to keep the bench script robust.
try:
    from rich import print as rprint  # type: ignore
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeRemainingColumn,
    )  # type: ignore
except Exception:
    import sys as _sys
    import types as _types

    # Minimal `rich` module shim that provides `print` and a small set of submodules
    def _rprint(*args, **kwargs):
        # Simple fallback to builtin print; keep signature minimal.
        print(*args, **kwargs)

    _rich_mod = _types.ModuleType("rich")
    _rich_mod.print = _rprint

    # Minimal progress module (used by exporter and progress helpers)
    _progress_mod = _types.ModuleType("rich.progress")

    class _DummyProgress:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def add_task(self, *args, **kwargs):
            return 1

        def update(self, *args, **kwargs):
            return None

    class _SpinnerColumn:
        pass

    class _TextColumn:
        def __init__(self, *a, **k):
            pass

    class _BarColumn:
        pass

    class _TimeRemainingColumn:
        pass

    _progress_mod.Progress = _DummyProgress
    _progress_mod.SpinnerColumn = _SpinnerColumn
    _progress_mod.TextColumn = _TextColumn
    _progress_mod.BarColumn = _BarColumn
    _progress_mod.TimeRemainingColumn = _TimeRemainingColumn

    # Minimal panel submodule
    _panel_mod = _types.ModuleType("rich.panel")

    class Panel:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __str__(self):
            return "Panel"

    _panel_mod.Panel = Panel

    # Minimal console submodule
    _console_mod = _types.ModuleType("rich.console")

    class Console:
        def __init__(self, *args, **kwargs):
            pass

        def print(self, *args, **kwargs):
            _rprint(*args, **kwargs)

        def input(self, *args, **kwargs):
            # Minimal blocking input fallback (shouldn't be used in bench runs)
            return input(*args, **kwargs)

    _console_mod.Console = Console

    # Minimal prompt submodule
    _prompt_mod = _types.ModuleType("rich.prompt")

    class Confirm:
        def __init__(self, *args, default=False, **kwargs):
            self.default = default

        def ask(self, *args, **kwargs):
            return bool(self.default)

    class IntPrompt:
        def __init__(self, *args, default=0, **kwargs):
            self.default = int(default)

        def ask(self, *args, **kwargs):
            return int(self.default)

    class Prompt:
        def __init__(self, *args, default="", **kwargs):
            self.default = default

        def ask(self, *args, **kwargs):
            return str(self.default)

    _prompt_mod.Confirm = Confirm
    _prompt_mod.IntPrompt = IntPrompt
    _prompt_mod.Prompt = Prompt

    # Minimal table submodule
    _table_mod = _types.ModuleType("rich.table")

    class Table:
        def __init__(self, *args, **kwargs):
            self.columns = []
            self.rows = []

        def add_column(self, *args, **kwargs):
            self.columns.append(args)

        def add_row(self, *row):
            self.rows.append(row)

        def __str__(self):
            return "Table"

    _table_mod.Table = Table

    # Minimal text submodule
    _text_mod = _types.ModuleType("rich.text")

    class Text:
        def __init__(self, s, *a, **kw):
            self.s = s

        def __str__(self):
            return str(self.s)

    _text_mod.Text = Text

    # Register modules
    _sys.modules["rich"] = _rich_mod
    _sys.modules["rich.progress"] = _progress_mod
    _sys.modules["rich.panel"] = _panel_mod
    _sys.modules["rich.console"] = _console_mod
    _sys.modules["rich.prompt"] = _prompt_mod
    _sys.modules["rich.table"] = _table_mod
    _sys.modules["rich.text"] = _text_mod

    # Expose local names as if they were imported
    rprint = _rich_mod.print
    Progress = _progress_mod.Progress
    SpinnerColumn = _progress_mod.SpinnerColumn
    TextColumn = _progress_mod.TextColumn
    BarColumn = _progress_mod.BarColumn
    TimeRemainingColumn = _progress_mod.TimeRemainingColumn
    Panel = _panel_mod.Panel
    Console = _console_mod.Console
    Confirm = _prompt_mod.Confirm
    IntPrompt = _prompt_mod.IntPrompt
    Prompt = _prompt_mod.Prompt
    Table = _table_mod.Table
    Text = _text_mod.Text

# Ensure `orjson` is available (use a lightweight shim if not installed).
try:
    import orjson  # type: ignore
except Exception:
    import json as _json_builtin

    class _OrjsonStub:
        OPT_INDENT_2 = None

        @staticmethod
        def dumps(obj, option=None):
            # Return bytes for compatibility with orjson.dumps
            return _json_builtin.dumps(obj, ensure_ascii=False).encode("utf-8")

        @staticmethod
        def loads(b):
            if isinstance(b, (bytes, bytearray)):
                return _json_builtin.loads(b.decode("utf-8"))
            return _json_builtin.loads(b)

    import types as _types_orjson

    _orjson_mod = _types_orjson.ModuleType("orjson")
    _orjson_mod.dumps = _OrjsonStub.dumps
    _orjson_mod.loads = _OrjsonStub.loads
    _orjson_mod.OPT_INDENT_2 = None
    import sys as _sys_orjson

    _sys_orjson.modules["orjson"] = _orjson_mod

# Minimal telethon stub to allow imports for benchmarking without telethon installed
try:
    import telethon  # type: ignore
except Exception:
    import types as _types_tel

    tele = _types_tel.ModuleType("telethon")

    # telethon.errors stub
    errors_mod = _types_tel.ModuleType("telethon.errors")

    class RPCError(Exception):
        pass

    class FloodWaitError(Exception):
        pass

    class SlowModeWaitError(Exception):
        pass

    class TimeoutError(Exception):
        pass

    errors_mod.RPCError = RPCError
    errors_mod.FloodWaitError = FloodWaitError
    errors_mod.SlowModeWaitError = SlowModeWaitError
    errors_mod.TimeoutError = TimeoutError
    import sys as _sys_tel

    _sys_tel.modules["telethon.errors"] = errors_mod
    tele.errors = errors_mod

    # telethon.utils stub
    utils_mod = _types_tel.ModuleType("telethon.utils")
    _sys_tel.modules["telethon.utils"] = utils_mod
    tele.utils = utils_mod

    # telethon.tl.functions and account stubs
    funcs_mod = _types_tel.ModuleType("telethon.tl.functions")
    funcs_mod.InvokeWithTakeoutRequest = object

    # Additional function submodule stubs commonly used across the codebase
    channels_mod = _types_tel.ModuleType("telethon.tl.functions.channels")
    channels_mod.GetFullChannelRequest = object
    channels_mod.GetParticipantsRequest = object
    channels_mod.InviteToChannelRequest = object
    _sys_tel.modules["telethon.tl.functions.channels"] = channels_mod

    messages_mod = _types_tel.ModuleType("telethon.tl.functions.messages")
    messages_mod.GetHistoryRequest = object
    messages_mod.GetMessagesRequest = object
    messages_mod.GetMessageRequest = object
    _sys_tel.modules["telethon.tl.functions.messages"] = messages_mod

    users_mod = _types_tel.ModuleType("telethon.tl.functions.users")
    users_mod.GetUsersRequest = object
    _sys_tel.modules["telethon.tl.functions.users"] = users_mod

    _sys_tel.modules["telethon.tl.functions"] = funcs_mod

    acct_mod = _types_tel.ModuleType("telethon.tl.functions.account")
    acct_mod.FinishTakeoutSessionRequest = object
    acct_mod.InitTakeoutSessionRequest = object
    _sys_tel.modules["telethon.tl.functions.account"] = acct_mod

    # telethon.tl.types stub (Message and document attribute types)
    types_mod = _types_tel.ModuleType("telethon.tl.types")

    class Message:
        pass

    class MessageMediaDocument:
        def __init__(self, document=None):
            # Minimal shape: expects .document with .attributes list
            # Use SimpleNamespace(attributes=[]) if not provided
            self.document = (
                document if document is not None else SimpleNamespace(attributes=[])
            )

    class MessageMediaPhoto:
        def __init__(self, photo=None):
            # Minimal placeholder for photo objects
            self.photo = photo if photo is not None else SimpleNamespace()

    class DocumentAttributeAudio:
        def __init__(self, *, voice: bool = False):
            # minimal attribute shape used by is_voice_message and note logic
            self.voice = voice

    class DocumentAttributeVideo:
        def __init__(self, *, duration: int = 0):
            # minimal attribute shape used by media helpers
            self.duration = duration

    class User:
        def __init__(
            self, id=None, first_name=None, last_name=None, username=None, title=None
        ):
            self.id = id
            self.first_name = first_name
            self.last_name = last_name
            self.username = username
            self.title = title

    class Channel:
        def __init__(self, id=None, title=None, username=None):
            # Minimal channel-like object used by parts of the codebase
            self.id = id
            self.title = title
            self.username = username

    class Peer:
        def __init__(self, peer_id=None, peer_type=None):
            # Lightweight peer placeholder used where code expects generic peers
            self.peer_id = peer_id
            self.peer_type = peer_type

    types_mod.Message = Message
    types_mod.MessageMediaDocument = MessageMediaDocument
    types_mod.MessageMediaPhoto = MessageMediaPhoto
    types_mod.DocumentAttributeAudio = DocumentAttributeAudio
    types_mod.DocumentAttributeVideo = DocumentAttributeVideo
    types_mod.User = User
    types_mod.Channel = Channel
    types_mod.Peer = Peer
    _sys_tel.modules["telethon.tl.types"] = types_mod
    # Expose types at the top-level for `from telethon import types` imports
    tele.types = types_mod
    _sys_tel.modules["telethon.types"] = types_mod

    # wire up minimal package
    tele.tl = _types_tel.ModuleType("telethon.tl")
    tele.tl.functions = funcs_mod
    tele.tl.types = types_mod
    _sys_tel.modules["telethon"] = tele

    class TelegramClient:
        """Minimal no-op TelegramClient stub for bench environment.

        This stub provides the small subset of the real TelegramClient API that
        project modules reference during imports and basic operations. All methods
        are intentionally no-ops or lightweight coroutines so the bench can run in
        an environment without the real telethon dependency.
        """

        def __init__(self, *args, **kwargs):
            # Accepts same constructor signature but does not create a real client.
            self._connected = False

        async def start(self, *args, **kwargs):
            self._connected = True
            return None

        async def connect(self, *args, **kwargs):
            self._connected = True
            return None

        async def disconnect(self, *args, **kwargs):
            self._connected = False
            return None

        def add_event_handler(self, *args, **kwargs):
            # No-op for benching
            return None

        def remove_event_handler(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    # Expose the stub as telethon.TelegramClient for normal imports
    tele.TelegramClient = TelegramClient
    _sys_tel.modules["telethon"] = tele

    # Minimal ffmpeg stub for bench environment: provide input()/output()/run() minimal API
    try:
        import ffmpeg  # type: ignore
    except Exception:
        import types as _types_ff

        def _ff_input(*args, **kwargs):
            class _Node:
                def output(self, *a, **k):
                    return self

                def run(self, *a, **k):
                    # Emulate subprocess output (stdout, stderr)
                    return (b"", b"")

            return _Node()

        ffmod = _types_ff.ModuleType("ffmpeg")
        ffmod.input = _ff_input
        ffmod.output = lambda *a, **k: _ff_input()
        ffmod.run = lambda *a, **k: (b"", b"")
        sys.modules["ffmpeg"] = ffmod

        # Minimal Pillow (PIL) stub for environments without Pillow installed.
        # Some project modules import `Image` and `ImageOps` from PIL; provide a tiny
        # shim that offers `open`, `save`, `convert`, `resize` and a noop ImageOps.
        try:
            from PIL import Image, ImageOps  # pragma: no cover
        except Exception:
            import types as _types_pil

            class _FakeImage:
                @staticmethod
                def open(path):
                    class _Img:
                        def save(self, *a, **k):
                            # no-op save for synthetic bench environment
                            return None

                        def convert(self, *a, **k):
                            return self

                        def resize(self, *a, **k):
                            return self

                    return _Img()

            def _noop_imageop(img, *a, **kw):
                return img

            _pil_mod = _types_pil.ModuleType("PIL")
            _pil_mod.Image = _FakeImage
            _pil_mod.ImageOps = _types_pil.ModuleType("PIL.ImageOps")
            _pil_mod.ImageOps.fit = _noop_imageop
            _pil_mod.ImageOps.resize = _noop_imageop

            import sys as _sys_pil

            _sys_pil.modules["PIL"] = _pil_mod
            _sys_pil.modules["PIL.Image"] = _pil_mod.Image
            _sys_pil.modules["PIL.ImageOps"] = _pil_mod.ImageOps

    # Try to import project modules; if imports fail, provide a clear error.
    try:
        from src.config import Config, ExportTarget
        from src.export.exporter import AsyncBufferedSaver, Exporter
    except Exception as exc:  # pragma: no cover - raise friendly
        raise SystemExit(
            "Failed to import project modules. Run this script from the repository root "
            "with the project's dev dependencies installed. Original error: " + str(exc)
        )


class FakeMessage:
    def __init__(self, mid: int, text: str = None):
        self.id = mid
        self.text = text or f"msg-{mid}"
        self.media = None
        self.sender = None


class FakeTelegramManager:
    """Simple fake Telegram manager with an async fetch_messages generator."""

    def __init__(self, messages: List[FakeMessage], fetch_delay: float = 0.0):
        self._messages = list(messages)
        self._delay = float(fetch_delay)

    async def resolve_entity(self, entity_id):
        # Minimal entity representation
        return SimpleNamespace(title=f"Bench {entity_id}")

    async def fetch_messages(self, entity, limit: Optional[int] = None):
        count = 0
        for m in self._messages:
            if limit is not None and count >= limit:
                break
            count += 1
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            yield m


class DummyCacheManager:
    async def get(self, key):
        return SimpleNamespace(processed_message_ids=set(), last_message_id=None)

    async def set(self, key, value):
        # no-op for benchmarks
        self._last_set = (key, value)


class DummyMediaProcessor:
    async def wait_for_downloads(self, timeout=None):
        return


class DummyNoteGenerator:
    pass


async def _run_single_export(
    *,
    messages: int,
    export_dir: Path,
    process_delay: float,
    writer_delay: float,
    process_workers: int,
    async_pipeline_enabled: bool,
) -> Dict[str, Any]:
    """
    Run a single exporter export and return collected metrics.
    """

    # Prepare config
    cfg = Config(api_id=1, api_hash="a" * 32)
    cfg.export_path = export_dir
    cfg.async_pipeline_enabled = bool(async_pipeline_enabled)
    cfg.async_pipeline_fetch_workers = 1
    cfg.async_pipeline_process_workers = process_workers
    cfg.async_pipeline_write_workers = 1
    # Ensure exporter won't use takeout or heavy features
    cfg.use_takeout = False

    # Create test objects
    msgs = [FakeMessage(i) for i in range(1, messages + 1)]
    tm = FakeTelegramManager(msgs)
    cache_mgr = DummyCacheManager()
    media_proc = DummyMediaProcessor()
    note_gen = DummyNoteGenerator()
    perf = SimpleNamespace(
        last_sample_time=0,
        memory_samples=[],
    )

    def sample_resources():
        perf.last_sample_time = time.time()
        try:
            perf.memory_samples.append(perf.get_peak_memory())
        except Exception:
            # best-effort: keep list stable even if get_peak_memory fails
            perf.memory_samples.append(0)

    perf.sample_resources = sample_resources
    perf.get_peak_memory = lambda: 0
    perf.get_avg_cpu = lambda: 0

    exporter = Exporter(
        cfg,
        tm,
        cache_mgr,
        media_proc,
        note_gen,
        http_session=None,
        performance_monitor=perf,
    )

    # Patch processing function to simulate the actual processing latency (and to avoid real I/O)
    orig_process = getattr(Exporter, "_process_message_parallel", None)

    async def fake_process(
        self, message, target, media_dir, output_dir, entity_reporter
    ):
        # Simulate processing latency
        await asyncio.sleep(process_delay)
        # Return tuple expected by Exporter writer
        return (f"MSG {message.id}\n", message.id, False, 0)

    Exporter._process_message_parallel = fake_process

    # Patch AsyncBufferedSaver to use an in-memory async writer (avoid disk I/O in bench)
    orig_write = getattr(AsyncBufferedSaver, "write", None)
    orig_aenter = getattr(AsyncBufferedSaver, "__aenter__", None)
    orig_aexit = getattr(AsyncBufferedSaver, "__aexit__", None)

    class _InMemoryWriter:
        def __init__(self):
            self._buf = []

        async def write(self, data):
            if writer_delay > 0:
                await asyncio.sleep(writer_delay)
            # store to in-memory buffer to simulate writes
            self._buf.append(data)
            return None

        async def flush(self):
            # no-op for in-memory buffer
            return None

        async def close(self):
            # no-op
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _fake_aenter(self):
        # Return the in-memory writer instance (used by `async with AsyncBufferedSaver(...) as f:`)
        return _InMemoryWriter()

    async def _fake_aexit(self, exc_type, exc, tb):
        # No-op cleanup
        return False

    # Replace the async context manager of AsyncBufferedSaver so `async with` yields an in-memory writer
    AsyncBufferedSaver.__aenter__ = _fake_aenter  # type: ignore
    AsyncBufferedSaver.__aexit__ = _fake_aexit  # type: ignore

    # Ensure export dir exists
    export_dir.mkdir(parents=True, exist_ok=True)

    # Run exporter and time it
    tgt = ExportTarget(id="bench-target")
    start = time.perf_counter()
    try:
        stats = await exporter._export_regular_target(tgt)
    finally:
        # restore original methods
        if orig_process is not None:
            Exporter._process_message_parallel = orig_process
        if orig_write is not None:
            AsyncBufferedSaver.write = orig_write  # type: ignore
        # Restore AsyncBufferedSaver context manager if we replaced it
        if orig_aenter is not None:
            AsyncBufferedSaver.__aenter__ = orig_aenter  # type: ignore
        if orig_aexit is not None:
            AsyncBufferedSaver.__aexit__ = orig_aexit  # type: ignore

    end = time.perf_counter()
    duration = end - start

    # Collect statistics
    processed = exporter.statistics.messages_processed
    media = exporter.statistics.media_downloaded
    pipeline_stats = dict(exporter.statistics.pipeline_stats or {})

    return {
        "messages": messages,
        "duration_s": duration,
        "throughput_msg_s": processed / duration if duration > 0 else 0.0,
        "processed": processed,
        "media": media,
        "pipeline_enabled": async_pipeline_enabled,
        "pipeline_stats": pipeline_stats,
        "raw_stats": stats or {},
    }


def run_benchmark(
    *,
    messages: int = 5000,
    runs: int = 3,
    process_workers: int = 2,
    process_delay: float = 0.001,
    writer_delay: float = 0.0005,
    out_json: Optional[Path] = None,
):
    """
    Run the benchmark for both pipeline enabled and disabled modes multiple times.
    """

    async def _run():
        results: List[Dict[str, Any]] = []
        # For fairness reuse same message set across modes/runs
        for mode in ("pipeline_enabled", "pipeline_disabled"):
            enabled = mode == "pipeline_enabled"
            for run_idx in range(1, runs + 1):
                # Use per-run subdir to avoid file collisions and to keep artifacts
                with tempfile.TemporaryDirectory(
                    prefix=f"bench_{mode}_run{run_idx}_"
                ) as td:
                    export_dir = Path(td) / "export"
                    export_dir.mkdir(parents=True, exist_ok=True)
                    res = await _run_single_export(
                        messages=messages,
                        export_dir=export_dir,
                        process_delay=process_delay,
                        writer_delay=writer_delay,
                        process_workers=process_workers,
                        async_pipeline_enabled=enabled,
                    )
                    res["mode"] = mode
                    res["run"] = run_idx
                    results.append(res)
                    # Basic reporting
                    print(
                        f"[{mode} run {run_idx}/{runs}] msg={messages} workers={process_workers} "
                        f"dur={res['duration_s']:.4f}s thpt={res['throughput_msg_s']:.1f} msg/s"
                    )
        # Summarize per-mode
        summary: Dict[str, Dict[str, Any]] = {}
        for mode in ("pipeline_enabled", "pipeline_disabled"):
            mode_results = [r for r in results if r["mode"] == mode]
            thr = [r["throughput_msg_s"] for r in mode_results]
            durs = [r["duration_s"] for r in mode_results]
            processed = [r["processed"] for r in mode_results]
            summary[mode] = {
                "runs": len(mode_results),
                "throughput_min": min(thr) if thr else 0.0,
                "throughput_max": max(thr) if thr else 0.0,
                "throughput_mean": statistics.mean(thr) if thr else 0.0,
                "throughput_median": statistics.median(thr) if thr else 0.0,
                "duration_mean": statistics.mean(durs) if durs else 0.0,
                "processed_mean": statistics.mean(processed) if processed else 0.0,
            }

        print("\nBenchmark summary:")
        for mode, s in summary.items():
            print(
                f"  {mode}: median_throughput={s['throughput_median']:.1f} msg/s mean={s['throughput_mean']:.1f}"
            )

        if out_json:
            out_data = {
                "results": results,
                "summary": summary,
                "meta": {
                    "messages": messages,
                    "process_workers": process_workers,
                    "process_delay": process_delay,
                    "writer_delay": writer_delay,
                    "runs": runs,
                },
            }
            with open(out_json, "w", encoding="utf-8") as fh:
                json.dump(out_data, fh, indent=2)
            print(f"Saved JSON results to {out_json}")

    asyncio.run(_run())


def parse_args(argv: Optional[List[str]] = None):
    p = argparse.ArgumentParser(
        description="Exporter pipeline benchmark (enabled vs disabled)"
    )
    p.add_argument(
        "--messages", "-n", type=int, default=5000, help="Number of messages to export"
    )
    p.add_argument("--runs", "-r", type=int, default=3, help="Runs per mode")
    p.add_argument(
        "--process-workers",
        "-w",
        type=int,
        default=2,
        help="Process workers (pipeline setting)",
    )
    p.add_argument(
        "--process-delay",
        type=float,
        default=0.001,
        help="Simulated process delay (s) per message",
    )
    p.add_argument(
        "--writer-delay",
        type=float,
        default=0.0005,
        help="Simulated writer delay (s) per message write",
    )
    p.add_argument("--out", type=str, default="", help="Optional JSON output path")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    out_path = Path(args.out) if args.out else None
    print("Exporter pipeline benchmark starting...")
    print(
        f"Messages: {args.messages}, runs: {args.runs}, process_workers: {args.process_workers}"
    )
    run_benchmark(
        messages=args.messages,
        runs=args.runs,
        process_workers=args.process_workers,
        process_delay=args.process_delay,
        writer_delay=args.writer_delay,
        out_json=out_path,
    )
