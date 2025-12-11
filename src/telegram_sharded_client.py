import asyncio
import os
import pickle
import shutil
import struct
import time
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

    def __init__(self, config: Config, connection_manager: Any = None):
        super().__init__(config, connection_manager)
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
            if hasattr(entity_or_peer, 'photo') and entity_or_peer.photo:
                if hasattr(entity_or_peer.photo, 'dc_id'):
                    return int(entity_or_peer.photo.dc_id)
            
            # Try to get from access_hash (channel/user)
            if hasattr(entity_or_peer, 'access_hash'):
                # DC ID is typically encoded in access_hash for channels
                # This is a heuristic, not guaranteed
                pass
            
            # For InputPeer types
            if isinstance(entity_or_peer, (types.InputPeerChannel, types.InputPeerUser)):  # type: ignore
                # Unfortunately, InputPeer doesn't carry DC info directly
                # We'd need to store it from the original entity
                pass
                
            return 0  # Unknown DC
        except Exception as e:
            logger.debug(f"Could not extract DC ID: {e}")
            return 0
    
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
        split_attempts = sum(1 for c in all_slow_chunks if c.get("action") == "split_attempted")
        avg_slow_duration = sum(c["duration_sec"] for c in all_slow_chunks) / total_slow_chunks
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
        top_slow_chunks = sorted(all_slow_chunks, key=lambda c: c["duration_sec"], reverse=True)[:5]
        
        logger.warning(
            f"🐢 Slow Chunks Summary: {total_slow_chunks} chunks >2s detected, "
            f"{split_attempts} split attempts, avg {avg_slow_duration:.1f}s"
        )
        
        max_dc_str = f"DC{max_slow_chunk.get('dc_id', 0)}" if max_slow_chunk.get('dc_id', 0) > 0 else "DC?"
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
                chunk_dc_str = f"DC{chunk.get('dc_id', 0)}" if chunk.get('dc_id', 0) > 0 else "DC?"
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
                logger.info(f"💾 Updated hot zones database: {hot_zones_mgr.slow_chunk_db_path}")
                
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
                    logger.debug(f"👷 Worker {worker_idx} starting in DYNAMIC mode (work stealing)")
                    
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
                                worker_idx, client, input_peer, start_id, end_id, 
                                f, stats, takeout_id,
                                slow_chunk_threshold=self.config.slow_chunk_threshold,
                                max_retries=self.config.slow_chunk_max_retries
                            )
                            
                            task_queue.task_done()
                            
                        except asyncio.QueueEmpty:
                            # No more tasks, worker finishes
                            logger.debug(f"✅ Worker {worker_idx} finished (queue empty, processed {stats['chunks_processed']} chunks)")
                            break
                
                # OLD: Static range assignment mode (fallback)
                else:
                    logger.debug(f"👷 Worker {worker_idx} starting in STATIC mode (fixed ranges)")
                    
                    for start_id, end_id in id_ranges:
                        logger.debug(
                            f"👷 Worker {worker_idx} processing range {start_id}-{end_id}"
                        )
                        
                        await self._fetch_chunk(
                            worker_idx, client, input_peer, start_id, end_id, 
                            f, stats, takeout_id,
                            slow_chunk_threshold=self.config.slow_chunk_threshold,
                            max_retries=self.config.slow_chunk_max_retries
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

            wrapped_req = InvokeWithTakeoutRequest(
                takeout_id=takeout_id, query=req
            )

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
        if chunk_duration > slow_chunk_threshold and chunk_span > 1000 and max_retries > 0:
            dc_str = f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
            logger.warning(
                f"🐢 Worker {worker_idx} VERY SLOW chunk: {start_id}-{end_id} "
                f"(span: {chunk_span:,} IDs, {chunk_messages} msgs, {dc_str}) took {chunk_duration:.1f}s"
            )
            logger.info(
                f"🔄 Worker {worker_idx} DISCARDING buffer and re-fetching as 4 sub-chunks (adaptive split)..."
            )
            
            # Record this slow chunk for statistics
            stats["slow_chunks"].append({
                "start_id": start_id,
                "end_id": end_id,
                "duration_sec": chunk_duration,
                "messages": chunk_messages,
                "action": "split_attempted",
                "dc_id": self.current_entity_dc,  # Add DC ID
            })
            
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
                    worker_idx, client, input_peer, sub_start, sub_end,
                    f, stats, takeout_id,
                    slow_chunk_threshold=slow_chunk_threshold,
                    max_retries=max_retries - 1  # Reduce retry count to prevent infinite recursion
                )
            
            # Early return - sub-chunks handle their own stats
            return
        
        # Case 2 & 3: Write messages to file (either moderately slow or fast)
        if message_buffer:
            io_start = time.time()
            
            # Use length-prefixed framing for safe reading
            data = pickle.dumps(message_buffer)
            f.write(struct.pack(">I", len(data)))
            f.write(data)
            f.flush()  # Ensure data is written to disk
            
            # Record IO time and message count
            io_time_ms = (time.time() - io_start) * 1000
            stats["io_time_ms"] += int(io_time_ms)
            stats["messages"] += chunk_messages  # Update stats ONLY when actually written
        
        # Log performance
        if chunk_duration > 2.0:
            dc_str = f"DC{self.current_entity_dc}" if self.current_entity_dc > 0 else "DC?"
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
                    
                    density = (chunk_messages / chunk_span * 1000) if chunk_span > 0 else 0
                    
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
                    logger.debug(f"💾 Recorded slow chunk to database: {start_id}-{end_id}")
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
        logger.info(f"🔍 ShardedTelegramManager.fetch_messages() CALLED - entity={entity}, limit={limit}, page={page}, min_id={min_id}")
        
        logger.debug(
            f"fetch_messages called: limit={limit}, page={page}, min_id={min_id}"
        )
        logger.debug(
            f"Sharding config: enable_shard_fetch={self.config.enable_shard_fetch}, worker_count={self.worker_count}"
        )

        # Fallback conditions:
        # 1. Pagination is requested (sharding is for full export)
        # 2. Limit is small (overhead of sharding not worth it)
        use_sharding = self.config.enable_shard_fetch

        if page is not None:
            logger.info("Sharding disabled: pagination requested")
            use_sharding = False

        if limit and limit < 1000:
            logger.info(f"Sharding disabled: limit too small ({limit} < 1000)")
            use_sharding = False

        if not use_sharding:
            logger.info("Using standard (non-sharded) fetch_messages")
            async for msg in super().fetch_messages(
                entity, limit, min_id, page, page_size
            ):
                yield msg
            return

        # --- Sharding Implementation ---
        logger.info("⚡ Sharding activated! Starting parallel fetch...")
        
        shard_start_time = time.time()

        try:
            # 1. Resolve Entity & Bounds
            step_start = time.time()
            resolved_entity = await self.client.get_entity(entity)
            logger.info(f"⏱️  Entity resolved in {(time.time() - step_start)*1000:.0f}ms")
            
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
            messages = await self.client.get_messages(resolved_entity, limit=1)
            if not messages:
                logger.info("⏱️  Empty chat, returning")
                return
            max_id = messages[0].id
            logger.info(f"⏱️  Max ID ({max_id}) fetched in {(time.time() - step_start)*1000:.0f}ms")

            # Get ACTUAL minimum message ID (not just 1)
            # Fetch oldest message to determine real range
            if min_id is None:
                step_start = time.time()
                logger.info("🔍 Fetching oldest message (reverse=True)...")
                oldest_messages = await self.client.get_messages(
                    resolved_entity,
                    limit=1,
                    reverse=True,  # Get oldest message
                    offset_id=0,
                )
                fetch_time_ms = (time.time() - step_start) * 1000
                if oldest_messages:
                    effective_min = oldest_messages[0].id
                    logger.info(f"⏱️  Oldest message ID ({effective_min}) fetched in {fetch_time_ms:.0f}ms")
                else:
                    effective_min = 1  # Fallback if no messages
                    logger.warning(f"⚠️  No oldest message found, using fallback min_id=1 (took {fetch_time_ms:.0f}ms)")
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

            # 2. Setup Takeout & Workers
            step_start = time.time()
            self.worker_stats = {}  # Reset stats for this run
            self.takeout_id = await self._setup_takeout()
            logger.info(f"⏱️  Takeout setup in {(time.time() - step_start)*1000:.0f}ms")
            
            step_start = time.time()
            self.worker_sessions = await self._prepare_workers()
            logger.info(f"⏱️  Worker sessions prepared in {(time.time() - step_start)*1000:.0f}ms")

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
                logger.debug(f"⏱️  Worker {i} connected in {(time.time() - client_start)*1000:.0f}ms")
            logger.info(f"⏱️  All {len(self.worker_clients)} worker clients connected in {(time.time() - step_start)*1000:.0f}ms")

            # 3. Calculate Chunks for Dynamic Work Stealing with Hot Zones & Density Awareness
            # NEW: Use HotZonesManager for adaptive chunking
            from src.hot_zones_manager import HotZonesManager
            
            step_start = time.time()
            hot_zones_mgr = HotZonesManager(self.config)
            logger.info(f"⏱️  Hot zones manager initialized in {(time.time() - step_start)*1000:.0f}ms")
            
            # NEW: Estimate message density for adaptive chunking
            estimated_density = 50.0  # Default
            if self.config.enable_density_estimation:
                step_start = time.time()
                logger.info("🔍 Estimating message density for adaptive chunking...")
                estimated_density = await hot_zones_mgr.estimate_density(
                    self.client,
                    resolved_entity,
                    effective_min,
                    max_id
                )
                logger.info(
                    f"📊 Estimated density: {estimated_density:.1f} msgs/1K IDs "
                    f"(took {(time.time() - step_start)*1000:.0f}ms)"
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
                    current_id,
                    max_id,
                    datacenter
                )
                
                # Apply density-based override if no hot zone matched
                if optimal_chunk_size == self.config.shard_chunk_size:
                    # Use density-based chunk size
                    optimal_chunk_size = hot_zones_mgr.get_chunk_size_for_density(estimated_density)
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

            # 4. Start Workers with Temporary Files (Dynamic Mode)
            step_start = time.time()
            temp_dir = Path(self.config.export_path) / "temp_shards"
            temp_dir.mkdir(parents=True, exist_ok=True)

            worker_files = []
            tasks = []

            for i in range(self.worker_count):
                p = temp_dir / f"shard_{i}.bin"
                worker_files.append(p)
                
                # NEW: Pass task_queue instead of fixed ranges
                task = asyncio.create_task(
                    self._worker_task(
                        i,
                        self.worker_clients[i],
                        input_peer,
                        [],  # No fixed ranges - using dynamic queue
                        p,
                        self.takeout_id,
                        task_queue=task_queue,  # Enable dynamic work stealing
                    )
                )
                tasks.append(task)
            
            logger.info(f"⏱️  {len(tasks)} worker tasks created in {(time.time() - step_start)*1000:.0f}ms")
            logger.info(f"⚡ Total sharding initialization time: {(time.time() - shard_start_time)*1000:.0f}ms")
            logger.info("🚀 Starting message merge from worker shards...")

            # 5. Yield from Files in Order with Profiling
            count = 0
            merge_start_time = time.time()
            total_read_time_ms: float = 0
            total_deserialize_time_ms: float = 0
            total_bytes_read = 0
            last_log_time = time.time()
            last_count = 0

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

                # Track per-worker IO stats
                worker_read_start = time.time()
                worker_bytes = 0

                with open(f_path, "rb") as f:
                    while True:
                        # Read length prefix
                        io_start = time.time()
                        header = f.read(4)
                        total_read_time_ms += (time.time() - io_start) * 1000
                        worker_bytes += len(header)
                        while len(header) < 4:
                            if task.done():
                                exc = task.exception()
                                if exc:
                                    raise exc

                                # Try to read remaining bytes once more
                                header += f.read(4 - len(header))
                                break

                            await asyncio.sleep(0.1)
                            io_start = time.time()
                            header += f.read(4 - len(header))
                            total_read_time_ms += (time.time() - io_start) * 1000

                        if len(header) < 4:
                            break  # EOF

                        length = struct.unpack(">I", header)[0]

                        # Read body
                        body = b""
                        while len(body) < length:
                            io_start = time.time()
                            chunk = f.read(length - len(body))
                            total_read_time_ms += (time.time() - io_start) * 1000
                            worker_bytes += len(chunk)
                            if not chunk:
                                if task.done():
                                    exc = task.exception()
                                    if exc:
                                        raise exc

                                    io_start = time.time()
                                    chunk = f.read(length - len(body))
                                    total_read_time_ms += (
                                        time.time() - io_start
                                    ) * 1000
                                    worker_bytes += len(chunk)
                                    if not chunk:
                                        break  # Truncated
                                else:
                                    await asyncio.sleep(0.1)
                                    continue
                            body += chunk

                        if len(body) < length:
                            break  # Truncated

                        # Measure deserialization time
                        deserialize_start = time.time()
                        batch = pickle.loads(body)
                        total_deserialize_time_ms += (
                            time.time() - deserialize_start
                        ) * 1000

                        for msg in sorted(batch, key=lambda m: m.id):  # Sort by ID to ensure chronological order (oldest first)
                            # Re-attach worker client to the message for parallel media download
                            msg._client = self.worker_clients[i]
                            yield msg
                            count += 1
                            
                            # 🔍 NEW: Log merge progress every second to detect stalls
                            now = time.time()
                            if now - last_log_time >= 1.0:
                                msgs_since_last = count - last_count
                                logger.debug(
                                    f"📊 Merge progress: {count:,} messages (+{msgs_since_last} msgs/s), "
                                    f"worker {i}, elapsed {now - merge_start_time:.1f}s"
                                )
                                last_log_time = now
                                last_count = count
                            
                            if limit and count >= limit:
                                break

                        if limit and count >= limit:
                            break

                # Log per-worker merge stats
                worker_read_time = (time.time() - worker_read_start) * 1000
                total_bytes_read += worker_bytes
                logger.debug(
                    f"Worker {i} merge: {worker_bytes:,} bytes read in {worker_read_time:.1f}ms"
                )

                if limit and count >= limit:
                    break

            # Log overall merge/IO telemetry
            merge_duration = time.time() - merge_start_time
            logger.info(
                f"📊 Merge telemetry: {total_bytes_read:,} bytes, "
                f"read time {total_read_time_ms:.0f}ms, "
                f"deserialize time {total_deserialize_time_ms:.0f}ms, "
                f"total time {merge_duration:.2f}s"
            )
            
            # 🔍 NEW: Log slow chunks statistics
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
            # Cleanup temp files
            if "temp_dir" in locals() and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to remove temp dir {temp_dir}: {e}")
