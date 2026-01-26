#!/usr/bin/env python3
"""
Full-scale optimization test on real chat with ~50K messages.

This will test all optimization strategies on the complete "Полишка" chat
to get realistic performance data and accuracy measurements.
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
from telethon.tl.types import User

# Configuration from .env
API_ID = 1359771
API_HASH = "b355753834ef6c784224309eb1d1393d"
SESSION_NAME = "sessions/tobs_session"

# Target chat name
TARGET_CHAT = "Полишка"

# Known accurate count from previous full iteration test
KNOWN_ACCURATE_COUNT = 50707


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


async def method_baseline(client: TelegramClient, entity):
    """
    Baseline: Default iter_messages with all messages.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 1: Baseline iter_messages (DEFAULT)")
    print(f"{'=' * 80}")
    print("⏳ This will take ~9 minutes based on previous test...")

    count = 0
    start_time = datetime.now()
    last_print = start_time

    async for message in client.iter_messages(entity):
        count += 1

        # Print progress every 5 seconds
        now = datetime.now()
        if (now - last_print).total_seconds() >= 5.0:
            elapsed = (now - start_time).total_seconds()
            rate = count / elapsed if elapsed > 0 else 0
            eta = (KNOWN_ACCURATE_COUNT - count) / rate if rate > 0 else 0
            print(
                f"   📥 {count:,} / ~{KNOWN_ACCURATE_COUNT:,} ({count / KNOWN_ACCURATE_COUNT * 100:.1f}%) - {rate:.1f} msg/s - ETA: {eta / 60:.1f} min",
                end="\r",
            )
            last_print = now

    elapsed = (datetime.now() - start_time).total_seconds()
    rate = count / elapsed if elapsed > 0 else 0

    print(
        f"\n✅ Counted {count:,} messages in {elapsed:.2f} seconds ({elapsed / 60:.1f} minutes)"
    )
    print(f"📊 Average rate: {rate:.1f} msg/sec")

    accuracy = abs(count - KNOWN_ACCURATE_COUNT)
    print(
        f"🎯 Accuracy: {count:,} vs known {KNOWN_ACCURATE_COUNT:,} (diff: {accuracy})"
    )

    return {
        "method": "Baseline iter_messages",
        "count": count,
        "time": elapsed,
        "rate": rate,
        "accuracy": accuracy,
    }


