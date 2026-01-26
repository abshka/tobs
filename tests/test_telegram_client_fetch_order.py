import asyncio
from types import SimpleNamespace
from typing import List

import pytest

# Local import (tests rely on minimal import stubs provided by conftest)
from src.telegram_client import TelegramManager


@pytest.mark.asyncio
async def test_fetch_messages_yields_messages_in_chronological_order():
    """
    Ensure TelegramManager.fetch_messages yields messages from oldest to newest
    when the underlying client returns batches. We simulate a client that
    responds to `get_messages` calls with consecutive id ranges.
    """

    class DummyConfig:
        def __init__(self):
            # Small batch size to force multiple batch fetches
            self.batch_fetch_size = 3
            self.request_delay = 0
            self.lazy_message_page_size = 50
            self.performance = SimpleNamespace(workers=1)
            # Other fields referenced by TelegramManager may remain absent;
            # tests use only the attributes above.

    total_messages = 10

    class FakeClient:
        """
        Fake client that simulates paginated `get_messages` behavior.
        When asked with offset_id=N and limit=L it returns messages with ids
        from N+1 to min(N+L, total_messages). This approximates the sequential
        forward-scanning behavior expected when reverse=True is used.
        """

        def __init__(self, total: int):
            self.total = int(total)

        async def get_messages(
            self, entity, limit=100, offset_id=0, min_id=0, reverse=False, **kwargs
        ):
            start = int(offset_id) + 1
            if start > self.total:
                return []
            end = min(start + int(limit) - 1, self.total)
            # Create simple message-like objects (SimpleNamespace with id/date)
            return [SimpleNamespace(id=i) for i in range(start, end + 1)]

    cfg = DummyConfig()
    mgr = TelegramManager(cfg)
    # Inject our fake client that behaves in a deterministic pagination-friendly way
    mgr.client = FakeClient(total_messages)

    collected: List[int] = []
    async for m in mgr.fetch_messages(entity="dummy", limit=None):
        collected.append(int(m.id))
        # safety guard (shouldn't be necessary, but prevents infinite loops on failure)
        if len(collected) > total_messages + 10:
            break

    assert collected == list(range(1, total_messages + 1)), (
        "Expected fetch_messages to yield messages in ascending order (oldest->newest). "
        f"Got order: {collected}"
    )
