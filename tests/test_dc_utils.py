# tests for DC utilities (DCRouter + prewarm_workers)
# TDD tests - they express the expected behavior of the DC utilities module.
#
# Expected API (implemented in src/telegram_dc_utils.py):
#   - class DCRouter:
#       - @staticmethod prioritize_workers_by_dc(worker_clients, target_dc) -> List[int]
#       - @staticmethod select_best_worker_index(worker_clients, target_dc) -> Optional[int]
#   - async def prewarm_workers(worker_clients, entity, timeout=5) -> Dict[int, bool]
#
# The tests below assume the above API and will fail until the implementation exists.
#

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.telegram_dc_utils import DCRouter, prewarm_workers  # type: ignore


class FakeWorker:
    """A minimal fake worker client for prewarming and DC tests."""

    def __init__(
        self, idx: int, *, connected_dc: int = 0, delay: float = 0.0, fail: bool = False
    ):
        self.idx = idx
        self.connected_dc = connected_dc  # attribute used by DCRouter
        self._delay = float(delay)
        self._fail = bool(fail)

    async def get_entity(self, entity: Any):
        """Simulate a lightweight RPC that can succeed, fail, or be slow."""
        # Simulate network or RPC delay
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError("simulated failure")
        # On success, return a simple namespace (client behavior not relied upon)
        return SimpleNamespace(id=getattr(entity, "id", None))


def _make_workers(dc_map: Dict[int, int]) -> List[FakeWorker]:
    """Helper to create workers with connected_dc set based on dc_map: {idx: dc_id}"""
    max_idx = max(dc_map.keys()) if dc_map else -1
    return [FakeWorker(i, connected_dc=dc_map.get(i, 0)) for i in range(max_idx + 1)]


def test_prioritize_workers_by_dc_basic():
    """
    Workers that already have connected_dc equal to target DC should be prioritized first.
    The function returns a list of worker indices ordered by preference.
    """
    # Worker 0 is in DC 2, worker 1 in DC 0, worker 2 in DC 2.
    workers = _make_workers({0: 2, 1: 0, 2: 2})
    prioritized = DCRouter.prioritize_workers_by_dc(workers, target_dc=2)
    # First two indices should be the workers with DC==2 (order among them may be by index)
    assert prioritized[0] in (0, 2)
    assert prioritized[1] in (0, 2)
    assert set(prioritized[:2]) == {0, 2}
    # A worker with different DC should appear later
    assert prioritized[-1] == 1


def test_select_best_worker_index_fallback():
    """
    If no worker matches the target DC, select_best_worker_index should still
    return a valid index (e.g., first available) rather than None.
    """
    workers = _make_workers({0: 0, 1: 0, 2: 0})
    chosen = DCRouter.select_best_worker_index(workers, target_dc=99)
    assert isinstance(chosen, int)
    assert 0 <= chosen < len(workers)


@pytest.mark.asyncio
async def test_prewarm_workers_success_and_failure():
    """
    prewarm_workers should attempt to call get_entity on each client and return
    a mapping of worker index -> success boolean.
    """
    # Worker 0: successful fast response
    # Worker 1: fails immediately
    # Worker 2: succeeds after a short delay
    w0 = FakeWorker(0, delay=0.0, fail=False)
    w1 = FakeWorker(1, delay=0.0, fail=True)
    w2 = FakeWorker(2, delay=0.01, fail=False)

    workers = [w0, w1, w2]
    entity = SimpleNamespace(id=123)

    results: Dict[int, bool] = await prewarm_workers(workers, entity, timeout=1.0)
    # Results should be recorded per worker index
    assert isinstance(results, dict)
    assert results[0] is True
    assert results[1] is False
    assert results[2] is True


@pytest.mark.asyncio
async def test_prewarm_workers_runs_concurrently_and_respects_timeout():
    """
    Validate that prewarm_workers executes prewarm calls concurrently (small
    total running time) and times out long-running prewarm attempts.
    """
    # Worker 0: slow (0.5s), Worker 1: slow (0.5s)
    # With concurrency, total time should be ~= 0.5s, not ~1.0s.
    w0 = FakeWorker(0, delay=0.5, fail=False)
    w1 = FakeWorker(1, delay=0.5, fail=False)
    workers = [w0, w1]
    entity = SimpleNamespace(id=456)

    t0 = time.monotonic()
    results = await prewarm_workers(workers, entity, timeout=1.0)
    t1 = time.monotonic()

    elapsed = t1 - t0
    # Ensure it ran concurrently (elapsed significantly less than serial 1.0s)
    assert elapsed < 0.9, f"Expected concurrent prewarm (elapsed={elapsed:.3f}s)"

    # Now test timeout behavior: use a worker that will sleep longer than timeout
    w_slow = FakeWorker(2, delay=1.5, fail=False)
    results = await prewarm_workers([w_slow], entity, timeout=0.2)
    # Should be marked as failed due to timeout
    # The returned dict uses per-call indices starting at 0 for the provided list
    assert results.get(0) is False
