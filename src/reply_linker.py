import asyncio
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

import aiofiles

from src.cache_manager import CacheManager
from src.concurrency_manager import ConcurrencyManager
from src.config import Config
from src.utils import get_relative_path, logger
class ReplyLinker:
    """Handles linking replies between messages for a specific entity."""

    def __init__(self, config: Config, cache_manager: CacheManager, concurrency_manager: ConcurrencyManager):
        """
        Initialize the ReplyLinker.

        Args:
            config (Config): The configuration object.
            cache_manager (CacheManager): The cache manager instance.
        Returns:
            None
        """
        self.config = config
        self.cache_manager = cache_manager
        self.concurrency_manager = concurrency_manager
        self.file_locks: Dict[Path, asyncio.Lock] = {}

    async def link_replies(self, entity_id: str, entity_export_path: Path):
        """
        Add reply links between messages for a specific entity.

        Args:
            entity_id (str): The identifier for the entity.
            entity_export_path (Path): The path to the entity's export directory.
        Returns:
            None
        """
        logger.info(f"[{entity_id}] Starting reply linking process...")

        processed_messages = await self.cache_manager.get_all_processed_messages_async(entity_id)
        if not processed_messages:
            logger.info(f"[{entity_id}] No processed messages found in cache. Skipping reply linking.")
            return

        links_to_add: List[Tuple[str, str]] = []
        unresolved_child_files: Set[str] = set()

        for child_id, msg_data in processed_messages.items():
            parent_id = msg_data.get("reply_to")
            child_filename = msg_data.get("filename")

            if not child_filename:
                continue

            if parent_id:
                parent_id_str = str(parent_id)
                parent_data = processed_messages.get(parent_id_str)

                if parent_data and parent_data.get("filename"):
                    links_to_add.append((parent_data["filename"], child_filename))
                else:
                    unresolved_child_files.add(child_filename)

        logger.info(f"[{entity_id}] Processing {len(links_to_add)} reply links and {len(unresolved_child_files)} unresolved links")

        results = await asyncio.gather(
            self._process_reply_links(links_to_add, entity_export_path, entity_id),
            self._process_unresolved_replies(unresolved_child_files, entity_export_path, entity_id),
            return_exceptions=True
        )

        if isinstance(results[0], Exception):
            logger.error(f"[{entity_id}] Error processing reply links: {results[0]}")
        if isinstance(results[1], Exception):
            logger.error(f"[{entity_id}] Error processing unresolved links: {results[1]}")

        logger.info(f"[{entity_id}] Finished processing reply links")

    async def _process_reply_links(self, links: List[Tuple[str, str]], entity_export_path: Path, entity_id: str) -> int:
        """
        Process reply links and add them to the appropriate files.

        Args:
            links (List[Tuple[str, str]]): List of (parent_filename, child_filename) tuples.
            entity_export_path (Path): The path to the entity's export directory.
            entity_id (str): The identifier for the entity.
        Returns:
            int: The number of successfully processed links.
        """
        if not links:
            return 0

        success_count = 0
        batch_size = 20
        for i in range(0, len(links), batch_size):
            batch = links[i:i+batch_size]

            tasks = []
            for parent_filename, child_filename in batch:
                task = asyncio.create_task(self._link_parent_to_child(
                    parent_filename, child_filename, entity_export_path, entity_id
                ))
                tasks.append(task)

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count += sum(1 for result in batch_results if result)

        return success_count

    async def _process_unresolved_replies(self, filenames: Set[str], entity_export_path: Path, entity_id: str) -> int:
        """
        Mark replies as unresolved if the parent message is not found.

        Args:
            filenames (Set[str]): Set of filenames to mark as unresolved.
            entity_export_path (Path): The path to the entity's export directory.
            entity_id (str): The identifier for the entity.
        Returns:
            int: The number of successfully marked unresolved replies.
        """
        if not filenames:
            return 0

        success_count = 0
        batch_size = 20
        filenames_list = list(filenames)
        for i in range(0, len(filenames_list), batch_size):
            batch = filenames_list[i:i+batch_size]

            tasks = []
            for filename in batch:
                task = asyncio.create_task(self._mark_as_unresolved(
                    filename, entity_export_path, entity_id
                ))
                tasks.append(task)

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count += sum(1 for result in batch_results if result)

        return success_count

    async def _link_parent_to_child(
        self, parent_filename: str, child_filename: str, entity_export_path: Path, entity_id: str
    ) -> bool:
        """
        Add a reply link from the parent file to the child file.

        Args:
            parent_filename (str): The filename of the parent message.
            child_filename (str): The filename of the child message.
            entity_export_path (Path): The path to the entity's export directory.
            entity_id (str): The identifier for the entity.
        Returns:
            bool: True if the link was added successfully, False otherwise.
        """
        parent_path = await self._find_note_path(entity_export_path, parent_filename)
        child_path = await self._find_note_path(entity_export_path, child_filename)

        if not parent_path or not child_path:
            return False

        try:
            relative_path = get_relative_path(child_path, parent_path.parent)

            if not relative_path:
                logger.error(f"[{entity_id}] Failed to calculate relative path: {parent_path} -> {child_path}")
                return False

            link_target = urllib.parse.unquote(relative_path.replace('.md', ''))
            link_text = f"Reply to: [[{link_target}]]\n"

            return await self._add_line_to_file(parent_path, link_text,
                                              check_existing="Reply to:", entity_id=entity_id)

        except Exception as e:
            logger.error(f"[{entity_id}] Error linking {parent_filename} to {child_filename}: {e}")
            return False

    async def _mark_as_unresolved(self, child_filename: str, entity_export_path: Path, entity_id: str) -> bool:
        """
        Mark a message as having an unresolved reply.

        Args:
            child_filename (str): The filename of the child message.
            entity_export_path (Path): The path to the entity's export directory.
            entity_id (str): The identifier for the entity.
        Returns:
            bool: True if the marker was added successfully, False otherwise.
        """
        child_path = await self._find_note_path(entity_export_path, child_filename)
        if not child_path:
            return False

        try:
            marker_text = "Replied to: [Unresolved Message]\n"
            return await self._add_line_to_file(
                child_path, marker_text,
                check_existing=("Replied to:", "Reply to:"),
                entity_id=entity_id
            )
        except Exception as e:
            logger.error(f"[{entity_id}] Error marking {child_filename} as unresolved: {e}")
            return False

    async def _find_note_path(self, base_path: Path, filename: str) -> Optional[Path]:
        """
        Find the path to a note file, searching in the base path and year subdirectory.

        Args:
            base_path (Path): The base directory to search in.
            filename (str): The filename to find.
        Returns:
            Optional[Path]: The path to the note file if found, otherwise None.
        """
        note_path = base_path / filename
        if note_path.exists():
            return note_path

        try:
            year = filename.split('-')[0]
            if year.isdigit() and len(year) == 4:
                year_path = base_path / year / filename
                if year_path.exists():
                    return year_path
        except Exception:
            pass

        return None

    async def _add_line_to_file(self, file_path: Path, line: str, check_existing: Union[str, Tuple[str, ...]], entity_id: str) -> bool:
        """
        Add a line to the beginning of a file if it does not already exist.

        Args:
            file_path (Path): The path to the file.
            line (str): The line to add.
            check_existing (Union[str, Tuple[str, ...]]): Prefix or prefixes to check for existing content.
            entity_id (str): The identifier for the entity.
        Returns:
            bool: True if the line was added, False otherwise.
        """
        if file_path not in self.file_locks:
            self.file_locks[file_path] = asyncio.Lock()
        file_lock = self.file_locks[file_path]

        checks = (check_existing,) if isinstance(check_existing, str) else check_existing

        async with self.concurrency_manager.io_semaphore:
            async with file_lock:
                try:
                    async with aiofiles.open(file_path, mode='r+', encoding='utf-8') as f:
                        content = await f.read()
                        content_start = content.lstrip()
                        if any(content_start.startswith(check) for check in checks):
                            return False

                        await f.seek(0)
                        await f.write(line + content)
                        await f.truncate()
                        return True

                except Exception as e:
                    log_level = 'error' if self.config.log_level.upper() == 'DEBUG' else 'warning'
                    getattr(logger, log_level)(f"[{entity_id}] Failed to update {file_path}: {e}")
                    return False
