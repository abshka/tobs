import asyncio
import os
import msgpack  # S-3: Security fix - replaced pickle with msgpack
import shutil
import struct
import time
import zlib
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from telethon import TelegramClient, types, utils
from telethon.errors import FloodWaitError
from telethon.tl.functions import InvokeWithTakeoutRequest
from telethon.tl.functions.account import (
    FinishTakeoutSessionRequest,
    InitTakeoutSessionRequest,
)
from telethon.tl.functions.messages import GetHistoryRequest

from src.config import Config
from src.logging_context import set_worker_context
from src.telegram_client import TelegramManager
from src.utils import logger  # Use configured logger from utils


# 🚀 NEW: In-Memory Message Queue for Sharding
class MessageQueue:
    """
    Thread-safe async queue for streaming messages from workers.
    Avoids serialization issues by keeping Message objects in memory.
    """
    
    def __init__(self, worker_id: int, max_size: int = 10000):
        self.worker_id = worker_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self.finished = False
        self.error: Optional[Exception] = None
        self.message_count = 0
    
    async def put(self, message: types.Message):
        """Add a message to the queue."""
        await self.queue.put(message)
        self.message_count += 1
    
    async def get(self) -> Optional[types.Message]:
        """Get a message from the queue. Returns None when finished."""
        if self.queue.empty() and self.finished:
            return None
        try:
            msg = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            return msg
        except asyncio.TimeoutError:
            # Check if we're finished after timeout
            if self.finished:
                return None
            # Otherwise just return None to let caller retry
            return None
    
    def mark_finished(self, error: Optional[Exception] = None):
        """Mark this queue as finished (no more messages will be added)."""
        self.finished = True
        self.error = error
        logger.debug(f"Worker {self.worker_id} queue marked finished: {self.message_count} messages")
    
    def is_finished(self) -> bool:
        """Check if queue is finished and empty."""
        return self.finished and self.queue.empty()
    
    def size(self) -> int:
        """Get current queue size."""
        return self.queue.qsize()


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
        return await super().__call__(request)


