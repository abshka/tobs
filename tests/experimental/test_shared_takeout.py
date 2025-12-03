import asyncio
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, functions, types
from telethon.tl.functions import InvokeWithTakeoutRequest
from telethon.tl.functions.account import (
    FinishTakeoutSessionRequest,
    InitTakeoutSessionRequest,
)
from telethon.tl.functions.messages import GetHistoryRequest

# Load environment variables
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME", "tobs_session")
PHONE = os.getenv("PHONE_NUMBER")


async def test_shared_takeout():
    print("🧪 Starting Shared Takeout Experiment (Raw API Mode)...")

    # 1. Setup Paths
    base_session = Path(f"{SESSION_NAME}.session")
    worker_session = Path(f"{SESSION_NAME}_worker_test.session")

    if not base_session.exists():
        print(
            f"❌ Base session {base_session} not found! Run the main app first to login."
        )
        return

    # 2. Clone Session (Simulate Worker)
    print(f"📋 Cloning session to {worker_session}...")
    shutil.copy(base_session, worker_session)

    # 3. Initialize Master Client
    print("🔌 Connecting Master Client...")
    master = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await master.connect()

    if not await master.is_user_authorized():
        print("❌ Master not authorized.")
        return

    # 4. Initialize Worker Client
    print("🔌 Connecting Worker Client...")
    worker = TelegramClient(f"{SESSION_NAME}_worker_test", API_ID, API_HASH)
    await worker.connect()

    try:
        # 5. Start Takeout on Master (Raw Request)
        print("🚀 Initiating Takeout on Master (Raw Request)...")

        # Requesting permission for everything
        init_request = InitTakeoutSessionRequest(
            contacts=True,
            message_users=True,
            message_chats=True,
            message_megagroups=True,
            message_channels=True,
            files=True,
            file_max_size=10 * 1024 * 1024,  # 10MB limit for test
        )

        takeout_session = await master(init_request)
        takeout_id = takeout_session.id
        print(f"🔑 Takeout ID obtained: {takeout_id}")

        # 6. Test Master Request (Wrapped manually to be sure)
        print("📨 Sending request from Master...")
        # Just get self to test
        request_master = functions.users.GetFullUserRequest(id=types.InputUserSelf())
        wrapped_master = InvokeWithTakeoutRequest(
            takeout_id=takeout_id, query=request_master
        )
        await master(wrapped_master)
        print(f"✅ Master Success")

        # 7. Test Worker Request (Injecting Takeout ID)
        print("💉 Injecting Takeout ID into Worker request...")

        # Resolve entity first to avoid issues inside the wrapper
        peer = await worker.get_input_entity("me")

        request_worker = GetHistoryRequest(
            peer=peer,
            offset_id=0,
            offset_date=None,
            add_offset=0,
            limit=5,
            max_id=0,
            min_id=0,
            hash=0,
        )

        # WRAP IT!
        wrapped_worker = InvokeWithTakeoutRequest(
            takeout_id=takeout_id, query=request_worker
        )

        print("📨 Sending wrapped request from Worker...")
        result = await worker(wrapped_worker)

        if isinstance(
            result,
            (
                types.messages.Messages,
                types.messages.ChannelMessages,
                types.messages.MessagesSlice,
            ),
        ):
            print(f"✅ Worker Success! Fetched {len(result.messages)} messages.")
            print(
                "🎉 HYPOTHESIS CONFIRMED: Multiple connections can share one Takeout ID!"
            )
        else:
            print(f"❓ Worker got unexpected result: {type(result)}")

        # 8. Finish Takeout
        print("🏁 Finishing Takeout Session...")
        await master(
            InvokeWithTakeoutRequest(
                takeout_id=takeout_id, query=FinishTakeoutSessionRequest(success=True)
            )
        )
        print("✅ Takeout Session Closed.")

    except Exception as e:
        print(f"❌ Experiment Failed: {e}")
        # Try to close session if it failed mid-way
        try:
            if "takeout_id" in locals():
                await master(
                    InvokeWithTakeoutRequest(
                        takeout_id=takeout_id,
                        query=FinishTakeoutSessionRequest(success=False),
                    )
                )
        except:
            pass

    finally:
        await master.disconnect()
        await worker.disconnect()

        # Cleanup
        if worker_session.exists():
            os.remove(worker_session)
            print("🧹 Cleaned up worker session.")


if __name__ == "__main__":
    asyncio.run(test_shared_takeout())
