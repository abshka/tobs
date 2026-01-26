#!/usr/bin/env python3
"""
Test script to explore optimization strategies for counting messages in private chats.

This will compare different approaches:
1. Baseline: iter_messages() with default settings
2. Large batches: iter_messages() with larger limit parameter
3. Direct API: GetHistoryRequest with optimized parameters
4. Lightweight iteration: Fetch only message IDs, not full content
5. Binary search approach: Find min/max ID and estimate
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import InputPeerEmpty, User

# Configuration from .env
API_ID = 1359771
API_HASH = "b355753834ef6c784224309eb1d1393d"
SESSION_NAME = "sessions/tobs_session"

# Target chat name
TARGET_CHAT = "Полишка"


async def find_dialog_by_name(client: TelegramClient, name: str):
    """Find a dialog by its display name."""
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        entity_name = (
            getattr(entity, "title", None)
            or getattr(entity, "first_name", None)
            or f"ID:{entity.id}"
        )
        if entity_name == name:
            return dialog
    return None


async def method1_baseline(client: TelegramClient, entity, limit_messages=1000):
    """
    Method 1: Baseline - iter_messages with default settings.
    Only count first N messages for quick testing.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 1: Baseline iter_messages (default settings)")
    print(f"{'=' * 80}")

    count = 0
    start_time = datetime.now()

    async for message in client.iter_messages(entity, limit=limit_messages):
        count += 1

    elapsed = (datetime.now() - start_time).total_seconds()
    rate = count / elapsed if elapsed > 0 else 0

    print(f"✅ Counted {count:,} messages in {elapsed:.2f} seconds")
    print(f"📊 Rate: {rate:.1f} msg/sec")

    return {
        "method": "Baseline iter_messages",
        "count": count,
        "time": elapsed,
        "rate": rate,
    }


async def method2_large_batches(client: TelegramClient, entity, limit_messages=1000):
    """
    Method 2: iter_messages with wait_time=0 to reduce delays.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 2: iter_messages with wait_time=0 (no rate limiting)")
    print(f"{'=' * 80}")

    count = 0
    start_time = datetime.now()

    async for message in client.iter_messages(
        entity, limit=limit_messages, wait_time=0
    ):
        count += 1

    elapsed = (datetime.now() - start_time).total_seconds()
    rate = count / elapsed if elapsed > 0 else 0

    print(f"✅ Counted {count:,} messages in {elapsed:.2f} seconds")
    print(f"📊 Rate: {rate:.1f} msg/sec")

    return {
        "method": "iter_messages (wait_time=0)",
        "count": count,
        "time": elapsed,
        "rate": rate,
    }


async def method3_direct_api(client: TelegramClient, entity, limit_messages=1000):
    """
    Method 3: Direct GetHistoryRequest with maximum limit per request.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 3: Direct GetHistoryRequest (max limit=100)")
    print(f"{'=' * 80}")

    count = 0
    offset_id = 0
    start_time = datetime.now()

    input_peer = await client.get_input_entity(entity)

    while count < limit_messages:
        # Get maximum allowed per request (100)
        batch_size = min(100, limit_messages - count)

        result = await client(
            GetHistoryRequest(
                peer=input_peer,
                offset_id=offset_id,
                offset_date=None,
                add_offset=0,
                limit=batch_size,
                max_id=0,
                min_id=0,
                hash=0,
            )
        )

        if not result.messages:
            break

        count += len(result.messages)
        offset_id = result.messages[-1].id

    elapsed = (datetime.now() - start_time).total_seconds()
    rate = count / elapsed if elapsed > 0 else 0

    print(f"✅ Counted {count:,} messages in {elapsed:.2f} seconds")
    print(f"📊 Rate: {rate:.1f} msg/sec")

    return {
        "method": "Direct GetHistoryRequest",
        "count": count,
        "time": elapsed,
        "rate": rate,
    }


