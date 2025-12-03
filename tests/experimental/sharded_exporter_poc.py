import asyncio
import math
import os
import shutil

try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("🚀 Using uvloop")
except ImportError:
    print("⚠️ uvloop not found, using default asyncio loop")
import time
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv
from telethon import TelegramClient, functions, types, utils
from telethon.tl.functions import InvokeWithTakeoutRequest
from telethon.tl.functions.account import (
    FinishTakeoutSessionRequest,
    InitTakeoutSessionRequest,
)
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser

# Try to use orjson for speed, fallback to json
try:
    import orjson as json
except ImportError:
    import json

# Load environment variables
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME", "tobs_session")
PHONE = os.getenv("PHONE_NUMBER")

# Configuration
WORKER_COUNT = 8  # Number of parallel connections
CHUNK_SIZE = 1000  # Messages per request (Takeout allows high limits)
TOTAL_MESSAGES_LIMIT = 50000  # Limit for this test run (set None for all)


class ShardedTakeoutExporter:
    def __init__(self, session_name: str, api_id: int, api_hash: int, workers: int = 4):
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.worker_count = workers
        self.master_client = None
        self.takeout_id = None
        self.worker_sessions = []
        self.start_time = 0
        self.total_fetched = 0

    async def setup_master(self):
        """Connects master and initializes Takeout"""
        print(f"🔌 Connecting Master Client ({self.session_name})...")
        self.master_client = TelegramClient(
            self.session_name, self.api_id, self.api_hash
        )
        await self.master_client.connect()

        if not await self.master_client.is_user_authorized():
            raise Exception("Master client not authorized!")

        print("🚀 Initiating Takeout Session...")
        init_request = InitTakeoutSessionRequest(
            contacts=True,
            message_users=True,
            message_chats=True,
            message_megagroups=True,
            message_channels=True,
            files=False,  # We are testing message throughput first
            file_max_size=10 * 1024 * 1024,
        )
        takeout_sess = await self.master_client(init_request)
        self.takeout_id = takeout_sess.id
        print(f"🔑 Takeout ID: {self.takeout_id}")

    async def prepare_workers(self):
        """Clones sessions for workers"""
        base_path = Path(f"{self.session_name}.session")
        if not base_path.exists():
            raise FileNotFoundError(f"Session file {base_path} not found")

        print(f"📋 Cloning sessions for {self.worker_count} workers...")
        for i in range(self.worker_count):
            worker_sess_name = f"{self.session_name}_worker_{i}"
            worker_path = Path(f"{worker_sess_name}.session")
            shutil.copy(base_path, worker_path)
            self.worker_sessions.append(worker_sess_name)

    async def cleanup(self):
        """Closes takeout and removes worker sessions"""
        print("\n🧹 Cleaning up...")
        if self.master_client and self.takeout_id:
            try:
                await self.master_client(
                    InvokeWithTakeoutRequest(
                        takeout_id=self.takeout_id,
                        query=FinishTakeoutSessionRequest(success=True),
                    )
                )
                print("✅ Takeout session finished.")
            except Exception as e:
                print(f"⚠️ Error finishing takeout: {e}")

        if self.master_client:
            await self.master_client.disconnect()

        for sess_name in self.worker_sessions:
            path = Path(f"{sess_name}.session")
            if path.exists():
                os.remove(path)
        print("🗑️ Worker sessions deleted.")

    async def get_chat_bounds(
        self, chat_input: str
    ) -> Tuple[types.TypeInputPeer, int, int]:
        """Resolves chat and finds min/max message IDs"""
        entity = await self.master_client.get_entity(chat_input)
        input_peer = utils.get_input_peer(entity)

        # Get latest message ID
        messages = await self.master_client.get_messages(entity, limit=1)
        if not messages:
            raise Exception("Chat is empty")

        max_id = messages[0].id
        min_id = 1  # Simplified for now, ideally we find the first message

        print(f"🎯 Target: {utils.get_display_name(entity)} (ID: {entity.id})")
        print(f"📊 Message Range: {min_id} -> {max_id} (Total ~{max_id - min_id})")

        return input_peer, min_id, max_id

    async def worker_task(
        self, worker_idx: int, input_peer, id_ranges: List[Tuple[int, int]]
    ):
        """
        Worker loop: processes a list of ID ranges.
        """
        sess_name = self.worker_sessions[worker_idx]
        client = TelegramClient(sess_name, self.api_id, self.api_hash)
        await client.connect()

        # Output file for this worker
        out_filename = f"dump_worker_{worker_idx}.jsonl"

        count = 0
        try:
            with open(out_filename, "wb") as f:
                for start_id, end_id in id_ranges:
                    print(
                        f"👷 Worker {worker_idx} processing range {start_id}-{end_id}"
                    )

                    # Iterate backwards from end_id to start_id
                    current_offset_id = (
                        end_id + 1
                    )  # +1 because offset is exclusive usually, but let's check logic

                    while current_offset_id > start_id:
                        limit = 100  # Standard batch size

                        # Construct request
                        # We use min_id to stop fetching when we hit the bottom of our range
                        req = GetHistoryRequest(
                            peer=input_peer,
                            offset_id=current_offset_id,
                            offset_date=None,
                            add_offset=0,
                            limit=limit,
                            max_id=0,
                            min_id=start_id,
                            hash=0,
                        )

                        wrapped_req = InvokeWithTakeoutRequest(
                            takeout_id=self.takeout_id, query=req
                        )

                        res = await client(wrapped_req)

                        if not res.messages:
                            print(
                                f"⚠️ Worker {worker_idx} empty result at offset {current_offset_id}"
                            )
                            break

                        # Write to disk (simulate work)
                        for msg in res.messages:
                            # Minimal serialization for speed test
                            data = {
                                "id": msg.id,
                                "date": str(msg.date),
                                "text": msg.message,
                            }
                            f.write(json.dumps(data) + b"\n")

                        fetched_count = len(res.messages)
                        count += fetched_count
                        self.total_fetched += fetched_count

                        # Update offset for next batch
                        last_msg = res.messages[-1]
                        current_offset_id = last_msg.id

                        if fetched_count < limit:
                            break

        except Exception as e:
            print(f"❌ Worker {worker_idx} failed: {e}")
        finally:
            await client.disconnect()
            # print(f"✅ Worker {worker_idx} finished. Fetched: {count}")
            return count

    async def run(self, chat_input: str):
        try:
            overall_start = time.time()
            await self.setup_master()
            await self.prepare_workers()

            input_peer, min_id, max_id = await self.get_chat_bounds(chat_input)

            # Calculate ranges
            # We want to split the total ID space into chunks for workers
            # For a simple test, let's just split the range [max_id - limit, max_id]
            # But for a real speed test, let's take the last N messages

            target_count = TOTAL_MESSAGES_LIMIT
            if target_count > max_id:
                target_count = max_id

            effective_min = max(1, max_id - target_count)

            print(
                f"🧪 Fetching range: {effective_min} to {max_id} ({max_id - effective_min} msgs)"
            )

            # Divide this range into N parts
            total_span = max_id - effective_min
            part_size = math.ceil(total_span / self.worker_count)

            ranges = []
            for i in range(self.worker_count):
                # Ranges are (start, end)
                # Worker 0 gets the top range (newest), Worker N gets bottom (oldest)
                r_end = max_id - (i * part_size)
                r_start = max(effective_min, r_end - part_size)

                if r_start < r_end:
                    ranges.append((r_start, r_end))

            print(f"📊 Distribution: {ranges}")

            setup_duration = time.time() - overall_start
            print(f"⏱️ Setup duration: {setup_duration:.2f}s")

            self.start_time = time.time()

            # Launch workers
            tasks = []
            for i in range(len(ranges)):
                # Each worker gets ONE big range for now.
                # In production, we might want a queue of smaller chunks.
                tasks.append(self.worker_task(i, input_peer, [ranges[i]]))

            print("🏁 Starting workers...")
            results = await asyncio.gather(*tasks)

            duration = time.time() - self.start_time
            total = sum(results)

            print(f"\n📈 RESULTS:")
            print(f"   Messages: {total}")
            print(f"   Fetch Time: {duration:.2f}s")
            print(f"   Total Time: {time.time() - overall_start:.2f}s")
            print(f"   Fetch Speed: {total / duration:.2f} msg/s")

        except Exception as e:
            print(f"💥 Critical Error: {e}")
            import traceback

            traceback.print_exc()
        finally:
            await self.cleanup()


if __name__ == "__main__":
    # Usage: python3 tests/experimental/sharded_exporter_poc.py <chat_username_or_link>
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "me"

    exporter = ShardedTakeoutExporter(
        SESSION_NAME, API_ID, API_HASH, workers=WORKER_COUNT
    )
    asyncio.run(exporter.run(target))