class ShardedTelegramManager(TelegramManager):
    """
    Extended TelegramManager that supports sharded parallel fetching using Takeout.
    """

    def __init__(
        self, config: Config, connection_manager: Any = None, cache_manager: Any = None
    ):
        super().__init__(config, connection_manager, cache_manager)
        self.worker_sessions: List[str] = []
        self.worker_clients: List[TelegramClient] = []
        self.takeout_id: Optional[int] = None
        self.worker_count = config.shard_count
        self.worker_stats: Dict[int, Dict[str, Any]] = {}
        self._owned_takeout = False  # Flag to track if we created the session
        self._external_takeout_id: Optional[int] = (
            None  # ID provided externally (e.g. by run_export)
        )
        self.current_entity_dc: int = 0  # DC ID of current entity being exported

    def get_worker_stats(self) -> Dict[int, Dict[str, Any]]:
        """Returns statistics for each worker."""
        return self.worker_stats

    async def _setup_takeout(self) -> int:
        """Initializes Takeout session on the master client."""
        # ... existing code ...
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
            return int(client_takeout_id)  # type: ignore

        # ... rest of function ...

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
            return takeout_sess.id  # type: ignore
        except Exception as e:
            logger.error(f"Failed to init Takeout: {e}")
            # If we failed because another session exists, we might want to try to finish it?
            # But for now, just raise.
            raise

    async def _prepare_workers(self) -> List[str]:
        """Clones the main session for workers."""
        base_path = Path(self.config.session_name)
        if not base_path.exists():
            # Try adding .session extension if missing
            base_path = Path(f"{self.config.session_name}.session")

        if not base_path.exists():
            raise FileNotFoundError(f"Session file {base_path} not found")

        worker_sessions = []
        session_name_base = self.config.session_name.replace(".session", "")

        logger.info(f"📋 Cloning sessions for {self.worker_count} workers...")
        for i in range(self.worker_count):
            worker_sess_name = f"{session_name_base}_worker_{i}"
            worker_path = Path(f"{worker_sess_name}.session")
            # Always overwrite to ensure fresh state
            shutil.copy(base_path, worker_path)
            worker_sessions.append(worker_sess_name)

        return worker_sessions  # type: ignore

    def _extract_dc_id(self, entity_or_peer: Any) -> int:
        """
        Extract DC (datacenter) ID from entity or peer.
        Returns DC ID or 0 if unable to determine.
        """
        try:
            # Try to get DC from photo
            if hasattr(entity_or_peer, "photo") and entity_or_peer.photo:
                if hasattr(entity_or_peer.photo, "dc_id"):
                    return int(entity_or_peer.photo.dc_id)

            # Try to get from access_hash (channel/user)
            if hasattr(entity_or_peer, "access_hash"):
                # DC ID is typically encoded in access_hash for channels
                # This is a heuristic, not guaranteed
                pass

            # For InputPeer types
            if isinstance(
                entity_or_peer, (types.InputPeerChannel, types.InputPeerUser)
            ):  # type: ignore
                # Unfortunately, InputPeer doesn't carry DC info directly
                # We'd need to store it from the original entity
                pass

            return 0  # Unknown DC
        except Exception as e:
            logger.debug(f"Could not extract DC ID: {e}")
            return 0

    def _to_lightweight_message_dict(self, message: types.Message) -> Dict[str, Any]:
        """Converts a Telethon Message object to a lightweight dictionary with only serializable primitives."""
        # Convert peer_id to int if it's a Peer object
        peer_id_value = None
        if hasattr(message, "peer_id") and message.peer_id:
            if hasattr(message.peer_id, "user_id"):
                peer_id_value = message.peer_id.user_id
            elif hasattr(message.peer_id, "channel_id"):
                peer_id_value = message.peer_id.channel_id
            elif hasattr(message.peer_id, "chat_id"):
                peer_id_value = message.peer_id.chat_id
            else:
                peer_id_value = str(message.peer_id)  # Fallback to string
        
        lightweight_dict = {
            "id": message.id,
            "peer_id": peer_id_value,
            "date": message.date.isoformat() if message.date else None,  # Serialize datetime
            "message": message.message,
            "out": message.out,
            "mentioned": message.mentioned,
            "media_unread": message.media_unread,
            "silent": message.silent,
            "post": message.post,
            "from_scheduled": message.from_scheduled,
            "legacy": message.legacy,
            "edit_hide": message.edit_hide,
            "pinned": message.pinned,
            "noforwards": message.noforwards,
            # Skip complex objects that aren't needed for basic export
            # "reactions": None,  # Not needed for export
            # "replies": None,  # Not needed for export
            # "forwards": None,  # Not needed for export
            "via_bot_id": message.via_bot_id,
            # "reply_to": None,  # Complex object, skip for now
            # "fwd_from": None,  # Complex object, skip for now
            # "entities": None,  # Complex object, skip for now
            # Store only essential media info, not the full object
            "has_media": message.media is not None,
            "has_file": hasattr(message, "file") and message.file is not None,
            "has_photo": hasattr(message, "photo") and message.photo is not None,
            "has_document": hasattr(message, "document") and message.document is not None,
        }

        # Handle sender information (convert to primitives)
        if message.sender_id:
            lightweight_dict["sender_id"] = message.sender_id
        if message.sender:
            lightweight_dict["sender_id"] = message.sender.id
            lightweight_dict["sender_username"] = getattr(message.sender, "username", None)
            lightweight_dict["sender_first_name"] = getattr(message.sender, "first_name", None)
            lightweight_dict["sender_last_name"] = getattr(message.sender, "last_name", None)

        return lightweight_dict

    async def _log_slow_chunks_statistics(self):
        """
        Aggregate and log statistics about slow chunks across all workers.
        Provides insights into problematic ID ranges and their impact.
        """
        all_slow_chunks = []

        # Collect slow chunks from all worker stats
        for i, stats in enumerate(self.worker_stats.values()):
            if "slow_chunks" in stats:
                for chunk in stats["slow_chunks"]:
                    chunk["worker_id"] = i
                    all_slow_chunks.append(chunk)

        if not all_slow_chunks:
            logger.info("✅ No slow chunks detected (all chunks completed in <2s)")
            return

        # Calculate statistics
        total_slow_chunks = len(all_slow_chunks)
        split_attempts = sum(
            1 for c in all_slow_chunks if c.get("action") == "split_attempted"
        )
        avg_slow_duration = (
            sum(c["duration_sec"] for c in all_slow_chunks) / total_slow_chunks
        )
        max_slow_chunk = max(all_slow_chunks, key=lambda c: c["duration_sec"])

        # DC-aware statistics
        dc_stats: Dict[int, Dict[str, Any]] = {}
        for chunk in all_slow_chunks:
            dc = chunk.get("dc_id", 0)
            if dc not in dc_stats:
                dc_stats[dc] = {"count": 0, "total_duration": 0.0, "chunks": []}
            dc_stats[dc]["count"] += 1
            dc_stats[dc]["total_duration"] += chunk["duration_sec"]
            dc_stats[dc]["chunks"].append(chunk)

        # Find most problematic ID ranges (sort by duration)
        top_slow_chunks = sorted(
            all_slow_chunks, key=lambda c: c["duration_sec"], reverse=True
        )[:5]

        logger.warning(
            f"🐢 Slow Chunks Summary: {total_slow_chunks} chunks >2s detected, "
            f"{split_attempts} split attempts, avg {avg_slow_duration:.1f}s"
        )

        max_dc_str = (
            f"DC{max_slow_chunk.get('dc_id', 0)}"
            if max_slow_chunk.get("dc_id", 0) > 0
            else "DC?"
        )
        logger.warning(
            f"   Slowest chunk: {max_slow_chunk['start_id']}-{max_slow_chunk['end_id']} "
            f"took {max_slow_chunk['duration_sec']:.1f}s "
            f"(worker {max_slow_chunk['worker_id']}, {max_slow_chunk['messages']} msgs, {max_dc_str})"
        )

        # Log DC-specific statistics
        if dc_stats:
            logger.info("📍 Slow chunks by Datacenter:")
            for dc_id in sorted(dc_stats.keys()):
                dc_data = dc_stats[dc_id]
                avg_dc_duration = dc_data["total_duration"] / dc_data["count"]
                dc_label = f"DC{dc_id}" if dc_id > 0 else "Unknown DC"
                logger.info(
                    f"   {dc_label}: {dc_data['count']} chunks, "
                    f"avg {avg_dc_duration:.1f}s, "
                    f"total {dc_data['total_duration']:.1f}s"
                )

        if len(top_slow_chunks) > 1:
            logger.info("📊 Top 5 slowest ID ranges:")
            for idx, chunk in enumerate(top_slow_chunks, 1):
                chunk_dc_str = (
                    f"DC{chunk.get('dc_id', 0)}" if chunk.get("dc_id", 0) > 0 else "DC?"
                )
                logger.info(
                    f"   {idx}. {chunk['start_id']:,}-{chunk['end_id']:,}: "
                    f"{chunk['duration_sec']:.1f}s ({chunk['messages']} msgs, "
                    f"{chunk_dc_str}, worker {chunk['worker_id']}, {chunk['action']})"
                )

        # NEW: Update hot zones database from patterns
        if self.config.enable_hot_zones and all_slow_chunks:
            try:
                from src.hot_zones_manager import HotZonesManager

                hot_zones_mgr = HotZonesManager(self.config)

                # Analyze each slow chunk and update hot zones
                for chunk in all_slow_chunks:
                    hot_zones_mgr.analyze_and_update_hot_zones(chunk)

                # Save updated database
                hot_zones_mgr.save_database()
                logger.info(
                    f"💾 Updated hot zones database: {hot_zones_mgr.slow_chunk_db_path}"
                )

                # Print actionable recommendations
                recommendations = hot_zones_mgr.get_recommendations()
                if recommendations:
                    logger.info("💡 Recommendations for future exports:")
                    for rec in recommendations:
                        logger.info(f"   • {rec}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to update hot zones database: {e}")

    async def _cleanup_sharding(self):
        """Closes Takeout and removes worker sessions."""
        logger.info("🧹 Cleaning up sharding resources...")

        # Disconnect worker clients
        if hasattr(self, "worker_clients"):
            for client in self.worker_clients:
                try:
                    if client.is_connected():  # type: ignore
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
        input_peer: "types.TypeInputPeer",  # type: ignore
        id_ranges: List[Tuple[int, int]],
        output_path: Path,
        takeout_id: int,
        task_queue: Optional[asyncio.Queue[Any]] = None,
    ):
        """
        Worker loop: fetches messages and writes them to a temporary file.
        Collects telemetry: latency, IO time, request count.

        NEW: Supports dynamic work stealing via task_queue.
        If task_queue is provided, worker pulls tasks dynamically instead of using fixed id_ranges.
        """
        # client is already connected and passed as argument

        set_worker_context(worker_idx)  # Set context for all logs from this worker

        # Initialize extended stats with telemetry
        if worker_idx not in self.worker_stats:
            self.worker_stats[worker_idx] = {
                "messages": 0,
                "flood_waits": 0,
                "requests": 0,
                "total_latency_ms": 0,
                "io_time_ms": 0,
                "chunks_processed": 0,  # NEW: track number of chunks
                "slow_chunks": [],  # NEW: track slow chunks for statistics
            }

        stats = self.worker_stats[worker_idx]

        try:
            # Open file for binary writing
            with open(output_path, "wb") as f:
                # NEW: Dynamic work stealing mode
                if task_queue is not None:
                    logger.debug(
                        f"👷 Worker {worker_idx} starting in DYNAMIC mode (work stealing)"
                    )

                    while True:
                        try:
                            # Non-blocking get: if queue is empty, worker is done
                            chunk = task_queue.get_nowait()
                            start_id, end_id = chunk
                            stats["chunks_processed"] += 1

                            logger.debug(
                                f"👷 Worker {worker_idx} grabbed chunk #{stats['chunks_processed']}: {start_id}-{end_id}"
                            )

                            # Process this chunk with config parameters
                            await self._fetch_chunk(
                                worker_idx,
                                client,
                                input_peer,
                                start_id,
                                end_id,
                                f,
                                stats,
                                takeout_id,
                                slow_chunk_threshold=self.config.slow_chunk_threshold,
                                max_retries=self.config.slow_chunk_max_retries,
                            )

                            task_queue.task_done()

                        except asyncio.QueueEmpty:
                            # No more tasks, worker finishes
                            logger.debug(
                                f"✅ Worker {worker_idx} finished (queue empty, processed {stats['chunks_processed']} chunks)"
                            )
                            break

                # OLD: Static range assignment mode (fallback)
                else:
                    logger.debug(
                        f"👷 Worker {worker_idx} starting in STATIC mode (fixed ranges)"
                    )

                    for start_id, end_id in id_ranges:
                        logger.debug(
                            f"👷 Worker {worker_idx} processing range {start_id}-{end_id}"
                        )

                        await self._fetch_chunk(
                            worker_idx,
                            client,
                            input_peer,
                            start_id,
                            end_id,
                            f,
                            stats,
                            takeout_id,
                            slow_chunk_threshold=self.config.slow_chunk_threshold,
                            max_retries=self.config.slow_chunk_max_retries,
                        )

        except Exception as e:
            logger.error(f"❌ Worker {worker_idx} failed: {e}")
        finally:
            # Log final telemetry for this worker
            if stats["requests"] > 0:
                avg_latency = stats["total_latency_ms"] / stats["requests"]
                logger.debug(
                    f"Worker {worker_idx} telemetry: "
                    f"{stats['messages']} messages, "
                    f"{stats['requests']} requests, "
                    f"avg latency {avg_latency:.1f}ms, "
                    f"total IO {stats['io_time_ms']}ms, "
                    f"chunks {stats.get('chunks_processed', 'N/A')}"
                )

    async def _worker_task_streaming(
        self,
        worker_idx: int,
        client: TelegramClient,
        input_peer: "types.TypeInputPeer",  # type: ignore
        id_ranges: List[Tuple[int, int]],
        message_queue: MessageQueue,
        takeout_id: int,
        task_queue: Optional[asyncio.Queue[Any]] = None,
    ):
        """
        🚀 NEW: Streaming worker that uses in-memory MessageQueue instead of files.
        
        Worker loop: fetches messages and streams them directly to MessageQueue.
        No serialization, no file I/O - Message objects stay in memory.
        Collects telemetry: latency, streaming time, request count.

        Supports dynamic work stealing via task_queue.
        If task_queue is provided, worker pulls tasks dynamically instead of using fixed id_ranges.
        """
        set_worker_context(worker_idx)  # Set context for all logs from this worker

        # Initialize extended stats with telemetry
        if worker_idx not in self.worker_stats:
            self.worker_stats[worker_idx] = {
                "messages": 0,
                "flood_waits": 0,
                "requests": 0,
                "total_latency_ms": 0,
                "stream_time_ms": 0,  # Replace io_time with stream_time
                "chunks_processed": 0,
                "slow_chunks": [],
            }

        stats = self.worker_stats[worker_idx]

        try:
            # NEW: Dynamic work stealing mode
            if task_queue is not None:
                logger.debug(
                    f"👷 Worker {worker_idx} starting in DYNAMIC STREAMING mode (work stealing)"
                )

                while True:
                    try:
                        # Non-blocking get: if queue is empty, worker is done
                        chunk = task_queue.get_nowait()
                        start_id, end_id = chunk
                        stats["chunks_processed"] += 1

                        logger.debug(
                            f"👷 Worker {worker_idx} grabbed chunk #{stats['chunks_processed']}: {start_id}-{end_id}"
                        )

                        # Process this chunk with streaming method
                        await self._fetch_chunk_streaming(
                            worker_idx,
                            client,
                            input_peer,
                            start_id,
                            end_id,
                            message_queue,
                            stats,
                            takeout_id,
                            slow_chunk_threshold=self.config.slow_chunk_threshold,
                            max_retries=self.config.slow_chunk_max_retries,
                        )

                        task_queue.task_done()

                    except asyncio.QueueEmpty:
                        # No more tasks, worker finishes
                        logger.debug(
                            f"✅ Worker {worker_idx} finished (queue empty, processed {stats['chunks_processed']} chunks)"
                        )
                        break

            # OLD: Static range assignment mode (fallback)
            else:
                logger.debug(
                    f"👷 Worker {worker_idx} starting in STATIC STREAMING mode (fixed ranges)"
                )

                for start_id, end_id in id_ranges:
                    logger.debug(
                        f"👷 Worker {worker_idx} processing range {start_id}-{end_id}"
                    )

                    await self._fetch_chunk_streaming(
                        worker_idx,
                        client,
                        input_peer,
                        start_id,
                        end_id,
                        message_queue,
                        stats,
                        takeout_id,
                        slow_chunk_threshold=self.config.slow_chunk_threshold,
                        max_retries=self.config.slow_chunk_max_retries,
                    )

        except Exception as e:
            logger.error(f"❌ Worker {worker_idx} failed: {e}")
            message_queue.mark_finished(error=e)
        finally:
            # Mark queue as finished (no more messages will be added)
            if not message_queue.finished:
                message_queue.mark_finished()
            
            # Log final telemetry for this worker
            if stats["requests"] > 0:
                avg_latency = stats["total_latency_ms"] / stats["requests"]
                logger.debug(
                    f"Worker {worker_idx} telemetry: "
                    f"{stats['messages']} messages, "
                    f"{stats['requests']} requests, "
                    f"avg latency {avg_latency:.1f}ms, "
                    f"total streaming {stats['stream_time_ms']}ms, "
                    f"chunks {stats.get('chunks_processed', 'N/A')}"
                )

    async def _fetch_chunk(
        self,
        worker_idx: int,
        client: TelegramClient,
        input_peer: "types.TypeInputPeer",  # type: ignore
        start_id: int,
        end_id: int,
        f,
        stats: dict,
        takeout_id: int,
        slow_chunk_threshold: float = 10.0,
        max_retries: int = 2,
    ):
        """
        Fetch a single chunk of messages (start_id to end_id) and write to file.

        Features:
        - Adaptive splitting: automatically divides slow chunks into smaller sub-chunks
        - Retry with exponential backoff for failed chunks
        - Detailed timing and warning logs for slow operations

        Args:
            slow_chunk_threshold: Time in seconds after which chunk is considered slow (default: 10s)
            max_retries: Maximum retry attempts with adaptive splitting (default: 2)
        """
        chunk_start_time = time.time()
        chunk_messages = 0
        chunk_span = end_id - start_id

        # Track slow chunks for statistics
        if "slow_chunks" not in stats:
            stats["slow_chunks"] = []

        # Buffer to collect messages BEFORE writing (so we can split if needed)
        message_buffer = []

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

            wrapped_req = InvokeWithTakeoutRequest(takeout_id=takeout_id, query=req)

            # Measure API request latency
            request_start = time.time()
            while True:
                try:
                    stats["requests"] += 1
                    res = await client(wrapped_req)
                    break
                except FloodWaitError as e:
                    logger.warning(
                        f"⏳ Worker {worker_idx} hit FloodWait: {e.seconds}s"
                    )
                    stats["flood_waits"] += 1
                    await asyncio.sleep(e.seconds + 1)

            # Record request latency
            request_latency_ms = (time.time() - request_start) * 1000
            stats["total_latency_ms"] += int(request_latency_ms)

            if not res.messages:
                break

            # Collect messages in buffer instead of writing immediately
            message_buffer.extend(res.messages)

            fetched_count = len(res.messages)
            chunk_messages += fetched_count

            if res.messages:
                last_msg = res.messages[-1]
                current_offset_id = last_msg.id

            if fetched_count < limit:
                break

        # 🔍 Check chunk performance BEFORE writing
        chunk_duration = time.time() - chunk_start_time

        # Case 1: Extremely slow chunk (>slow_chunk_threshold) - SPLIT instead of writing
        if (
            chunk_duration > slow_chunk_threshold
            and chunk_span > 1000
            and max_retries > 0
        ):
            dc_str = (
                f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
            )
            logger.warning(
                f"🐢 Worker {worker_idx} VERY SLOW chunk: {start_id}-{end_id} "
                f"(span: {chunk_span:,} IDs, {chunk_messages} msgs, {dc_str}) took {chunk_duration:.1f}s"
            )
            logger.info(
                f"🔄 Worker {worker_idx} DISCARDING buffer and re-fetching as 4 sub-chunks (adaptive split)..."
            )

            # Record this slow chunk for statistics
            stats["slow_chunks"].append(
                {
                    "start_id": start_id,
                    "end_id": end_id,
                    "duration_sec": chunk_duration,
                    "messages": chunk_messages,
                    "action": "split_attempted",
                    "dc_id": self.current_entity_dc,  # Add DC ID
                }
            )

            # IMPORTANT: Don't write the buffer! Discard it and re-fetch as sub-chunks
            # This prevents duplicate messages
            message_buffer = []

            # Split into 4 smaller sub-chunks
            sub_chunk_size = chunk_span // 4
            sub_chunks = []
            for i in range(4):
                sub_start = start_id + (i * sub_chunk_size)
                sub_end = start_id + ((i + 1) * sub_chunk_size) if i < 3 else end_id
                sub_chunks.append((sub_start, sub_end))

            # Recursively fetch sub-chunks with reduced retry count
            for sub_start, sub_end in sub_chunks:
                logger.debug(
                    f"  🔹 Worker {worker_idx} fetching sub-chunk {sub_start}-{sub_end}"
                )
                await self._fetch_chunk(
                    worker_idx,
                    client,
                    input_peer,
                    sub_start,
                    sub_end,
                    f,
                    stats,
                    takeout_id,
                    slow_chunk_threshold=slow_chunk_threshold,
                    max_retries=max_retries
                    - 1,  # Reduce retry count to prevent infinite recursion
                )

            # Early return - sub-chunks handle their own stats
            return

        # Case 2 & 3: Write messages to file (either moderately slow or fast)
        if message_buffer:
            io_start = time.time()

            # Serialization & Compression
            # CRITICAL: ALWAYS use lightweight schema - Message objects are not serializable
            logger.debug(
                f"Worker {worker_idx}: Using lightweight schema for serialization"
            )
            serialized_data = [
                self._to_lightweight_message_dict(msg) for msg in message_buffer
            ]

            # S-3: Security fix - use msgpack instead of pickle
            data = msgpack.packb(serialized_data, use_bin_type=True)
            is_compressed = 0

            if getattr(self.config, "shard_compression_enabled", True):
                try:
                    level = getattr(self.config, "shard_compression_level", 1)
                    compressed = zlib.compress(data, level=level)
                    # Only use compression if it actually saves space
                    if len(compressed) < len(data):
                        data = compressed
                        is_compressed = 1
                except Exception as e:
                    logger.warning(f"Compression failed, using raw data: {e}")

            # Format: [Length: 4 bytes] [Flag: 1 byte] [Data]
            f.write(struct.pack(">I", len(data)))
            f.write(struct.pack(">B", is_compressed))
            f.write(data)
            f.flush()  # Ensure data is written to disk

            # Record IO time and message count
            io_time_ms = (time.time() - io_start) * 1000
            stats["io_time_ms"] += int(io_time_ms)
            stats["messages"] += (
                chunk_messages  # Update stats ONLY when actually written
            )

        # Log performance
        if chunk_duration > 2.0:
            dc_str = (
                f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
            )
            logger.warning(
                f"⚠️ Worker {worker_idx} slow chunk: {start_id}-{end_id} "
                f"(span: {chunk_span:,} IDs, {chunk_messages} msgs, {dc_str}) took {chunk_duration:.1f}s"
            )

            slow_chunk_info = {
                "start_id": start_id,
                "end_id": end_id,
                "duration_sec": chunk_duration,
                "messages": chunk_messages,
                "action": "logged",
                "dc_id": self.current_entity_dc,  # Add DC ID
            }
            stats["slow_chunks"].append(slow_chunk_info)

            # NEW: Record to persistent database if significantly slow
            if chunk_duration > self.config.slow_chunk_threshold:
                try:
                    from src.hot_zones_manager import HotZonesManager, SlowChunkRecord

                    hot_zones_mgr = HotZonesManager(self.config)

                    density = (
                        (chunk_messages / chunk_span * 1000) if chunk_span > 0 else 0
                    )

                    slow_record = SlowChunkRecord(
                        id_range=(start_id, end_id),
                        duration_sec=chunk_duration,
                        message_count=chunk_messages,
                        density=density,
                        datacenter=dc_str,
                        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        worker_id=worker_idx,
                    )

                    hot_zones_mgr.record_slow_chunk(slow_record)
                    hot_zones_mgr.save_database()
                    logger.debug(
                        f"💾 Recorded slow chunk to database: {start_id}-{end_id}"
                    )
                except Exception as e:
                    logger.debug(f"⚠️ Failed to record slow chunk to database: {e}")
        else:
            logger.debug(
                f"✅ Worker {worker_idx} chunk {start_id}-{end_id}: "
                f"{chunk_messages} msgs in {chunk_duration:.1f}s"
            )

    async def _fetch_chunk_streaming(
        self,
        worker_idx: int,
        client: TelegramClient,
        input_peer: "types.TypeInputPeer",  # type: ignore
        start_id: int,
        end_id: int,
        message_queue: MessageQueue,
        stats: dict,
        takeout_id: int,
        slow_chunk_threshold: float = 10.0,
        max_retries: int = 2,
    ):
        """
        🚀 NEW: Streaming version that puts messages directly into MessageQueue.
        Avoids serialization entirely - Message objects stay in memory.

        Features:
        - Adaptive splitting: automatically divides slow chunks into smaller sub-chunks
        - Retry with exponential backoff for failed chunks
        - Direct streaming to in-memory queue (no file I/O)
        - Detailed timing and warning logs for slow operations

        Args:
            message_queue: MessageQueue to stream messages into
            slow_chunk_threshold: Time in seconds after which chunk is considered slow (default: 10s)
            max_retries: Maximum retry attempts with adaptive splitting (default: 2)
        """
        chunk_start_time = time.time()
        chunk_messages = 0
        chunk_span = end_id - start_id

        # Track slow chunks for statistics
        if "slow_chunks" not in stats:
            stats["slow_chunks"] = []

        # Buffer to collect messages BEFORE streaming (so we can split if needed)
        message_buffer = []

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

            wrapped_req = InvokeWithTakeoutRequest(takeout_id=takeout_id, query=req)

            # Measure API request latency
            request_start = time.time()
            while True:
                try:
                    stats["requests"] += 1
                    res = await client(wrapped_req)
                    break
                except FloodWaitError as e:
                    logger.warning(
                        f"⏳ Worker {worker_idx} hit FloodWait: {e.seconds}s"
                    )
                    stats["flood_waits"] += 1
                    await asyncio.sleep(e.seconds + 1)

            # Record request latency
            request_latency_ms = (time.time() - request_start) * 1000
            stats["total_latency_ms"] += int(request_latency_ms)

            if not res.messages:
                break

            # Collect messages in buffer
            message_buffer.extend(res.messages)

            fetched_count = len(res.messages)
            chunk_messages += fetched_count

            if res.messages:
                last_msg = res.messages[-1]
                current_offset_id = last_msg.id

            if fetched_count < limit:
                break

        # 🔍 ENHANCEMENT: Batch-load sender entities for all messages
        # GetHistoryRequest doesn't populate message.sender, so we need to fetch them
        if message_buffer:
            unique_sender_ids = set()
            for msg in message_buffer:
                if msg.sender_id and not msg.sender:  # Has ID but no sender object
                    unique_sender_ids.add(msg.sender_id)
            
            if unique_sender_ids:
                try:
                    # Batch fetch all sender entities at once (efficient!)
                    sender_entities = await client.get_entity(list(unique_sender_ids))
                    
                    # Handle single entity result (not a list)
                    if not isinstance(sender_entities, list):
                        sender_entities = [sender_entities]
                    
                    # Create lookup dict
                    sender_lookup = {entity.id: entity for entity in sender_entities if hasattr(entity, 'id')}
                    
                    # Attach sender objects to messages
                    for msg in message_buffer:
                        if msg.sender_id in sender_lookup:
                            msg.sender = sender_lookup[msg.sender_id]
                    
                    logger.debug(f"✅ Worker {worker_idx} loaded {len(sender_lookup)} sender entities")
                    
                except Exception as e:
                    # Fallback: if batch fetch fails, messages will use "User ID" format
                    logger.debug(f"⚠️ Worker {worker_idx} failed to batch-load senders: {e}")

        # 🔍 Check chunk performance BEFORE streaming
        chunk_duration = time.time() - chunk_start_time

        # Case 1: Extremely slow chunk (>slow_chunk_threshold) - SPLIT instead of streaming
        if (
            chunk_duration > slow_chunk_threshold
            and chunk_span > 1000
            and max_retries > 0
        ):
            dc_str = (
                f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
            )
            logger.warning(
                f"🐢 Worker {worker_idx} VERY SLOW chunk: {start_id}-{end_id} "
                f"(span: {chunk_span:,} IDs, {chunk_messages} msgs, {dc_str}) took {chunk_duration:.1f}s"
            )
            logger.info(
                f"🔄 Worker {worker_idx} DISCARDING buffer and re-fetching as 4 sub-chunks (adaptive split)..."
            )

            # Record this slow chunk for statistics
            stats["slow_chunks"].append(
                {
                    "start_id": start_id,
                    "end_id": end_id,
                    "duration_sec": chunk_duration,
                    "messages": chunk_messages,
                    "action": "split_attempted",
                    "dc_id": self.current_entity_dc,
                }
            )

            # IMPORTANT: Don't stream the buffer! Discard it and re-fetch as sub-chunks
            message_buffer = []

            # Split into 4 smaller sub-chunks
            sub_chunk_size = chunk_span // 4
            sub_chunks = []
            for i in range(4):
                sub_start = start_id + (i * sub_chunk_size)
                sub_end = start_id + ((i + 1) * sub_chunk_size) if i < 3 else end_id
                sub_chunks.append((sub_start, sub_end))

            # Recursively fetch sub-chunks with reduced retry count
            for sub_start, sub_end in sub_chunks:
                logger.debug(
                    f"  🔹 Worker {worker_idx} fetching sub-chunk {sub_start}-{sub_end}"
                )
                await self._fetch_chunk_streaming(
                    worker_idx,
                    client,
                    input_peer,
                    sub_start,
                    sub_end,
                    message_queue,
                    stats,
                    takeout_id,
                    slow_chunk_threshold=slow_chunk_threshold,
                    max_retries=max_retries - 1,
                )

            # Early return - sub-chunks handle their own stats
            return

        # Case 2 & 3: Stream messages to queue (either moderately slow or fast)
        if message_buffer:
            stream_start = time.time()

            # 🚀 Stream messages directly to queue - NO SERIALIZATION!
            for msg in message_buffer:
                # Attach worker client to message for media downloads
                msg._client = client
                await message_queue.put(msg)

            # Record streaming time and message count
            stream_time_ms = (time.time() - stream_start) * 1000
            stats["stream_time_ms"] = stats.get("stream_time_ms", 0) + int(stream_time_ms)
            stats["messages"] += chunk_messages

        # Log performance
        if chunk_duration > 2.0:
            dc_str = (
                f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
            )
            logger.warning(
                f"⚠️ Worker {worker_idx} slow chunk: {start_id}-{end_id} "
                f"(span: {chunk_span:,} IDs, {chunk_messages} msgs, {dc_str}) took {chunk_duration:.1f}s"
            )

            slow_chunk_info = {
                "start_id": start_id,
                "end_id": end_id,
                "duration_sec": chunk_duration,
                "messages": chunk_messages,
                "action": "logged",
                "dc_id": self.current_entity_dc,
            }
            stats["slow_chunks"].append(slow_chunk_info)

            # Record to persistent database if significantly slow
            if chunk_duration > self.config.slow_chunk_threshold:
                try:
                    from src.hot_zones_manager import HotZonesManager, SlowChunkRecord

                    hot_zones_mgr = HotZonesManager(self.config)

                    density = (
                        (chunk_messages / chunk_span * 1000) if chunk_span > 0 else 0
                    )

                    slow_record = SlowChunkRecord(
                        id_range=(start_id, end_id),
                        duration_sec=chunk_duration,
                        message_count=chunk_messages,
                        density=density,
                        datacenter=dc_str,
                        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        worker_id=worker_idx,
                    )

                    hot_zones_mgr.record_slow_chunk(slow_record)
                    hot_zones_mgr.save_database()
                    logger.debug(
                        f"💾 Recorded slow chunk to database: {start_id}-{end_id}"
                    )
                except Exception as e:
                    logger.debug(f"⚠️ Failed to record slow chunk to database: {e}")
        else:
            logger.debug(
                f"✅ Worker {worker_idx} chunk {start_id}-{end_id}: "
                f"{chunk_messages} msgs in {chunk_duration:.1f}s"
            )

    async def fetch_messages(
        self,
        entity: Any,
        limit: Optional[int] = None,
        min_id: Optional[int] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> AsyncGenerator["types.Message", None]:  # type: ignore
        """
        Overrides fetch_messages to use sharding if applicable.
        Falls back to super().fetch_messages() if sharding is disabled or not possible.
        """

        # 🔍 CRITICAL DEBUG: Log entry point FIRST
        logger.info(
            f"🔍 ShardedTelegramManager.fetch_messages() CALLED - entity={entity}, limit={limit}, page={page}, min_id={min_id}"
        )

        logger.debug(
            f"fetch_messages called: limit={limit}, page={page}, min_id={min_id}"
        )
        logger.debug(
            f"Sharding config: enable_shard_fetch={self.config.enable_shard_fetch}, worker_count={self.worker_count}"
        )

        # Fallback conditions:
        # 1. Pagination is requested (sharding is for full export)
        # 2. Limit is small (overhead of sharding not worth it)
        # 3. Entity has content restrictions (noforwards=True) that block Takeout access
        # ✅ FIXED: Re-enabled sharding with in-memory streaming (no serialization issues)
        use_sharding = self.config.enable_shard_fetch

        if page is not None:
            logger.info("Sharding disabled: pagination requested")
            use_sharding = False

        if limit and limit < 1000:
            logger.info(f"Sharding disabled: limit too small ({limit} < 1000)")
            use_sharding = False
        
        # 🔧 NEW: Check for restricted channels (noforwards=True)
        # These channels block access when using Takeout, but work fine with standard client
        try:
            resolved_entity_check = await self.client.get_entity(entity)
            if getattr(resolved_entity_check, "noforwards", False):
                logger.warning(f"⚠️ Channel has content restrictions (noforwards=True)")
                logger.info("🔄 Sharding disabled: using standard fetch for restricted channel")
                use_sharding = False
        except Exception as e:
            logger.debug(f"Could not check entity restrictions: {e}")

        if not use_sharding:
            logger.info("Using standard (non-sharded) fetch_messages")
            async for msg in super().fetch_messages(
                entity, limit, min_id, page, page_size
            ):
                yield msg
            return

        # --- 🚀 Streaming Sharding Implementation (In-Memory Queues) ---
        logger.info("⚡ Sharding activated! Starting parallel fetch with in-memory streaming...")

        shard_start_time = time.time()

        try:
            # 0. Setup Takeout FIRST (before fetching bounds)
            if not self.takeout_id:
                step_start = time.time()
                self.worker_stats = {}  # Reset stats for this run
                self.takeout_id = await self._setup_takeout()
                logger.info(
                    f"⏱️  Takeout setup in {(time.time() - step_start) * 1000:.0f}ms"
                )
            
            # 1. Resolve Entity & Bounds
            step_start = time.time()
            resolved_entity = await self.client.get_entity(entity)
            logger.info(
                f"⏱️  Entity resolved in {(time.time() - step_start) * 1000:.0f}ms"
            )

            # Extract DC ID for diagnostics
            dc_id = self._extract_dc_id(resolved_entity)
            self.current_entity_dc = dc_id  # Store for worker access
            if dc_id > 0:
                logger.info(f"📍 Entity DC: DC{dc_id}")
            else:
                logger.debug("📍 Entity DC: Unknown (will log per-chunk if available)")

            input_peer = utils.get_input_peer(resolved_entity)  # type: ignore

            # Get bounds (max_id)
            step_start = time.time()
            
            # 🔧 For restricted channels, use Takeout API directly
            if self.takeout_id:
                logger.debug(f"🔐 Using Takeout API (ID: {self.takeout_id}) to fetch max_id")
                try:
                    # Wrap GetHistoryRequest in Takeout context
                    request = InvokeWithTakeoutRequest(
                        takeout_id=self.takeout_id,
                        query=GetHistoryRequest(
                            peer=input_peer,
                            offset_id=0,
                            offset_date=None,
                            add_offset=0,
                            limit=1,
                            max_id=0,
                            min_id=0,
                            hash=0
                        )
                    )
                    result = await self.client(request)
                    
                    # Extract first message
                    if result.messages:
                        messages = result.messages
                        logger.debug(f"✅ Takeout API returned {len(messages)} message(s)")
                    else:
                        messages = []
                        logger.warning("⚠️ Takeout API returned empty messages list")
                except Exception as e:
                    logger.error(f"❌ Takeout API call failed: {e}")
                    messages = []
            else:
                # No takeout - use standard method
                messages = await self.client.get_messages(resolved_entity, limit=1)
            
            if not messages:
                # 🔧 FALLBACK for restricted channels (noforwards=True):
                # If sharding can't get bounds, fallback to standard (non-sharded) fetch
                logger.warning("⚠️ All methods failed to get max_id - falling back to standard (non-sharded) fetch")
                logger.info("🔄 Switching to standard fetch_messages for restricted channel")
                
                # Use parent's fetch_messages (non-sharded)
                async for msg in super().fetch_messages(
                    entity, limit, min_id, page, page_size
                ):
                    yield msg
                return
                    
            max_id = messages[0].id
            logger.info(
                f"⏱️  Max ID ({max_id}) fetched in {(time.time() - step_start) * 1000:.0f}ms"
            )

            # Get ACTUAL minimum message ID (not just 1)
            # Fetch oldest message to determine real range
            if min_id is None:
                step_start = time.time()
                logger.info("🔍 Fetching oldest message (reverse=True)...")
                
                # 🔧 For restricted channels, use Takeout API directly
                if self.takeout_id:
                    logger.debug(f"🔐 Using Takeout API (ID: {self.takeout_id}) to fetch min_id")
                    try:
                        # Wrap GetHistoryRequest in Takeout context with reverse order
                        request = InvokeWithTakeoutRequest(
                            takeout_id=self.takeout_id,
                            query=GetHistoryRequest(
                                peer=input_peer,
                                offset_id=0,
                                offset_date=None,
                                add_offset=-1,  # Reverse: get oldest
                                limit=1,
                                max_id=0,
                                min_id=0,
                                hash=0
                            )
                        )
                        result = await self.client(request)
                        
                        if result.messages:
                            oldest_messages = result.messages
                            logger.debug(f"✅ Takeout API (reverse) returned {len(oldest_messages)} message(s)")
                        else:
                            oldest_messages = []
                            logger.warning("⚠️ Takeout API (reverse) returned empty")
                    except Exception as e:
                        logger.error(f"❌ Takeout API (reverse) call failed: {e}")
                        oldest_messages = []
                else:
                    oldest_messages = await self.client.get_messages(
                        resolved_entity,
                        limit=1,
                        reverse=True,  # Get oldest message
                        offset_id=0,
                    )
                
                # 🔧 FALLBACK for restricted channels
                if not oldest_messages:
                    logger.warning("⚠️ get_messages(reverse=True) returned empty - trying iter_messages fallback")
                    try:
                        async for msg in self.client.iter_messages(resolved_entity, limit=1, reverse=True):
                            oldest_messages = [msg]
                            break
                    except Exception as e:
                        logger.error(f"❌ iter_messages(reverse=True) fallback failed: {e}")
                
                fetch_time_ms = (time.time() - step_start) * 1000
                if oldest_messages:
                    effective_min = oldest_messages[0].id
                    logger.info(
                        f"⏱️  Oldest message ID ({effective_min}) fetched in {fetch_time_ms:.0f}ms"
                    )
                else:
                    effective_min = 1  # Fallback if no messages
                    logger.warning(
                        f"⚠️  No oldest message found, using fallback min_id=1 (took {fetch_time_ms:.0f}ms)"
                    )
            else:
                effective_min = min_id
                logger.info(f"📌 Using provided min_id: {effective_min}")

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

            actual_span = max_id - effective_min
            logger.info(
                f"⚡ Starting Sharded Export for {utils.get_display_name(resolved_entity)}"
            )
            logger.info(
                f"📊 Message ID Range: {effective_min} -> {max_id} (span: {actual_span:,} IDs)"
            )

            # 2. Setup Workers (Takeout already initialized earlier)
            step_start = time.time()
            self.worker_sessions = await self._prepare_workers()
            logger.info(
                f"⏱️  Worker sessions prepared in {(time.time() - step_start) * 1000:.0f}ms"
            )

            # Initialize worker clients
            step_start = time.time()
            self.worker_clients.clear()
            for i, sess_name in enumerate(self.worker_sessions):
                client_start = time.time()
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
                logger.debug(
                    f"⏱️  Worker {i} connected in {(time.time() - client_start) * 1000:.0f}ms"
                )
            logger.info(
                f"⏱️  All {len(self.worker_clients)} worker clients connected in {(time.time() - step_start) * 1000:.0f}ms"
            )

            # Optionally pre-warm workers to the entity DC for faster per-DC routing
            if (
                getattr(self.config, "dc_aware_routing_enabled", False)
                and self.current_entity_dc > 0
                and getattr(self.config, "dc_prewarm_enabled", True)
            ):
                try:
                    from src.telegram_dc_utils import prewarm_workers  # lazy import

                    prewarm_start = time.time()
                    prewarm_results = await prewarm_workers(
                        self.worker_clients,
                        resolved_entity,
                        timeout=self.config.dc_prewarm_timeout,
                        dc_id=self.current_entity_dc,
                    )
                    prewarm_time = time.time() - prewarm_start
                    prewarmed_count = sum(1 for ok in prewarm_results.values() if ok)
                    logger.info(
                        f"♻️ DC pre-warm: {prewarmed_count}/{len(self.worker_clients)} workers warmed to DC{self.current_entity_dc} in {prewarm_time:.2f}s"
                    )
                except Exception as e:
                    logger.warning(f"DC pre-warm failed or not available: {e}")

            # 3. Calculate Chunks for Dynamic Work Stealing with Hot Zones & Density Awareness
            # NEW: Use HotZonesManager for adaptive chunking
            from src.hot_zones_manager import HotZonesManager

            step_start = time.time()
            hot_zones_mgr = HotZonesManager(self.config)
            logger.info(
                f"⏱️  Hot zones manager initialized in {(time.time() - step_start) * 1000:.0f}ms"
            )

            # NEW: Estimate message density for adaptive chunking
            estimated_density = 50.0  # Default
            if self.config.enable_density_estimation:
                step_start = time.time()
                logger.info("🔍 Estimating message density for adaptive chunking...")
                estimated_density = await hot_zones_mgr.estimate_density(
                    self.client, resolved_entity, effective_min, max_id
                )
                logger.info(
                    f"📊 Estimated density: {estimated_density:.1f} msgs/1K IDs "
                    f"(took {(time.time() - step_start) * 1000:.0f}ms)"
                )

            total_span = max_id - effective_min
            datacenter = f"DC{dc_id}" if dc_id > 0 else "Unknown"

            # Create task queue and populate with adaptive-sized chunks
            task_queue: asyncio.Queue[Any] = asyncio.Queue()
            current_id = effective_min
            chunks_created = 0

            logger.info(
                f"📊 Creating adaptive chunks from ID range {effective_min}-{max_id} "
                f"(span: {total_span:,} IDs, density: {estimated_density:.1f} msgs/1K)"
            )

            # NEW: Variable-sized chunks based on hot zones and density
            while current_id < max_id:
                # Query optimal chunk size for current position
                optimal_chunk_size = hot_zones_mgr.get_optimal_chunk_size(
                    current_id, max_id, datacenter
                )

                # Apply density-based override if no hot zone matched
                if optimal_chunk_size == self.config.shard_chunk_size:
                    # Use density-based chunk size
                    optimal_chunk_size = hot_zones_mgr.get_chunk_size_for_density(
                        estimated_density
                    )
                    if estimated_density > 100:
                        logger.debug(
                            f"🎯 High density ({estimated_density:.1f}), using chunk size: {optimal_chunk_size}"
                        )
                else:
                    logger.debug(
                        f"🔥 Hot zone detected at {current_id}, using chunk size: {optimal_chunk_size}"
                    )

                remaining_span = max_id - current_id
                this_chunk_size = min(optimal_chunk_size, remaining_span)
                chunk_end = min(current_id + this_chunk_size, max_id)

                if current_id < chunk_end:
                    await task_queue.put((current_id, chunk_end))
                    chunks_created += 1
                    logger.debug(
                        f"Chunk {chunks_created}: {current_id}-{chunk_end} "
                        f"({chunk_end - current_id} IDs, adaptive size: {optimal_chunk_size})"
                    )

                current_id = chunk_end

            logger.info(
                f"✅ Created {chunks_created} adaptive chunks (sizes: 5K-50K based on hot zones + density)"
            )

            # 4. 🚀 Start Workers with In-Memory Message Queues (Streaming Mode)
            step_start = time.time()

            message_queues: List[MessageQueue] = []
            tasks = []

            for i in range(self.worker_count):
                # Create message queue for this worker
                msg_queue = MessageQueue(worker_id=i, max_size=10000)
                message_queues.append(msg_queue)

                # 🚀 NEW: Pass message_queue instead of file path
                task = asyncio.create_task(
                    self._worker_task_streaming(
                        i,
                        self.worker_clients[i],
                        input_peer,
                        [],  # No fixed ranges - using dynamic queue
                        msg_queue,  # Stream to queue instead of file
                        self.takeout_id,
                        task_queue=task_queue,  # Enable dynamic work stealing
                    )
                )
                tasks.append(task)

            logger.info(
                f"⏱️  {len(tasks)} worker tasks created in {(time.time() - step_start) * 1000:.0f}ms"
            )
            logger.info(
                f"⚡ Total sharding initialization time: {(time.time() - shard_start_time) * 1000:.0f}ms"
            )
            logger.info("🚀 Starting message streaming from worker queues...")

            # 5. 🚀 Collect & Sort Messages from All Workers (Fix message order)
            count = 0
            merge_start_time = time.time()
            all_messages = []  # Collect all messages first
            
            logger.info("📥 Collecting messages from all worker queues...")

            # Collect messages from ALL workers in parallel
            for i, (task, msg_queue) in enumerate(zip(tasks, message_queues)):
                logger.debug(f"📥 Collecting from worker {i} queue...")

                # Stream messages from this worker's queue
                while True:
                    # Check if worker task finished with error
                    if task.done():
                        exc = task.exception()
                        if exc:
                            logger.error(f"❌ Worker {i} failed with error: {exc}")
                            raise exc

                    # Get message from queue (will return None when finished)
                    msg = await msg_queue.get()
                    
                    if msg is None:
                        # Queue is finished and empty
                        if msg_queue.is_finished():
                            logger.debug(f"✅ Worker {i} queue finished: {msg_queue.message_count} messages")
                            break
                        else:
                            # Temporary empty, wait a bit
                            await asyncio.sleep(0.05)
                            continue

                    # Collect message (don't yield yet!)
                    all_messages.append(msg)
                    count += 1

                    if limit and count >= limit:
                        break

                if limit and count >= limit:
                    break

            # 🔍 Sort all messages by ID to restore chronological order
            logger.info(f"📊 Collected {len(all_messages):,} messages, sorting by ID...")
            sort_start = time.time()
            all_messages.sort(key=lambda m: m.id)  # Ascending order (oldest first)
            sort_duration = time.time() - sort_start
            
            logger.info(
                f"✅ Messages sorted in {sort_duration:.2f}s "
                f"(ID range: {all_messages[0].id} → {all_messages[-1].id})"
            )

            # 🚀 Yield messages in correct chronological order
            yield_count = 0
            for msg in all_messages:
                yield msg
                yield_count += 1


            # Log overall streaming telemetry
            stream_duration = time.time() - merge_start_time
            logger.info(
                f"📊 Streaming telemetry: {yield_count:,} messages yielded in {stream_duration:.2f}s "
                f"({yield_count/stream_duration:.0f} msgs/sec, including {sort_duration:.2f}s sort)"
            )


            # 🔍 Log slow chunks statistics
            await self._log_slow_chunks_statistics()

            # Cancel remaining tasks if we exited early
            for task in tasks:
                if not task.done():
                    task.cancel()

        except Exception as e:
            logger.error(f"💥 Sharded export failed: {e}")
            raise e
        finally:
            await self._cleanup_sharding()
            # Note: No temp files to cleanup with streaming approach!
