#!/usr/bin/env python3
"""
Test script to count messages in a specific chat through full iteration.

This will demonstrate:
1. Finding a specific dialog by name
2. Iterating through all messages
3. Counting the total accurately
4. Comparing with API placeholder value
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from telethon import TelegramClient
from telethon.tl.types import User

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


async def count_messages_via_iteration(client: TelegramClient, entity):
    """Count messages by iterating through all of them."""
    print(f"📊 Starting full message iteration...")
    print(f"   This may take a while for large chats.\n")

    count = 0
    start_time = datetime.now()
    last_print_time = start_time

    async for message in client.iter_messages(entity):
        count += 1

        # Print progress every second
        now = datetime.now()
        if (now - last_print_time).total_seconds() >= 1.0:
            elapsed = (now - start_time).total_seconds()
            rate = count / elapsed if elapsed > 0 else 0
            print(f"   📥 {count:,} messages processed ({rate:.1f} msg/sec)", end="\r")
            last_print_time = now

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n   ✅ Completed in {elapsed:.2f} seconds")

    return count


async def get_api_placeholder(client: TelegramClient, entity):
    """Get the API's placeholder count."""
    try:
        result = await client.get_messages(entity, limit=0)
        if hasattr(result, "total"):
            return result.total
        return None
    except Exception as e:
        print(f"   ⚠️  Error getting API count: {e}")
        return None


async def main():
    print("=" * 80)
    print("🔍 Message Count Test - Single Chat via Iteration")
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

    # Find target dialog
    print(f"🔎 Searching for dialog: '{TARGET_CHAT}'...")
    dialog = await find_dialog_by_name(client, TARGET_CHAT)

    if not dialog:
        print(f"❌ Dialog '{TARGET_CHAT}' not found!")
        print("\nAvailable dialogs:")
        count = 0
        async for d in client.iter_dialogs():
            if count < 10:  # Show first 10
                entity = d.entity
                name = (
                    getattr(entity, "title", None)
                    or getattr(entity, "first_name", None)
                    or f"ID:{entity.id}"
                )
                print(f"   • {name}")
                count += 1
        print(f"   ... and {await client.get_dialogs_count() - 10} more")
        await client.disconnect()
        return

    entity = dialog.entity
    entity_name = getattr(entity, "first_name", None) or getattr(entity, "title", None)

    print(f"✅ Found dialog: '{entity_name}'")
    print(f"   Type: {type(entity).__name__}")
    print(f"   ID: {entity.id}")

    # Check if it's a PeerUser (expected to have placeholder)
    is_user = isinstance(entity, User)
    print(f"   Is PeerUser: {'Yes' if is_user else 'No'}")
    print()

    # Get API placeholder value
    print("1️⃣  Getting API placeholder count...")
    api_count = await get_api_placeholder(client, entity)

    if api_count:
        is_placeholder = api_count == 2147483647
        print(f"   API returned: {api_count:,}")
        if is_placeholder:
            print(f"   🔴 This is INT32_MAX placeholder (as expected for PeerUser)")
        else:
            print(f"   ✅ This is a real count")
    else:
        print(f"   ⚠️  Could not get API count")
    print()

    # Count via iteration
    print("2️⃣  Counting messages via full iteration...")
    real_count = await count_messages_via_iteration(client, entity)

    print()
    print("=" * 80)
    print("📊 RESULTS")
    print("=" * 80)
    print(f"Chat: '{entity_name}'")
    print(f"Type: {type(entity).__name__}")
    print()
    print(
        f"API Count (placeholder):  {api_count:>15,}" if api_count else "API Count: N/A"
    )
    print(f"Real Count (iteration):   {real_count:>15,}")
    print()

    if api_count and api_count == 2147483647:
        print("🎯 CONFIRMATION:")
        print(f"   • API returns placeholder for PeerUser")
        print(f"   • Real message count: {real_count:,}")
        print(f"   • Difference: {abs(api_count - real_count):,}")

    print("=" * 80)
    print()

    await client.disconnect()
    print("✅ Test complete!\n")


if __name__ == "__main__":
    asyncio.run(main())