async def method4_ids_only(client: TelegramClient, entity, limit_messages=1000):
    """
    Method 4: Fetch with ids parameter to get only IDs (lighter payload).
    Note: This is speculative - need to verify if Telethon supports this.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 4: Lightweight iteration (testing payload optimization)")
    print(f"{'=' * 80}")

    count = 0
    start_time = datetime.now()

    # Try to minimize data transferred by not accessing message content
    async for message in client.iter_messages(entity, limit=limit_messages):
        # Only access ID, skip all other attributes
        _ = message.id
        count += 1

    elapsed = (datetime.now() - start_time).total_seconds()
    rate = count / elapsed if elapsed > 0 else 0

    print(f"✅ Counted {count:,} messages in {elapsed:.2f} seconds")
    print(f"📊 Rate: {rate:.1f} msg/sec")

    return {
        "method": "Lightweight iteration",
        "count": count,
        "time": elapsed,
        "rate": rate,
    }


async def method5_binary_search(client: TelegramClient, entity):
    """
    Method 5: Use binary search to find the oldest message ID.
    Then estimate count = (max_id - min_id + 1) - deleted_messages

    This is VERY fast but may not be 100% accurate due to:
    - Deleted messages (gaps in IDs)
    - Service messages
    """
    print(f"\n{'=' * 80}")
    print("METHOD 5: Binary search for min/max message IDs")
    print(f"{'=' * 80}")

    start_time = datetime.now()

    # Get newest message (max_id)
    newest = await client.get_messages(entity, limit=1)
    if not newest:
        print("❌ No messages found")
        return None

    max_id = newest[0].id
    print(f"📍 Newest message ID: {max_id}")

    # Get oldest message (reverse=True gets oldest first)
    oldest = await client.get_messages(entity, limit=1, reverse=True)
    if not oldest:
        print("❌ No messages found")
        return None

    min_id = oldest[0].id
    print(f"📍 Oldest message ID: {min_id}")

    # Estimate count (this is upper bound, actual may be less due to deletions)
    estimated_count = max_id - min_id + 1

    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"✅ Estimated range: {estimated_count:,} messages in {elapsed:.2f} seconds")
    print(f"⚠️  Note: This is an UPPER BOUND (includes deleted message IDs)")

    return {
        "method": "Binary search (estimate)",
        "count": estimated_count,
        "time": elapsed,
        "rate": estimated_count / elapsed if elapsed > 0 else 0,
        "note": "Upper bound estimate",
    }


async def method6_batch_size_test(client: TelegramClient, entity, limit_messages=1000):
    """
    Method 6: Test different internal batch sizes.
    Telethon's iter_messages uses internal batching - let's see if we can optimize it.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 6: Testing with reverse=False vs reverse=True")
    print(f"{'=' * 80}")

    # Test reverse=False (newest first, default)
    count1 = 0
    start_time1 = datetime.now()
    async for message in client.iter_messages(
        entity, limit=limit_messages, reverse=False
    ):
        count1 += 1
    elapsed1 = (datetime.now() - start_time1).total_seconds()
    rate1 = count1 / elapsed1 if elapsed1 > 0 else 0

    print(
        f"  reverse=False: {count1:,} messages in {elapsed1:.2f}s ({rate1:.1f} msg/sec)"
    )

    # Test reverse=True (oldest first)
    count2 = 0
    start_time2 = datetime.now()
    async for message in client.iter_messages(
        entity, limit=limit_messages, reverse=True
    ):
        count2 += 1
    elapsed2 = (datetime.now() - start_time2).total_seconds()
    rate2 = count2 / elapsed2 if elapsed2 > 0 else 0

    print(
        f"  reverse=True:  {count2:,} messages in {elapsed2:.2f}s ({rate2:.1f} msg/sec)"
    )

    # Return the faster one
    if rate1 > rate2:
        return {
            "method": "reverse=False (newest first)",
            "count": count1,
            "time": elapsed1,
            "rate": rate1,
        }
    else:
        return {
            "method": "reverse=True (oldest first)",
            "count": count2,
            "time": elapsed2,
            "rate": rate2,
        }


