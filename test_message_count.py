#!/usr/bin/env python3
"""
Test script to verify get_total_message_count() implementation.

This script connects to Telegram using your session and tests
the message count retrieval for your dialogs.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest, GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from telethon.tl.types.messages import Messages, MessagesSlice, ChannelMessages


# Your configuration from .env
API_ID = 1359771
API_HASH = "b355753834ef6c784224309eb1d1393d"
SESSION_NAME = "sessions/tobs_session"


async def get_message_count_method1(client: TelegramClient, entity) -> int:
    """
    Method 1: Using GetHistoryRequest with limit=1 (TOBS implementation).
    """
    try:
        input_peer = await client.get_input_entity(entity)
        
        result = await client(GetHistoryRequest(
            peer=input_peer,
            offset_id=0,
            offset_date=None,
            add_offset=0,
            limit=1,
            max_id=0,
            min_id=0,
            hash=0
        ))
        
        if isinstance(result, Messages):
            count = len(result.messages)
        elif isinstance(result, (MessagesSlice, ChannelMessages)):
            count = result.count
        else:
            count = getattr(result, 'count', 0)
        
        # Sanity check
        MAX_REASONABLE = 100_000_000
        if count > MAX_REASONABLE:
            return -1  # Indicate placeholder value
        
        return count
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return -2  # Indicate error


async def get_message_count_method2(client: TelegramClient, entity) -> int:
    """
    Method 2: Using get_messages(limit=0) to get total count.
    This is an alternative approach that might work better for some entities.
    """
    try:
        # get_messages with limit=0 returns metadata without actual messages
        result = await client.get_messages(entity, limit=0)
        return result.total if hasattr(result, 'total') else 0
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return -2


async def main():
    """Main test function."""
    print("=" * 70)
    print("TOBS Message Count Test")
    print("=" * 70)
    print()
    
    # Create client
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    
    await client.connect()
    
    if not await client.is_user_authorized():
        print("❌ Session not authorized. Please run main TOBS export first.")
        return
    
    print("✅ Connected to Telegram\n")
    
    # Get dialogs (last 30 chats)
    print("📥 Fetching dialogs...")
    dialogs = await client.get_dialogs(limit=30)
    print(f"✅ Found {len(dialogs)} dialogs\n")
    
    # Test message counting
    print("=" * 70)
    print("Testing message count methods:")
    print("=" * 70)
    print()
    
    results = []
    
    for dialog in dialogs:
        entity = dialog.entity
        name = dialog.name[:40]  # Truncate long names
        
        print(f"📊 {name}")
        
        # Method 1: GetHistoryRequest (TOBS implementation)
        count1 = await get_message_count_method1(client, entity)
        if count1 == -1:
            print(f"  ⚠️  Method 1: PLACEHOLDER (INT32_MAX detected)")
        elif count1 == -2:
            print(f"  ❌ Method 1: ERROR")
        else:
            print(f"  ✅ Method 1 (GetHistoryRequest): {count1:,} messages")
        
        # Method 2: get_messages(limit=0)
        count2 = await get_message_count_method2(client, entity)
        if count2 == -2:
            print(f"  ❌ Method 2: ERROR")
        else:
            print(f"  ✅ Method 2 (get_messages): {count2:,} messages")
        
        # Compare results
        if count1 >= 0 and count2 >= 0:
            if count1 == count2:
                print(f"  ✅ MATCH: Both methods agree")
            else:
                diff = abs(count1 - count2)
                print(f"  ⚠️  MISMATCH: Difference of {diff:,} messages")
        
        results.append((name, count1, count2))
        print()
    
    # Summary
    print("=" * 70)
    print("Summary (sorted by Method 1 count):")
    print("=" * 70)
    print()
    
    # Sort by Method 1 count (descending), handle special values
    def sort_key(item):
        count = item[1]
        if count < 0:
            return -1  # Put errors/placeholders at bottom
        return count
    
    results.sort(key=sort_key, reverse=True)
    
    print(f"{'Chat Name':<40} {'Method 1':>15} {'Method 2':>15}")
    print("-" * 70)
    
    for name, count1, count2 in results:
        c1_str = "PLACEHOLDER" if count1 == -1 else "ERROR" if count1 == -2 else f"{count1:,}"
        c2_str = "ERROR" if count2 == -2 else f"{count2:,}"
        print(f"{name:<40} {c1_str:>15} {c2_str:>15}")
    
    print()
    print("=" * 70)
    print("Legend:")
    print("  PLACEHOLDER = INT32_MAX detected (unrealistic count)")
    print("  ERROR       = API call failed")
    print("=" * 70)
    
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
