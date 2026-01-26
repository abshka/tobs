# Optimization Report: Batch Message Fetching

## Overview
We have replaced the previous `iter_messages`-based implementation in `TelegramManager` with a more efficient batch fetching mechanism using `get_messages`. This change targets the high-priority optimization identified in `docs/telethon_optimizations.md`.

## Rationale
The previous implementation used `iter_messages` wrapped in a complex per-message timeout loop. This introduced significant Python-level overhead:
1.  **Per-message Await:** Each message required an `await` cycle, slowing down the event loop.
2.  **Timeout Overhead:** Creating `asyncio.wait_for` for every single message is expensive.
3.  **Hidden Batching:** While `iter_messages` does batch internally, the wrapper logic effectively processed them one by one.

## Implemented Changes

### 1. Configuration
-   Added `batch_fetch_size` to `src/config.py` (Default: 100).
-   This allows tuning the chunk size based on network conditions and message size.

### 2. `TelegramManager.fetch_messages`
-   **Old:** Used `iter_messages` with a custom `_message_generator` and `asyncio.wait_for` on every yield.
-   **New:** Uses a `while True` loop that calls `client.get_messages(limit=batch_size)`.
-   **Logic:**
    -   Fetches a batch of messages.
    -   Yields them one by one (maintaining the API contract).
    -   Updates `offset_id` to the last message in the batch (oldest) to fetch the next batch.
    -   Includes robust `FloodWaitError` handling and retries at the batch level.

### 3. `TelegramManager.get_topic_messages_stream`
-   **Old:** Similar `iter_messages` wrapper for forum topics.
-   **New:** Applied the same batching pattern using `client.get_messages(reply_to=topic_id)`.
-   **Logic:**
    -   Respects `reply_to` to fetch topic-specific messages.
    -   Filters out the topic creation message if returned.
    -   Manages pagination via `offset_id`.

## Impact Analysis
-   **Throughput:** Expected to significantly increase `messages/second` rate due to reduced CPU overhead and better network utilization.
-   **Stability:** Removed the brittle per-message timeout which could trigger false positives on slow processing.
-   **Compatibility:** The method signatures and yield behavior are unchanged. Consumers like `src/export/exporter.py` will work without modification.

## Verification
-   **Manual Review:** Code logic confirms correct `offset_id` chaining and limit handling.
-   **Next Steps:** Run a test export to verify message continuity and performance gain.
