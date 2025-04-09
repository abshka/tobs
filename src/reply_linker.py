from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any
from src.config import Config
from src.cache_manager import CacheManager
from src.utils import logger, get_relative_path
import aiofiles
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os

class ReplyLinker:
    def __init__(self, config: Config, cache_manager: CacheManager):
        self.config = config
        self.cache_manager = cache_manager
        self.max_workers = getattr(config, 'max_workers', 10)  # Default to 10 workers if not specified
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._semaphore = asyncio.Semaphore(self.max_workers * 2)  # Control concurrent I/O operations

    async def link_replies(self):
        """Iterates through cached messages and adds reply links to parent notes."""
        logger.info("Starting reply linking process...")
        processed_messages = self.cache_manager.get_all_processed_messages()

        # Use thread pool to parallelize initial data processing
        loop = asyncio.get_event_loop()
        operations = await loop.run_in_executor(
            self._executor,
            self._prepare_linking_operations,
            processed_messages
        )

        linking_operations, unresolved_operations = operations

        # Process operations concurrently
        tasks = []
        if linking_operations:
            logger.info(f"Attempting to add {len(linking_operations)} reply links...")
            tasks.append(self._process_links_in_batches(linking_operations))
        else:
            logger.info("No reply links to process.")

        if unresolved_operations:
            logger.info(f"Processing {len(unresolved_operations)} unresolved links...")
            tasks.append(self._process_unresolved_in_batches(unresolved_operations))

        if tasks:
            await asyncio.gather(*tasks)

        logger.info("Reply linking process finished.")

    def _prepare_linking_operations(self, processed_messages: Dict[str, Any]) -> Tuple[List[Tuple[Path, Path]], List[str]]:
        """Prepares linking operations in a separate thread to optimize performance."""
        linking_operations = []
        unresolved_operations = []

        # We iterate through messages that *have* a reply_to field
        for child_id_str, msg_data in processed_messages.items():
            parent_id = msg_data.get("reply_to")
            if parent_id:
                parent_id_str = str(parent_id)
                # Check if the parent message was also processed and exists in our vault
                if parent_id_str in processed_messages:
                    parent_filename = processed_messages[parent_id_str].get("filename")
                    child_filename = msg_data.get("filename")

                    if parent_filename and child_filename:
                        parent_note_path = self._find_note_path(parent_filename)
                        child_note_path = self._find_note_path(child_filename)

                        if parent_note_path and child_note_path and parent_note_path.exists():
                            # Add to linking operations list
                            linking_operations.append((parent_note_path, child_note_path))
                        else:
                            logger.warning(f"Could not find note files for reply link: Parent={parent_filename}, Child={child_filename}. Skipping link.")
                            # Add to unresolved operations
                            unresolved_operations.append(child_filename)
                    else:
                         logger.warning(f"Filename missing in cache for reply pair: Parent ID {parent_id_str}, Child ID {child_id_str}")
                         if child_filename:
                             unresolved_operations.append(child_filename)
                else:
                    # Parent message wasn't processed (e.g., deleted, outside scope)
                    logger.warning(f"Parent message {parent_id} for reply {child_id_str} not found in processed cache. Marking as unresolved.")
                    child_filename = msg_data.get("filename")
                    if child_filename:
                        unresolved_operations.append(child_filename)

        return linking_operations, unresolved_operations

    async def _process_links_in_batches(self, operations: List[Tuple[Path, Path]], batch_size: int = 50):
        """Process reply links in batches using a thread pool for I/O operations."""
        total_ops = len(operations)
        logger.info(f"Processing {total_ops} reply links in batches with {self.max_workers} workers")

        # Create semaphore-limited tasks to avoid overwhelming resources
        tasks = [self._add_reply_link_with_semaphore(parent_path, child_path)
                for parent_path, child_path in operations]

        # Use chunking for better progress reporting
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i+batch_size]
            completed = await asyncio.gather(*batch)
            logger.info(f"Processed batch of {len(batch)} reply links ({i+len(batch)}/{total_ops})")

    async def _process_unresolved_in_batches(self, child_filenames: List[str], batch_size: int = 50):
        """Process unresolved links in batches with maximum parallelism."""
        total_ops = len(child_filenames)
        logger.info(f"Processing {total_ops} unresolved links in batches")

        # Create all tasks with semaphore limiting
        tasks = [self._add_unresolved_reply_link_with_semaphore(child_filename)
                for child_filename in child_filenames]

        # Process in chunks for better progress reporting
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i+batch_size]
            completed = await asyncio.gather(*batch)
            logger.info(f"Processed batch of {len(batch)} unresolved links ({i+len(batch)}/{total_ops})")

    async def _find_note_path_async(self, filename: str) -> Optional[Path]:
        """Async version of _find_note_path using executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._find_note_path, filename)

    def _find_note_path(self, filename: str) -> Optional[Path]:
        """Finds the full path of a note given its filename (including year)."""
        # Assumes filename format YYYY.MM.DD.Title.md
        try:
            year = filename.split('.')[0]
            if year.isdigit() and len(year) == 4:
                path = self.config.obsidian_path / year / filename
                # Check existence? The caller checks existence.
                return path
            else:
                logger.error(f"Could not extract year from filename: {filename}")
                return None
        except Exception as e:
            logger.error(f"Error parsing filename {filename} to find path: {e}")
            return None

    async def _add_reply_link_with_semaphore(self, parent_note_path: Path, child_note_path: Path):
        """Wrapper with semaphore to limit concurrent file operations."""
        async with self._semaphore:
            return await self._add_reply_link(parent_note_path, child_note_path)

    async def _add_reply_link(self, parent_note_path: Path, child_note_path: Path):
        """Adds a 'Reply to [[child]]' link to the beginning of the parent note."""
        try:
            # Use executor for CPU-bound operations
            loop = asyncio.get_event_loop()
            relative_child_path = await loop.run_in_executor(
                self._executor,
                get_relative_path,
                child_note_path,
                parent_note_path
            )

            # Offload string operations to thread pool
            link_target = relative_child_path.replace('.md', '')  # Obsidian often resolves without .md
            reply_line = f"Reply to [[{link_target}]]\n"

            async with aiofiles.open(parent_note_path, mode='r+', encoding='utf-8') as f:
                content = await f.read()
                if not content.startswith("Reply to"):  # Avoid adding duplicate links
                    logger.debug(f"Adding reply link to {parent_note_path.name} pointing to {child_note_path.name}")
                    await f.seek(0)
                    await f.write(reply_line + content)
                    await f.truncate()  # Ensure file is truncated if new content is shorter
                else:
                    logger.debug(f"Reply link already exists in {parent_note_path.name}. Skipping.")
            return True
        except Exception as e:
            logger.error(f"Failed to add reply link to {parent_note_path}: {e}", exc_info=self.config.verbose)
            return False

    async def _add_unresolved_reply_link_with_semaphore(self, child_filename: Optional[str]):
        """Wrapper with semaphore to limit concurrent file operations."""
        async with self._semaphore:
            return await self._add_unresolved_reply_link(child_filename)

    async def _add_unresolved_reply_link(self, child_filename: Optional[str]):
        """Adds 'Reply to Unresolved' to the beginning of the child note if the parent is missing."""
        if not child_filename:
            logger.warning("Cannot add unresolved reply link: child filename is missing.")
            return False

        # Use async path finding
        child_note_path = await self._find_note_path_async(child_filename)

        # Execute file existence check in thread pool to avoid blocking
        if not child_note_path:
            logger.warning(f"Cannot add unresolved reply link: child note path not found ({child_filename}).")
            return False

        exists = await asyncio.get_event_loop().run_in_executor(
            self._executor, os.path.exists, child_note_path
        )
        if not exists:
            logger.warning(f"Cannot add unresolved reply link: child note path doesn't exist ({child_filename}).")
            return False

        try:
            reply_line = "Reply to Unresolved\n\n---\n"  # Add separator
            async with aiofiles.open(child_note_path, mode='r+', encoding='utf-8') as f:
                content = await f.read()
                if not content.startswith("Reply to"):  # Avoid adding duplicate links
                    logger.debug(f"Adding 'Reply to Unresolved' to {child_note_path.name}")
                    await f.seek(0)
                    await f.write(reply_line + content)
                    await f.truncate()
                    return True
                else:
                    logger.debug(f"'Reply to' line already exists in {child_note_path.name}. Skipping unresolved.")
                    return False

        except Exception as e:
            logger.error(f"Failed to add 'Reply to Unresolved' to {child_note_path}: {e}", exc_info=self.config.verbose)
            return False
