#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integration test: ensure ShardedTelegramManager invokes the DC pre-warm step
and marks worker clients with `connected_dc` when prewarm succeeds.

This test uses lightweight fakes and monkeypatches to:
  - Avoid constructing real Telethon clients
  - Ensure the code path that connects workers and runs prewarm is exercised
  - Stop the flow immediately after prewarm (by monkeypatching HotZonesManager) so
    we can assert the observed prewarm side-effects without executing the full export.
"""

from types import SimpleNamespace

import pytest

import src.hot_zones_manager as hot_zones_mod
import src.telegram_sharded_client as sharded_mod
from src.config import Config


class FakeWorkerClient:
    """Minimal fake worker client that supports connect() and get_entity()."""

    def __init__(
        self, session_name, api_id=None, api_hash=None, takeout_id=None, **kwargs
    ):
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.takeout_id = takeout_id
        self._connected = False
        # attribute set by prewarm code on success:
        self.connected_dc = 0

    async def connect(self):
        self._connected = True

    async def get_entity(self, entity):
        # Return a harmless object; prewarm only needs success/timeout behavior
        return SimpleNamespace(id=getattr(entity, "id", None))


@pytest.mark.asyncio
async def test_sharded_manager_prewarm_marks_workers_connected(monkeypatch, tmp_path):
    """
    High-level integration test:
      - Patch ShardedTelegramManager._prepare_workers to avoid disk/sessions
      - Patch TakeoutWorkerClient to use FakeWorkerClient
      - Patch ShardedTelegramManager._extract_dc_id to return a fixed DC id
      - Patch HotZonesManager to raise after prewarm to short-circuit remaining flow
      - Run fetch_messages and assert worker clients were annotated with `connected_dc`
    """
    # Prepare minimal config
    cfg = Config(api_id=1, api_hash="a" * 32)
    cfg.export_path = tmp_path
    cfg.dc_aware_routing_enabled = True
    cfg.dc_prewarm_enabled = True
    cfg.dc_prewarm_timeout = 1  # short timeout for test

    # Instantiate manager
    mgr = sharded_mod.ShardedTelegramManager(cfg)

    # 1) Patch _prepare_workers to return two worker session names
    async def fake_prepare_workers():
        return ["worker_session_0", "worker_session_1"]

    monkeypatch.setattr(mgr, "_prepare_workers", fake_prepare_workers, raising=True)

    # 2) Patch the worker client constructor in the module to our fake class
    monkeypatch.setattr(
        sharded_mod, "TakeoutWorkerClient", FakeWorkerClient, raising=True
    )

    # 3) Force entity DC extraction to return a known DC id (e.g., 42)
    monkeypatch.setattr(mgr, "_extract_dc_id", lambda ent: 42, raising=True)

    # 4) Patch HotZonesManager to raise an exception right after prewarm to stop fetch_messages
    class _StopAfterPrewarmHotZonesManager:
        def __init__(self, config):
            # raise to short-circuit after worker setup + pre-warm phase
            raise RuntimeError("stop after prewarm")

    monkeypatch.setattr(
        hot_zones_mod, "HotZonesManager", _StopAfterPrewarmHotZonesManager, raising=True
    )

    # Now call fetch_messages and expect a RuntimeError from the fake HotZonesManager
    with pytest.raises(RuntimeError, match="stop after prewarm"):
        async for _ in mgr.fetch_messages(entity="some-entity", limit=None):
            break

    # After the expected interruption, worker_clients should have been created and
    # prewarm should have set connected_dc to the target DC (42) on success.
    assert len(mgr.worker_clients) == 2, "Expected two worker clients to be initialized"

    # Verify each fake client has connected_dc set to the expected DC
    for client in mgr.worker_clients:
        assert getattr(client, "connected_dc", 0) == 42, (
            "Expected prewarm to set client.connected_dc to the entity DC (42)"
        )