async def main():
    print("=" * 80)
    print("🚀 Message Counting Optimization Tests")
    print("=" * 80)
    print()
    print("This will test various optimization strategies for counting messages")
    print("in private chats (PeerUser) where API returns placeholder.")
    print()

    # Initialize client
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("❌ Session not authorized. Please run the main export first.")
        await client.disconnect()
        return

    print("✅ Client connected and authorized\n")

    # Find target dialog
    print(f"🔎 Searching for dialog: '{TARGET_CHAT}'...")
    dialog = await find_dialog_by_name(client, TARGET_CHAT)

    if not dialog:
        print(f"❌ Dialog '{TARGET_CHAT}' not found!")
        await client.disconnect()
        return

    entity = dialog.entity
    entity_name = getattr(entity, "first_name", None) or getattr(entity, "title", None)

    print(f"✅ Found: '{entity_name}' (Type: {type(entity).__name__})")
    print()

    # Test sample size
    TEST_LIMIT = 1000  # Count only first 1000 messages for quick comparison

    print(f"⚙️  Test configuration: First {TEST_LIMIT:,} messages per method")
    print(f"⚙️  This allows quick comparison without waiting 9+ minutes")
    print()

    input("Press Enter to start tests...")

    results = []

    # Run tests
    try:
        results.append(await method1_baseline(client, entity, TEST_LIMIT))
        await asyncio.sleep(2)  # Brief pause between tests

        results.append(await method2_large_batches(client, entity, TEST_LIMIT))
        await asyncio.sleep(2)

        results.append(await method3_direct_api(client, entity, TEST_LIMIT))
        await asyncio.sleep(2)

        results.append(await method4_ids_only(client, entity, TEST_LIMIT))
        await asyncio.sleep(2)

        results.append(await method6_batch_size_test(client, entity, TEST_LIMIT))
        await asyncio.sleep(2)

        # Method 5 is special - it estimates total without iteration
        result5 = await method5_binary_search(client, entity)
        if result5:
            results.append(result5)

    except KeyboardInterrupt:
        print("\n\n⚠️  Tests interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Error during tests: {e}")
        import traceback

        traceback.print_exc()

    # Print comparison
    print(f"\n{'=' * 80}")
    print("📊 COMPARISON OF ALL METHODS")
    print(f"{'=' * 80}\n")

    if results:
        # Sort by rate (fastest first)
        results_sorted = sorted(results, key=lambda x: x["rate"], reverse=True)

        print(f"{'Rank':<6} {'Method':<35} {'Time':<12} {'Rate':<15} {'Notes'}")
        print(f"{'-' * 6} {'-' * 35} {'-' * 12} {'-' * 15} {'-' * 20}")

        for i, r in enumerate(results_sorted, 1):
            note = r.get("note", "")
            print(
                f"{i:<6} {r['method']:<35} {r['time']:>8.2f}s   {r['rate']:>10.1f} msg/s  {note}"
            )

        print()
        print("🏆 WINNER:")
        winner = results_sorted[0]
        print(f"   Method: {winner['method']}")
        print(f"   Speed:  {winner['rate']:.1f} msg/sec")
        print(f"   Time:   {winner['time']:.2f} seconds")

        # Calculate speedup
        baseline = next((r for r in results if "Baseline" in r["method"]), None)
        if baseline and winner["method"] != baseline["method"]:
            speedup = winner["rate"] / baseline["rate"]
            print(f"   Speedup: {speedup:.2f}x faster than baseline")

        print()

        # Recommendations
        print("💡 RECOMMENDATIONS:")
        print()
        if any("Binary search" in r["method"] for r in results_sorted[:3]):
            print("   ✅ For FAST ESTIMATES (acceptable error):")
            print("      → Use binary search method (min/max ID)")
            print("      → ~Instant results, but includes deleted message IDs")
            print()

        print("   ✅ For ACCURATE COUNTS:")
        fastest_accurate = results_sorted[0]
        if "estimate" not in fastest_accurate.get("note", "").lower():
            print(f"      → Use: {fastest_accurate['method']}")
            print(f"      → Rate: {fastest_accurate['rate']:.1f} msg/sec")

            # Extrapolate to full chat
            full_count = 50707  # From previous test
            estimated_time = full_count / fastest_accurate["rate"]
            print(
                f"      → Estimated time for {full_count:,} messages: {estimated_time / 60:.1f} minutes"
            )

    print(f"\n{'=' * 80}\n")

    await client.disconnect()
    print("✅ All tests complete!\n")


if __name__ == "__main__":
    asyncio.run(main())
