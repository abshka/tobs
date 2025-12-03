import asyncio
import logging
import math
import os
import pickle
import shutil
import struct
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from telethon import TelegramClient, functions, types, utils
from telethon.errors import FloodWaitError
from telethon.tl.functions import InvokeWithTakeoutRequest
from telethon.tl.functions.account import (
    FinishTakeoutSessionRequest,
    InitTakeoutSessionRequest,
)
from telethon.tl.functions.messages import GetHistoryRequest

from src.config import Config
from src.telegram_client import TelegramManager

logger = logging.getLogger(__name__)

# Try to use orjson for speed
try:
    import orjson as json
except ImportError:
    import json


class TakeoutWorkerClient(TelegramClient):
    """
    Worker client that automatically wraps all requests in a Takeout session.
    Crucial for high-speed media downloads.
    """

    def __init__(self, session, api_id, api_hash, takeout_id=None, **kwargs):
        super().__init__(session, api_id, api_hash, **kwargs)
        self.takeout_id = takeout_id

    async def __call__(self, request, ordered=False):
        if self.takeout_id:
            # Avoid double wrapping if request is already wrapped
            if not isinstance(request, InvokeWithTakeoutRequest):
                # We also need to check if it's a container or other special types?
                # Telethon handles containers.
                # For now, simple check is enough.
                request = InvokeWithTakeoutRequest(
                    takeout_id=self.takeout_id, query=request
                )
        return await super().__call__(request, ordered=ordered)


