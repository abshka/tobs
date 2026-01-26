#!/usr/bin/env python3
"""
Test script to verify the theory that Telegram API returns placeholder count
(2147483647) specifically for private chats (PeerUser).

This script will:
1. Fetch all dialogs
2. Categorize them by peer type (PeerUser, PeerChat, PeerChannel)
3. Get message count for each
4. Analyze patterns and identify which peer types return placeholders
"""

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from telethon import TelegramClient
from telethon.tl.types import (
    PeerChannel, PeerChat, PeerUser,
    User, Chat, Channel
)

# Configuration from .env
API_ID = 1359771
API_HASH = "b355753834ef6c784224309eb1d1393d"
SESSION_NAME = "sessions/tobs_session"

# Placeholder constant from Telegram API
PLACEHOLDER_COUNT = 2147483647
MAX_REASONABLE_COUNT = 100_000_000


async def get_peer_type_name(dialog) -> str:
    """Get human-readable peer type name based on entity type."""
    entity = dialog.entity
    
    if isinstance(entity, User):
        return "PeerUser"
    elif isinstance(entity, Chat):
        return "PeerChat"
    elif isinstance(entity, Channel):
        return "PeerChannel"
    
    return "Unknown"


async def get_message_count(client: TelegramClient, entity) -> int:
    """Get total message count using get_messages(limit=0)."""
    try:
        result = await client.get_messages(entity, limit=0)
        if hasattr(result, "total"):
            return result.total
        return 0
    except Exception as e:
        print(f"  ⚠️  Error getting count: {e}")
        return -1