async def method_reverse_true(client: TelegramClient, entity):
    """
    Optimized: iter_messages with reverse=True.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 2: iter_messages with reverse=True (OPTIMIZED)")
    print(f"{'=' * 80}")
    print("⏳ Testing if reverse=True is consistently faster...")

    count = 0
    start_time = datetime.now()
    last_print = start_time

    async for message in client.iter_messages(entity, reverse=True):
        count += 1

        # Print progress every 5 seconds
        now = datetime.now()
        if (now - last_print).total_seconds() >= 5.0:
            elapsed = (now - start_time).total_seconds()
            rate = count / elapsed if elapsed > 0 else 0
            eta = (KNOWN_ACCURATE_COUNT - count) / rate if rate > 0 else 0
            print(
                f"   📥 {count:,} / ~{KNOWN_ACCURATE_COUNT:,} ({count / KNOWN_ACCURATE_COUNT * 100:.1f}%) - {rate:.1f} msg/s - ETA: {eta / 60:.1f} min",
                end="\r",
            )
            last_print = now

    elapsed = (datetime.now() - start_time).total_seconds()
    rate = count / elapsed if elapsed > 0 else 0

    print(
        f"\n✅ Counted {count:,} messages in {elapsed:.2f} seconds ({elapsed / 60:.1f} minutes)"
    )
    print(f"📊 Average rate: {rate:.1f} msg/sec")

    accuracy = abs(count - KNOWN_ACCURATE_COUNT)
    print(
        f"🎯 Accuracy: {count:,} vs known {KNOWN_ACCURATE_COUNT:,} (diff: {accuracy})"
    )

    return {
        "method": "iter_messages (reverse=True)",
        "count": count,
        "time": elapsed,
        "rate": rate,
        "accuracy": accuracy,
    }


async def method_binary_search_estimate(client: TelegramClient, entity):
    """
    Fast estimate using min/max message IDs.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 3: Binary search estimate (INSTANT)")
    print(f"{'=' * 80}")

    start_time = datetime.now()

    # Get newest message
    newest = await client.get_messages(entity, limit=1)
    if not newest:
        return None

    max_id = newest[0].id
    print(f"   📍 Newest message ID: {max_id:,}")

    # Get oldest message
    oldest = await client.get_messages(entity, limit=1, reverse=True)
    if not oldest:
        return None

    min_id = oldest[0].id
    print(f"   📍 Oldest message ID: {min_id:,}")

    estimated_count = max_id - min_id + 1

    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"✅ Estimated {estimated_count:,} messages in {elapsed:.2f} seconds")
    print(f"⚠️  This is UPPER BOUND (includes deleted message IDs)")

    accuracy = abs(estimated_count - KNOWN_ACCURATE_COUNT)
    error_rate = (accuracy / KNOWN_ACCURATE_COUNT) * 100

    print(f"🎯 Accuracy: {estimated_count:,} vs known {KNOWN_ACCURATE_COUNT:,}")
    print(f"   Overestimate by: {accuracy:,} messages ({error_rate:.1f}% error)")

    return {
        "method": "Binary search (estimate)",
        "count": estimated_count,
        "time": elapsed,
        "rate": estimated_count / elapsed if elapsed > 0 else 0,
        "accuracy": accuracy,
        "error_rate": error_rate,
    }


