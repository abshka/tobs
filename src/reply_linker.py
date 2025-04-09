import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Dict

import aiofiles

from src.config import Config
from src.cache_manager import CacheManager
from src.utils import logger, get_relative_path

class ReplyLinker:
    def __init__(self, config: Config, cache_manager: CacheManager):
        self.config = config
        self.cache_manager = cache_manager
        # Use default executor for potentially blocking file I/O and path operations
        self.executor = ThreadPoolExecutor(max_workers=config.max_workers)
        self.io_semaphore = asyncio.Semaphore(20) # Limit concurrent file I/O
        self.file_locks: Dict[Path, asyncio.Lock] = {}

    async def _get_file_lock(self, path: Path) -> asyncio.Lock:
        """Gets or creates an asyncio Lock for a specific file path."""
        if path not in self.file_locks:
            self.file_locks[path] = asyncio.Lock()
        return self.file_locks[path]

    async def link_replies(self, entity_id: str, entity_export_path: Path):
        """
        Iterates through cached messages for a specific entity and adds reply links
        to the corresponding note files within the entity's export path.
        """
        logger.info(f"[{entity_id}] Starting reply linking process...")

        # Get processed messages only for the current entity
        processed_messages = await self.cache_manager.get_all_processed_messages_async(entity_id)

        if not processed_messages:
            logger.info(f"[{entity_id}] No processed messages found in cache. Skipping reply linking.")
            return

        # Prepare operations (find parent/child pairs)
        link_tasks = []
        unresolved_tasks = [] # Tasks to mark notes with unresolved parents

        for child_id_str, msg_data in processed_messages.items():
            parent_id = msg_data.get("reply_to")
            child_filename = msg_data.get("filename")

            if not child_filename:
                logger.warning(f"[{entity_id}] Child message {child_id_str} missing filename in cache. Cannot process replies.")
                continue

            # Resolve child note path asynchronously
            child_note_path_coro = self._resolve_note_path(entity_export_path, child_filename)
            child_note_path_task = asyncio.create_task(child_note_path_coro)

            if parent_id:
                parent_id_str = str(parent_id)
                # Check if the parent message was also processed *for this entity*
                parent_msg_data = processed_messages.get(parent_id_str)

                if parent_msg_data:
                    parent_filename = parent_msg_data.get("filename")
                    if parent_filename:
                        # Both parent and child processed for this entity
                        parent_note_path_coro = self._resolve_note_path(entity_export_path, parent_filename)
                        parent_note_path_task = asyncio.create_task(parent_note_path_coro)
                        link_tasks.append(
                            self._schedule_link_addition(parent_note_path_task, child_note_path_task, entity_id)
                        )
                    else:
                        # Parent processed but filename missing (should not happen)
                        logger.warning(f"[{entity_id}] Parent message {parent_id_str} missing filename. Marking reply in {child_filename} as unresolved.")
                        unresolved_tasks.append(
                            self._schedule_unresolved_mark(child_note_path_task, entity_id)
                        )
                else:
                    # Parent message wasn't processed (deleted, outside scope, different entity)
                    # logger.debug(f"[{entity_id}] Parent message {parent_id} for reply {child_id_str} not found in this entity's cache. Marking as unresolved.")
                    unresolved_tasks.append(
                         self._schedule_unresolved_mark(child_note_path_task, entity_id)
                     )
            # else: No parent ID, nothing to link

        # Execute all linking and marking tasks concurrently
        logger.info(f"[{entity_id}] Processing {len(link_tasks)} potential reply links and {len(unresolved_tasks)} unresolved links...")
        all_tasks = link_tasks + unresolved_tasks
        if all_tasks:
            results = await asyncio.gather(*all_tasks, return_exceptions=True)
            # Log errors from gather
            success_count = 0
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    task_type = "link" if i < len(link_tasks) else "unresolved"
                    logger.error(f"[{entity_id}] Error processing {task_type} task: {result}")
                elif result: # Count successful operations (True returned)
                    success_count +=1
            logger.info(f"[{entity_id}] Finished processing reply links. {success_count}/{len(all_tasks)} operations successful.")
        else:
            logger.info(f"[{entity_id}] No reply links or unresolved messages to process.")

    async def _resolve_note_path(self, entity_export_path: Path, filename: str) -> Optional[Path]:
        """Resolves the full path to a note file within the entity's export directory."""
        loop = asyncio.get_running_loop()

        # First check if file is directly in entity_export_path
        note_path = entity_export_path / filename
        exists = await loop.run_in_executor(self.executor, note_path.exists)
        if exists:
            return note_path

        # Then check in the year subdirectory (YYYY)
        try:
            year = filename.split('-')[0]
            if year.isdigit() and len(year) == 4:
                year_path = entity_export_path / year / filename
                exists_in_year = await loop.run_in_executor(self.executor, year_path.exists)
                if exists_in_year:
                    return year_path
        except:
            pass  # Ignore parsing errors

        logger.warning(f"Note file '{filename}' not found in '{entity_export_path}' (or subdirs).")
        return None

    async def _schedule_link_addition(self, parent_path_task: asyncio.Task, child_path_task: asyncio.Task, entity_id: str) -> bool:
        """Waits for path resolutions and schedules the link addition."""
        parent_note_path = await parent_path_task
        child_note_path = await child_path_task

        if parent_note_path and child_note_path:
            return await self._add_reply_link(parent_note_path, child_note_path, entity_id)
        else:
            # Log which path failed if possible (requires modifying resolve_note_path to return identifier)
            # logger.warning(f"[{entity_id}] Skipping link addition due to missing parent or child note file.")
            return False # Indicate failure

    async def _schedule_unresolved_mark(self, child_path_task: asyncio.Task, entity_id: str) -> bool:
        """Waits for child path resolution and schedules marking as unresolved."""
        child_note_path = await child_path_task
        if child_note_path:
            return await self._add_unresolved_reply_marker(child_note_path, entity_id)
        else:
             # logger.warning(f"[{entity_id}] Skipping unresolved marker: child note path could not be resolved.")
             return False # Indicate failure

    async def _add_reply_link(self, parent_note_path: Path, child_note_path: Path, entity_id: str) -> bool:
        """Adds a relative 'Reply to [[child]]' link to the parent note."""
        loop = asyncio.get_running_loop()
        try:
            # Calculate relative path (CPU bound, run in executor)
             # Calculate relative path from the parent note's directory to the child note
            relative_child_path_str = await loop.run_in_executor(
                self.executor,
                get_relative_path,
                child_note_path,
                parent_note_path.parent # Base is parent's directory
            )

            if not relative_child_path_str:
                logger.error(f"[{entity_id}] Failed to calculate relative path from {parent_note_path} to {child_note_path}")
                return False

            # Prepare link text (Obsidian format)
            link_target = relative_child_path_str.replace('.md', '') # Obsidian resolves without .md
            reply_line = f"Reply to: [[{link_target}]]\n" # Add colon for clarity

            # Read, modify, write file content (I/O bound)
            file_lock = await self._get_file_lock(parent_note_path)
            async with self.io_semaphore:
                async with file_lock:
                    async with aiofiles.open(parent_note_path, mode='r+', encoding='utf-8') as f:
                        content = await f.read()
                        # Check if a similar link already exists (simple check)
                        if not content.lstrip().startswith("Reply to:"):
                            logger.debug(f"[{entity_id}] Adding reply link to {parent_note_path.name} -> {child_note_path.name}")
                            await f.seek(0)
                            await f.write(reply_line + content)
                            await f.truncate() # Ensure file is truncated if new content is shorter
                            return True
                        else:
                            # logger.debug(f"[{entity_id}] Reply link already exists in {parent_note_path.name}. Skipping.")
                            return False # Indicate no change made
        except Exception as e:
            logger.error(f"[{entity_id}] Failed to add reply link to {parent_note_path}: {e}", exc_info=self.config.verbose)
            return False

    async def _add_unresolved_reply_marker(self, child_note_path: Path, entity_id: str) -> bool:
        """Adds 'Replied to: [Unresolved Message]' at the beginning of the child note."""
        try:
            reply_line = "Replied to: [Unresolved Message]\n" # Use different text

            # Read, modify, write file content (I/O bound)
            file_lock = await self._get_file_lock(child_note_path)
            async with self.io_semaphore:
                async with file_lock:
                    async with aiofiles.open(child_note_path, mode='r+', encoding='utf-8') as f:
                        content = await f.read()
                        # Avoid adding duplicate markers
                        if not content.lstrip().startswith("Replied to:") and not content.lstrip().startswith("Reply to:"):
                            logger.debug(f"[{entity_id}] Adding 'Replied to: [Unresolved Message]' to {child_note_path.name}")
                            await f.seek(0)
                            await f.write(reply_line + content)
                            await f.truncate()
                            return True
                        else:
                            # logger.debug(f"[{entity_id}] Reply marker already exists in {child_note_path.name}. Skipping unresolved.")
                            return False # Indicate no change made
        except Exception as e:
            logger.error(f"[{entity_id}] Failed to add unresolved reply marker to {child_note_path}: {e}", exc_info=self.config.verbose)
            return False