async def main():
    print("=" * 80)
    print("🔍 Testing PeerUser Placeholder Theory")
    print("=" * 80)
    print()

    # Initialize client
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("❌ Session not authorized. Please run the main export first.")
        await client.disconnect()
        return

    print("✅ Client connected and authorized\n")

    # Data structures for analysis
    stats = defaultdict(
        lambda: {
            "total": 0,
            "placeholder": 0,
            "unreasonable": 0,
            "reasonable": 0,
            "zero": 0,
            "error": 0,
            "counts": [],
        }
    )

    examples = defaultdict(list)  # Store examples of each category

    print("📥 Fetching all dialogs...\n")

    dialog_count = 0
    async for dialog in client.iter_dialogs():
        dialog_count += 1

        # Get peer type
        peer_type = await get_peer_type_name(dialog)
        entity_name = (
            getattr(dialog.entity, "title", None)
            or getattr(dialog.entity, "first_name", None)
            or f"ID:{dialog.entity.id}"
        )

        # Get message count
        count = await get_message_count(client, dialog.entity)

        # Categorize result
        stat = stats[peer_type]
        stat["total"] += 1

        if count == -1:
            stat["error"] += 1
            category = "ERROR"
        elif count == 0:
            stat["zero"] += 1
            category = "ZERO"
        elif count == PLACEHOLDER_COUNT:
            stat["placeholder"] += 1
            category = "PLACEHOLDER"
        elif count > MAX_REASONABLE_COUNT:
            stat["unreasonable"] += 1
            category = "UNREASONABLE"
        else:
            stat["reasonable"] += 1
            stat["counts"].append(count)
            category = "REASONABLE"

        # Store example
        if len(examples[f"{peer_type}_{category}"]) < 3:  # Keep max 3 examples
            examples[f"{peer_type}_{category}"].append(
                {"name": entity_name, "count": count, "peer_type": peer_type}
            )

        # Print progress
        status_emoji = {
            "REASONABLE": "✅",
            "PLACEHOLDER": "🔴",
            "UNREASONABLE": "🟡",
            "ZERO": "⚪",
            "ERROR": "❌",
        }

        print(
            f"{status_emoji.get(category, '❓')} [{peer_type:12s}] {entity_name[:40]:40s} → {count:>12,d}"
        )

    print(f"\n{'=' * 80}")
    print(f"📊 ANALYSIS RESULTS ({dialog_count} total dialogs)")
    print(f"{'=' * 80}\n")

    # Detailed statistics by peer type
    for peer_type in ["PeerUser", "PeerChat", "PeerChannel", "Unknown"]:
        if peer_type not in stats:
            continue

        stat = stats[peer_type]
        total = stat["total"]

        if total == 0:
            continue

        print(f"\n{'─' * 80}")
        print(f"📋 {peer_type} ({total} dialogs)")
        print(f"{'─' * 80}")

        # Calculate percentages
        placeholder_pct = (stat["placeholder"] / total) * 100 if total > 0 else 0
        reasonable_pct = (stat["reasonable"] / total) * 100 if total > 0 else 0
        unreasonable_pct = (stat["unreasonable"] / total) * 100 if total > 0 else 0
        zero_pct = (stat["zero"] / total) * 100 if total > 0 else 0
        error_pct = (stat["error"] / total) * 100 if total > 0 else 0

        print(
            f"  ✅ Reasonable counts:    {stat['reasonable']:4d} ({reasonable_pct:5.1f}%)"
        )
        print(
            f"  🔴 Placeholder (INT_MAX): {stat['placeholder']:4d} ({placeholder_pct:5.1f}%)"
        )
        print(
            f"  🟡 Unreasonable (>100M):  {stat['unreasonable']:4d} ({unreasonable_pct:5.1f}%)"
        )
        print(f"  ⚪ Zero counts:           {stat['zero']:4d} ({zero_pct:5.1f}%)")
        print(f"  ❌ Errors:                {stat['error']:4d} ({error_pct:5.1f}%)")

        # Statistics for reasonable counts
        if stat["counts"]:
            counts = stat["counts"]
            avg_count = sum(counts) / len(counts)
            min_count = min(counts)
            max_count = max(counts)
            print(f"\n  📈 Reasonable count stats:")
            print(f"     Average: {avg_count:,.0f}")
            print(f"     Min:     {min_count:,d}")
            print(f"     Max:     {max_count:,d}")

        # Show examples
        print(f"\n  💡 Examples:")
        for category in ["REASONABLE", "PLACEHOLDER", "UNREASONABLE", "ZERO"]:
            key = f"{peer_type}_{category}"
            if key in examples and examples[key]:
                print(f"     {category}:")
                for ex in examples[key][:2]:  # Show up to 2 examples
                    print(f"       • {ex['name'][:35]:35s} → {ex['count']:>12,d}")

    # Theory verification
    print(f"\n{'=' * 80}")
    print("🎯 THEORY VERIFICATION")
    print(f"{'=' * 80}\n")

    peer_user_stats = stats.get("PeerUser", {})
    peer_chat_stats = stats.get("PeerChat", {})
    peer_channel_stats = stats.get("PeerChannel", {})

    # Calculate placeholder rates
    user_placeholder_rate = (
        peer_user_stats.get("placeholder", 0) / peer_user_stats.get("total", 1)
    ) * 100
    chat_placeholder_rate = (
        peer_chat_stats.get("placeholder", 0) / peer_chat_stats.get("total", 1)
    ) * 100
    channel_placeholder_rate = (
        peer_channel_stats.get("placeholder", 0) / peer_channel_stats.get("total", 1)
    ) * 100

    print(f"Placeholder rate by peer type:")
    print(f"  • PeerUser (private chats):     {user_placeholder_rate:5.1f}%")
    print(f"  • PeerChat (small groups):      {chat_placeholder_rate:5.1f}%")
    print(f"  • PeerChannel (channels/groups): {channel_placeholder_rate:5.1f}%")

    print(f"\n{'─' * 80}")

    # Verdict
    if (
        user_placeholder_rate > 50
        and chat_placeholder_rate < 10
        and channel_placeholder_rate < 10
    ):
        print("✅ THEORY CONFIRMED:")
        print(
            "   Telegram API returns placeholder (2147483647) predominantly for PeerUser"
        )
        print("   (private chats), while PeerChat and PeerChannel return real counts.")
    elif user_placeholder_rate > 80:
        print("✅ THEORY STRONGLY CONFIRMED:")
        print("   Nearly all PeerUser dialogs return placeholder count.")
    elif user_placeholder_rate < 10:
        print("❌ THEORY REJECTED:")
        print("   PeerUser dialogs do NOT consistently return placeholder values.")
    else:
        print("🟡 THEORY PARTIALLY CONFIRMED:")
        print("   PeerUser dialogs show mixed behavior with placeholder counts.")

    print(f"{'=' * 80}\n")

    await client.disconnect()
    print("✅ Analysis complete!\n")


if __name__ == "__main__":
    asyncio.run(main())
