import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from src.config import Config
from src.core_manager import ConnectionManager
from src.telegram_sharded_client import ShardedTelegramManager

# Load environment variables
load_dotenv()


async def test_sharded_manager():
    print("🧪 Testing ShardedTelegramManager Integration...")

    # 1. Setup Config
    config = Config.from_env()
    config.sharding_enabled = True
    config.shard_count = 4
    config.session_path = os.getenv("SESSION_NAME", "tobs_session")

    print(f"⚙️ Config: Sharding={config.sharding_enabled}, Workers={config.shard_count}")

    # 2. Setup Manager
    conn_manager = ConnectionManager(config)
    manager = ShardedTelegramManager(config, conn_manager)

    try:
        print("🔌 Connecting...")
        await manager.connect()

        if not await manager.client.is_user_authorized():
            print("❌ Not authorized")
            return

        # 3. Test Fetching
        target = "durov"  # Public channel with enough history
        print(f"🎯 Fetching from {target}...")

        count = 0
        async for msg in manager.fetch_messages(target, limit=2000):
            count += 1
            if count % 100 == 0:
                print(f"   Fetched {count} messages...")

        print(f"✅ Successfully fetched {count} messages via Sharded Manager!")

    except Exception as e:
        print(f"❌ Test Failed: {e}")
        import traceback

        traceback.print_exc()
    finally:
        await manager.disconnect()
        print("🔌 Disconnected.")


if __name__ == "__main__":
    asyncio.run(test_sharded_manager())