class ShardedTelegramManager(TelegramManager):
    """
    Extended TelegramManager that supports sharded parallel fetching using Takeout.
    """

    def __init__(self, config: Config, connection_manager: Any = None):
        super().__init__(config, connection_manager)
        self.worker_sessions: List[str] = []
        self.worker_clients: List[TelegramClient] = []
        self.takeout_id: Optional[int] = None
        self.worker_count = config.shard_count
        self.worker_stats: Dict[int, Dict[str, int]] = {}
        self._owned_takeout = False  # Flag to track if we created the session
        self._external_takeout_id: Optional[int] = (
            None  # ID provided externally (e.g. by run_export)
        )

    def get_worker_stats(self) -> Dict[int, Dict[str, int]]:
        """Returns statistics for each worker."""
        return self.worker_stats

    async def _setup_takeout(self) -> int:
        """Initializes Takeout session on the master client."""

        logger.info(
            f"DEBUG: _setup_takeout called. External ID: {self._external_takeout_id}"
        )

        # 1. Check for externally provided ID (from run_export)
        if self._external_takeout_id:
            logger.info(
                f"♻️ Reusing external Takeout Session (ID: {self._external_takeout_id})"
            )
            self._owned_takeout = False
            return self._external_takeout_id

        # 2. Check if client is already a TakeoutClient (reusing session from run_export)
        # Telethon's TakeoutClient usually has a .takeout_id attribute
        current_client = self.client
        client_takeout_id = getattr(
            current_client, "takeout_id", getattr(current_client, "_takeout_id", None)
        )

        if client_takeout_id:
            logger.info(f"♻️ Reusing existing Takeout Session (ID: {client_takeout_id})")
            self._owned_takeout = False
            return client_takeout_id

        # 3. Check if we are inside a context manager that wraps the client
        # This is tricky, but if we are here, it means we failed to find the ID.
        # If the client is a TakeoutClient but has no ID, it might be a proxy object.
        if type(current_client).__name__ == "TakeoutClient":
            logger.warning(
                "⚠️ Client is TakeoutClient but ID not found. Attempting to proceed without explicit ID (risky)."
            )
            # We can't return None here because the return type is int.
            # But if we proceed to init_request, it will fail.
            # Let's try to inspect the request wrapper if possible? No.
            pass

        logger.info("🚀 Initiating Takeout Session for Sharded Export...")
        try:
            # Calculate max file size from config (default 2GB)
            max_file_size = getattr(self.config, "max_file_size_mb", 2000) * 1024 * 1024

            init_request = InitTakeoutSessionRequest(
                contacts=True,
                message_users=True,
                message_chats=True,
                message_megagroups=True,
                message_channels=True,
                files=True,  # Enable files for worker-based media download
                file_max_size=max_file_size,
            )
            takeout_sess = await self.client(init_request)
            self._owned_takeout = True
            return takeout_sess.id
        except Exception as e:
            logger.error(f"Failed to init Takeout: {e}")
            # If we failed because another session exists, we might want to try to finish it?
            # But for now, just raise.
            raise

    async def _prepare_workers(self) -> List[str]:
        """Clones the main session for workers."""
        base_path = Path(self.config.session_path)
        if not base_path.exists():
            # Try adding .session extension if missing
            base_path = Path(f"{self.config.session_path}.session")

        if not base_path.exists():
            raise FileNotFoundError(f"Session file {base_path} not found")

        worker_sessions = []
        session_name_base = self.config.session_path.replace(".session", "")

        logger.info(f"📋 Cloning sessions for {self.worker_count} workers...")
        for i in range(self.worker_count):
            worker_sess_name = f"{session_name_base}_worker_{i}"
            worker_path = Path(f"{worker_sess_name}.session")
            # Always overwrite to ensure fresh state
            shutil.copy(base_path, worker_path)
            worker_sessions.append(worker_sess_name)

        return worker_sessions

    async def _cleanup_sharding(self):
        """Closes Takeout and removes worker sessions."""
        logger.info("🧹 Cleaning up sharding resources...")

        # Disconnect worker clients
        if hasattr(self, "worker_clients"):
            for client in self.worker_clients:
                try:
                    if client.is_connected():
                        await client.disconnect()
                except Exception as e:
                    logger.warning(f"Error disconnecting worker client: {e}")
            self.worker_clients.clear()

        # Close Takeout
        if self.client and self.takeout_id:
            if self._owned_takeout:
                try:
                    await self.client(
                        InvokeWithTakeoutRequest(
                            takeout_id=self.takeout_id,
                            query=FinishTakeoutSessionRequest(success=True),
                        )
                    )
                    logger.info("✅ Takeout session finished.")
                except Exception as e:
                    logger.warning(f"⚠️ Error finishing takeout: {e}")
            else:
                logger.info("♻️ Skipping Takeout finish (session owned by parent)")

        self.takeout_id = None
        self._owned_takeout = False

        # Remove session files
        for sess_name in self.worker_sessions:
            path = Path(f"{sess_name}.session")
            if path.exists():
                try:
                    os.remove(path)
                except OSError as e:
                    logger.warning(f"Failed to remove worker session {path}: {e}")

        self.worker_sessions = []

    async def _worker_task(
        self,
        worker_idx: int,
        client: TelegramClient,
        input_peer: types.TypeInputPeer,
        id_ranges: List[Tuple[int, int]],
        output_path: Path,
        takeout_id: int,
    ):
        """
        Worker loop: fetches messages and writes them to a temporary file.
        """
        # client is already connected and passed as argument

        # Initialize stats
        if worker_idx not in self.worker_stats:
            self.worker_stats[worker_idx] = {"messages": 0, "flood_waits": 0}

        try:
            # Open file for binary writing
            with open(output_path, "wb") as f:
                for start_id, end_id in id_ranges:
                    logger.debug(
                        f"👷 Worker {worker_idx} processing range {start_id}-{end_id}"
                    )

                    current_offset_id = end_id + 1

                    while current_offset_id > start_id:
                        limit = 100

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
                            takeout_id=takeout_id, query=req
                        )

                        while True:
                            try:
                                res = await client(wrapped_req)
                                break
                            except FloodWaitError as e:
                                logger.warning(
                                    f"⏳ Worker {worker_idx} hit FloodWait: {e.seconds}s"
                                )
                                self.worker_stats[worker_idx]["flood_waits"] += 1
                                await asyncio.sleep(e.seconds + 1)

                        if not res.messages:
                            break

                        # Serialize and write batch to file
                        if res.messages:
                            # Use length-prefixed framing for safe reading
                            data = pickle.dumps(res.messages)
                            f.write(struct.pack(">I", len(data)))
                            f.write(data)
                            f.flush()  # Ensure data is written to disk

                        fetched_count = len(res.messages)
                        self.worker_stats[worker_idx]["messages"] += fetched_count

                        if res.messages:
                            last_msg = res.messages[-1]
                            current_offset_id = last_msg.id

                        if fetched_count < limit:
                            break

        except Exception as e:
            logger.error(f"❌ Worker {worker_idx} failed: {e}")
        finally:
            # Client lifecycle is managed by fetch_messages
            pass

    async def fetch_messages(
        self,
        entity: Any,
        limit: Optional[int] = None,
        min_id: Optional[int] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> AsyncGenerator[types.Message, None]:
        """
        Overrides fetch_messages to use sharding if applicable.
        Falls back to super().fetch_messages() if sharding is disabled or not possible.
        """

        # Fallback conditions:
        # 1. Pagination is requested (sharding is for full export)
        # 2. Limit is small (overhead of sharding not worth it)
        # 3. Entity is not a channel/chat (User history might be sparse, but we tested it works)

        use_sharding = self.config.sharding_enabled

        if page is not None:
            use_sharding = False

        if limit and limit < 1000:
            use_sharding = False

        if not use_sharding:
            async for msg in super().fetch_messages(
                entity, limit, min_id, page, page_size
            ):
                yield msg
            return

        # --- Sharding Implementation ---

        try:
            # 1. Resolve Entity & Bounds
            # We need to resolve entity using the master client first
            # entity arg might be a string or InputPeer.
            # super().resolve_entity handles this but we need the result.

            # We use self.client directly as we are in the manager
            resolved_entity = await self.client.get_entity(entity)
            input_peer = utils.get_input_peer(resolved_entity)

            # Get bounds
            messages = await self.client.get_messages(resolved_entity, limit=1)
            if not messages:
                return  # Empty chat

            max_id = messages[0].id
            effective_min = min_id or 1

            if limit:
                # If limit is set, we only need the top N messages
                # So effective_min should be max_id - limit
                # But message IDs are not strictly sequential count.
                # Approximation:
                effective_min = max(1, max_id - limit)
                # This is risky if IDs are sparse.
                # Better: Fetch full range and stop when count reached?
                # For sharding, we usually want "All history".
                # If limit is set, sharding might be overkill or inaccurate.
                # Let's assume sharding is for "Full Export" mostly.

            logger.info(
                f"⚡ Starting Sharded Export for {utils.get_display_name(resolved_entity)}"
            )
            logger.info(f"📊 Range: {effective_min} -> {max_id}")

            # 2. Setup Takeout & Workers
            self.worker_stats = {}  # Reset stats for this run
            self.takeout_id = await self._setup_takeout()
            self.worker_sessions = await self._prepare_workers()

            # Initialize worker clients
            self.worker_clients.clear()
            for sess_name in self.worker_sessions:
                client = TakeoutWorkerClient(
                    sess_name,
                    self.config.api_id,
                    self.config.api_hash,
                    takeout_id=self.takeout_id,
                    request_retries=10,
                    connection_retries=20,
                    retry_delay=2,
                    timeout=60,  # Increase socket timeout
                )
                await client.connect()
                self.worker_clients.append(client)

            # 3. Calculate Ranges
            total_span = max_id - effective_min
            part_size = math.ceil(total_span / self.worker_count)

            ranges = []
            for i in range(self.worker_count):
                r_end = max_id - (i * part_size)
                r_start = max(effective_min, r_end - part_size)
                if r_start < r_end:
                    ranges.append((r_start, r_end))

            # 4. Start Workers with Temporary Files
            temp_dir = Path(self.config.export_path) / "temp_shards"
            temp_dir.mkdir(parents=True, exist_ok=True)

            worker_files = []
            tasks = []

            for i in range(len(ranges)):
                p = temp_dir / f"shard_{i}.bin"
                worker_files.append(p)
                task = asyncio.create_task(
                    self._worker_task(
                        i,
                        self.worker_clients[i],
                        input_peer,
                        [ranges[i]],
                        p,
                        self.takeout_id,
                    )
                )
                tasks.append(task)

            # 5. Yield from Files in Order
            count = 0

            # Iterate through workers in order (0 to N) to maintain message order
            for i, task in enumerate(tasks):
                f_path = worker_files[i]

                # Wait for file to be created
                while not f_path.exists() and not task.done():
                    await asyncio.sleep(0.1)

                if task.done():
                    exc = task.exception()
                    if exc:
                        raise exc

                if not f_path.exists():
                    logger.warning(f"Worker {i} did not create output file.")
                    continue

                with open(f_path, "rb") as f:
                    while True:
                        # Read length prefix
                        header = f.read(4)
                        while len(header) < 4:
                            if task.done():
                                exc = task.exception()
                                if exc:
                                    raise exc

                                # Try to read remaining bytes once more
                                header += f.read(4 - len(header))
                                break

                            await asyncio.sleep(0.1)
                            header += f.read(4 - len(header))

                        if len(header) < 4:
                            break  # EOF

                        length = struct.unpack(">I", header)[0]

                        # Read body
                        body = b""
                        while len(body) < length:
                            chunk = f.read(length - len(body))
                            if not chunk:
                                if task.done():
                                    exc = task.exception()
                                    if exc:
                                        raise exc

                                    chunk = f.read(length - len(body))
                                    if not chunk:
                                        break  # Truncated
                                else:
                                    await asyncio.sleep(0.1)
                                    continue
                            body += chunk

                        if len(body) < length:
                            break  # Truncated

                        batch = pickle.loads(body)

                        for msg in batch:
                            # Re-attach worker client to the message for parallel media download
                            msg._client = self.worker_clients[i]
                            yield msg
                            count += 1
                            if limit and count >= limit:
                                break

                        if limit and count >= limit:
                            break

                if limit and count >= limit:
                    break

            # Cancel remaining tasks if we exited early
            for task in tasks:
                if not task.done():
                    task.cancel()

        except Exception as e:
            logger.error(f"💥 Sharded export failed: {e}")
            raise e
        finally:
            await self._cleanup_sharding()
            # Cleanup temp files
            if "temp_dir" in locals() and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to remove temp dir {temp_dir}: {e}")