async def method_hybrid_corrected(client: TelegramClient, entity):
    """
    Hybrid: Binary search + sample-based correction.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 4: Hybrid with sample-based correction (SMART)")
    print(f"{'=' * 80}")

    start_time = datetime.now()

    # Step 1: Get estimate via binary search
    print("   Step 1: Getting min/max IDs...")
    newest = await client.get_messages(entity, limit=1)
    oldest = await client.get_messages(entity, limit=1, reverse=True)

    if not newest or not oldest:
        return None

    max_id = newest[0].id
    min_id = oldest[0].id
    estimate = max_id - min_id + 1

    print(f"   📊 Estimated range: {estimate:,} messages")

    # Step 2: Take sample to calculate deletion rate
    print("   Step 2: Sampling 2000 messages to estimate deletion rate...")
    sample_size = 2000
    sample_count = 0
    sample_min_id = None
    sample_max_id = None

    async for msg in client.iter_messages(entity, limit=sample_size, reverse=True):
        if sample_count == 0:
            sample_min_id = msg.id
        sample_max_id = msg.id
        sample_count += 1

    sample_range = sample_max_id - sample_min_id + 1
    deletion_rate = 1 - (sample_count / sample_range) if sample_range > 0 else 0

    print(
        f"   📈 Sample: {sample_count} messages in ID range {sample_range:,} (deletion rate: {deletion_rate:.2%})"
    )

    # Step 3: Apply correction
    corrected_count = int(estimate * (1 - deletion_rate))

    elapsed = (datetime.now() - start_time).total_seconds()

    print(
        f"✅ Corrected estimate: {corrected_count:,} messages in {elapsed:.2f} seconds"
    )

    accuracy = abs(corrected_count - KNOWN_ACCURATE_COUNT)
    error_rate = (accuracy / KNOWN_ACCURATE_COUNT) * 100

    print(f"🎯 Accuracy: {corrected_count:,} vs known {KNOWN_ACCURATE_COUNT:,}")
    print(f"   Error: {accuracy:,} messages ({error_rate:.1f}%)")

    return {
        "method": "Hybrid (sample-corrected)",
        "count": corrected_count,
        "time": elapsed,
        "rate": corrected_count / elapsed if elapsed > 0 else 0,
        "accuracy": accuracy,
        "error_rate": error_rate,
        "deletion_rate": deletion_rate,
    }


async def method_chunked_counting(client: TelegramClient, entity):
    """
    Chunked: Count in large batches using GetHistoryRequest directly.
    """
    print(f"\n{'=' * 80}")
    print("METHOD 5: Chunked counting with direct API (BATCHED)")
    print(f"{'=' * 80}")
    print("⏳ Counting in batches of 100 messages...")

    count = 0
    offset_id = 0
    start_time = datetime.now()
    last_print = start_time

    input_peer = await client.get_input_entity(entity)

    while True:
        result = await client(
            GetHistoryRequest(
                peer=input_peer,
                offset_id=offset_id,
                offset_date=None,
                add_offset=0,
                limit=100,  # Max allowed by API
                max_id=0,
                min_id=0,
                hash=0,
            )
        )

        if not result.messages:
            break

        batch_count = len(result.messages)
        count += batch_count
        offset_id = result.messages[-1].id

        # Print progress every 5 seconds
        now = datetime.now()
        if (now - last_print).total_seconds() >= 5.0:
            elapsed = (now - start_time).total_seconds()
            rate = count / elapsed if elapsed > 0 else 0
            eta = (KNOWN_ACCURATE_COUNT - count) / rate if rate > 0 else 0
            print(
                f"   📥 {count:,} / ~{KNOWN_ACCURATE_COUNT:,} ({count / KNOWN_ACCURATE_COUNT * 100:.1f}%) - {rate:.1f} msg/s - ETA: {eta / 60:.1f} min",
                end="\r",
            )
            last_print = now

    elapsed = (datetime.now() - start_time).total_seconds()
    rate = count / elapsed if elapsed > 0 else 0

    print(
        f"\n✅ Counted {count:,} messages in {elapsed:.2f} seconds ({elapsed / 60:.1f} minutes)"
    )
    print(f"📊 Average rate: {rate:.1f} msg/sec")

    accuracy = abs(count - KNOWN_ACCURATE_COUNT)
    print(
        f"🎯 Accuracy: {count:,} vs known {KNOWN_ACCURATE_COUNT:,} (diff: {accuracy})"
    )

    return {
        "method": "Chunked GetHistoryRequest",
        "count": count,
        "time": elapsed,
        "rate": rate,
        "accuracy": accuracy,
    }


async def main():
    print("=" * 80)
    print("🚀 FULL-SCALE OPTIMIZATION TEST")
    print("=" * 80)
    print()
    print(f"Target: '{TARGET_CHAT}' (~{KNOWN_ACCURATE_COUNT:,} messages)")
    print("This will test all methods on the COMPLETE chat to get real-world data.")
    print()
    print("⚠️  WARNING: Some methods will take 5-10 minutes each!")
    print()

    # Initialize client
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("❌ Session not authorized.")
        await client.disconnect()
        return

    print("✅ Client connected\n")

    # Find dialog
    dialog = await find_dialog_by_name(client, TARGET_CHAT)
    if not dialog:
        print(f"❌ Dialog '{TARGET_CHAT}' not found!")
        await client.disconnect()
        return

    entity = dialog.entity
    print(f"✅ Found: '{TARGET_CHAT}'\n")

    print("=" * 80)
    print("TEST PLAN:")
    print("=" * 80)
    print("1. Binary search estimate (instant)")
    print("2. Hybrid with correction (~30 seconds)")
    print("3. Baseline iter_messages (~9 minutes)")
    print("4. Optimized reverse=True (~8 minutes)")
    print("5. Chunked counting (~5 minutes)")
    print()
    print("Total estimated time: ~25 minutes")
    print("=" * 80)
    print()

    response = input("Continue with full test? (y/N): ")
    if response.lower() != "y":
        print("Test cancelled.")
        await client.disconnect()
        return

    results = []

    # Fast methods first
    try:
        print("\n🏃 FAST METHODS (< 1 minute each)")
        print("=" * 80)

        result3 = await method_binary_search_estimate(client, entity)
        if result3:
            results.append(result3)

        await asyncio.sleep(2)

        result4 = await method_hybrid_corrected(client, entity)
        if result4:
            results.append(result4)

        await asyncio.sleep(2)

        # Slow methods
        print("\n\n🐌 SLOW METHODS (5-10 minutes each)")
        print("=" * 80)
        print("⚠️  These will take time. Press Ctrl+C to skip to results.\n")

        result5 = await method_chunked_counting(client, entity)
        if result5:
            results.append(result5)

        await asyncio.sleep(2)

        result2 = await method_reverse_true(client, entity)
        if result2:
            results.append(result2)

        await asyncio.sleep(2)

        result1 = await method_baseline(client, entity)
        if result1:
            results.append(result1)

    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted. Showing results for completed methods...\n")

    # Print final comparison
    print(f"\n{'=' * 80}")
    print("📊 FINAL RESULTS - FULL SCALE TEST")
    print(f"{'=' * 80}")
    print(f"Known accurate count: {KNOWN_ACCURATE_COUNT:,} messages\n")

    if not results:
        print("No results to show.")
        await client.disconnect()
        return

    # Sort by speed (rate)
    results_by_speed = sorted(results, key=lambda x: x["rate"], reverse=True)

    print("🏃 RANKED BY SPEED:")
    print(f"{'Rank':<6} {'Method':<35} {'Time':<15} {'Rate':<15}")
    print("-" * 80)
    for i, r in enumerate(results_by_speed, 1):
        time_str = (
            f"{r['time']:.2f}s"
            if r["time"] < 60
            else f"{r['time'] / 60:.1f}m ({r['time']:.0f}s)"
        )
        print(f"{i:<6} {r['method']:<35} {time_str:<15} {r['rate']:>10.1f} msg/s")

    print()

    # Sort by accuracy
    results_by_accuracy = sorted(results, key=lambda x: x["accuracy"])

    print("🎯 RANKED BY ACCURACY:")
    print(f"{'Rank':<6} {'Method':<35} {'Error':<15} {'Error %':<15} {'Count':<15}")
    print("-" * 80)
    for i, r in enumerate(results_by_accuracy, 1):
        error_pct = (r["accuracy"] / KNOWN_ACCURATE_COUNT) * 100
        print(
            f"{i:<6} {r['method']:<35} {r['accuracy']:>7,} msgs   {error_pct:>6.2f}%        {r['count']:>10,}"
        )

    print()
    print("=" * 80)
    print("💡 RECOMMENDATIONS FOR PRODUCTION:")
    print("=" * 80)

    # Find best options
    fastest = results_by_speed[0]
    most_accurate = results_by_accuracy[0]

    print(f"\n🏆 FASTEST METHOD:")
    print(f"   {fastest['method']}")
    print(f"   Time: {fastest['time']:.1f}s")
    print(
        f"   Accuracy: ±{fastest['accuracy']:,} messages ({fastest.get('error_rate', 0):.1f}%)"
    )

    print(f"\n🎯 MOST ACCURATE METHOD:")
    print(f"   {most_accurate['method']}")
    print(f"   Time: {most_accurate['time'] / 60:.1f} minutes")
    print(f"   Error: {most_accurate['accuracy']} messages")

    # Find best balance
    hybrid = next((r for r in results if "Hybrid" in r["method"]), None)
    if hybrid:
        print(f"\n⚖️  BEST BALANCE (Hybrid):")
        print(f"   Time: {hybrid['time']:.1f} seconds")
        print(
            f"   Accuracy: {hybrid['count']:,} (error: {hybrid.get('error_rate', 0):.1f}%)"
        )
        print(
            f"   Speed advantage: {results_by_accuracy[0]['time'] / hybrid['time']:.0f}x faster than accurate count"
        )

    print()
    print("=" * 80)

    await client.disconnect()
    print("\n✅ Full-scale test complete!\n")


if __name__ == "__main__":
    asyncio.run(main())
