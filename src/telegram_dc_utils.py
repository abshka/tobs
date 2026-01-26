"""
DC utilities for TOBS - datacenter-aware routing helpers and prewarm utilities.

Provides:
- DCRouter: lightweight worker prioritization helper (prefer workers already
  connected to a target datacenter).
- prewarm_workers: asynchronously attempt a cheap RPC on each worker client to
  establish connectivity to the target datacenter (e.g. by calling `get_entity`).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

# Use the project's configured logger when available
try:
    from src.utils import logger  # type: ignore
except Exception:  # pragma: no cover - fallback for isolated tests/environments
    import logging as _logging

    logger = _logging.getLogger(__name__)


class DCRouter:
    """
    Lightweight datacenter-aware router utilities.

    The routines are intentionally minimal so they are easy to unit-test and
    adapt in follow-up iterations (e.g., using worker latency statistics).
    """

    @staticmethod
    def prioritize_workers_by_dc(
        worker_clients: List[Any], target_dc: int
    ) -> List[int]:
        """
        Return a list of worker indices ordered by preference for the given target DC.

        Strategy (simple and deterministic):
          1. Workers whose `connected_dc` attribute equals `target_dc` come first.
          2. The remainder follow in their original order.

        Args:
            worker_clients: list of worker client objects; each object MAY have an
                            attribute `connected_dc` (int). Missing attribute is
                            treated as 0 (unknown).
            target_dc: datacenter id (int) to prioritize

        Returns:
            list of worker indices (ints) ordered by preference.
        """
        preferred = []
        others = []
        for idx, client in enumerate(worker_clients):
            client_dc = getattr(client, "connected_dc", 0) or 0
            if client_dc == target_dc and target_dc != 0:
                preferred.append(idx)
            else:
                others.append(idx)
        return preferred + others

    @staticmethod
    def select_best_worker_index(
        worker_clients: List[Any], target_dc: int, strategy: str = "smart"
    ) -> Optional[int]:
        """
        Choose the single best worker index given a target DC and strategy.

        At this stage `strategy` is accepted for future extensions; current
        behavior is equivalent to returning the first prioritized worker, else
        the first available worker.

        Returns:
            int index of chosen worker, or None if no workers provided.
        """
        if not worker_clients:
            return None

        prioritized = DCRouter.prioritize_workers_by_dc(worker_clients, target_dc)
        if prioritized:
            return prioritized[0]
        return 0


async def prewarm_workers(
    worker_clients: List[Any],
    entity: Any,
    timeout: float = 5.0,
    dc_id: Optional[int] = None,
) -> Dict[int, bool]:
    """
    Attempt to pre-warm worker clients for `entity` by invoking a cheap RPC.

    Behavior:
    - For each `client` in `worker_clients`, attempts `await client.get_entity(entity)`
      with the provided timeout. This is a lightweight way to ensure the client's
      connection is routed to the desired datacenter (server-side) for subsequent
      heavy operations.
    - On success, if `dc_id` is provided and > 0, the client's attribute
      `connected_dc` will be set to `dc_id` for later routing decisions.
    - Returns a mapping {worker_index: success_bool}.

    Notes:
    - This function is resilient: a single slow/failing worker won't abort others.
    - Exceptions or timeouts are treated as failure (False).
    """
    results: Dict[int, bool] = {}

    async def _try_prewarm(idx: int, client: Any) -> None:
        try:
            # Some fake/stand-in clients in tests provide `get_entity` as a coroutine.
            coro = getattr(client, "get_entity", None)
            if coro is None or not callable(coro):
                # No callable present -> cannot pre-warm; mark as failed
                logger.debug(f"Worker #{idx}: no get_entity callable; skipping prewarm")
                results[idx] = False
                return

            # Run the client's get_entity call with timeout
            await asyncio.wait_for(client.get_entity(entity), timeout=timeout)

            # If successful, optionally annotate client with the DC id
            if dc_id and dc_id > 0:
                try:
                    setattr(client, "connected_dc", int(dc_id))
                except Exception:
                    # Best-effort; don't fail the whole prewarm on attribute set errors
                    logger.debug(f"Worker #{idx}: could not set connected_dc attribute")

            results[idx] = True
            logger.debug(f"Worker #{idx}: prewarm succeeded")
        except asyncio.TimeoutError:
            logger.debug(f"Worker #{idx}: prewarm timed out after {timeout:.2f}s")
            results[idx] = False
        except Exception as e:
            logger.debug(f"Worker #{idx}: prewarm failed: {e}")
            results[idx] = False

    # Launch prewarm tasks concurrently for better wall-clock behavior
    tasks = [
        asyncio.create_task(_try_prewarm(i, c)) for i, c in enumerate(worker_clients)
    ]
    # Wait for all to complete (they set results[] themselves)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # Log summary
    succeeded = [i for i, ok in results.items() if ok]
    failed = [i for i, ok in results.items() if not ok]
    logger.info(
        f"DC prewarm completed: {len(succeeded)} succeeded, {len(failed)} failed (timeout={timeout}s)"
    )

    return results


__all__ = ["DCRouter", "prewarm_workers"]
