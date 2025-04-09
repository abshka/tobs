import asyncio
import functools
# import os
# from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Union

import aiofiles
from telethon.tl.types import Message

from src.config import Config
from src.utils import logger, sanitize_filename, ensure_dir_exists, get_relative_path

class NoteGenerator:
    def __init__(self, config: Config):
        self.config = config
        # Using default asyncio executor (usually ThreadPoolExecutor) for I/O-bound tasks
        # Can define specific executors if needed, e.g., for CPU-bound sanitization
        self.file_locks: Dict[Path, asyncio.Lock] = {}
        self.io_semaphore = asyncio.Semaphore(20) # Limit concurrent file writes/reads

    async def _get_file_lock(self, path: Path) -> asyncio.Lock:
        """Gets or creates an asyncio Lock for a specific file path."""
        if path not in self.file_locks:
            self.file_locks[path] = asyncio.Lock()
        return self.file_locks[path]

    async def _sanitize_title_async(self, text: str, max_length: int) -> str:
        """Run sanitize_filename in the default executor (thread pool)."""
        loop = asyncio.get_running_loop()
        # sanitize_filename is potentially CPU-bound for complex regex
        return await loop.run_in_executor(
            None, # Uses default executor
            functools.partial(sanitize_filename, text, max_length=max_length)
        )

    async def _ensure_dir_exists_async(self, path: Path):
        """Ensure directory exists asynchronously using the default executor."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, ensure_dir_exists, path)

    async def _generate_markdown_content(
        self,
        message: Message,
        media_paths: List[Path], # List of ABSOLUTE paths to media files
        note_file_path: Path # ABSOLUTE path where the note will be saved
        ) -> str:
        """Generates markdown content, calculating relative media links."""
        loop = asyncio.get_running_loop()
        content = ""

        # 1. Add message text (handle None)
        message_text = getattr(message, 'text', '') or ""
        # Basic markdown escaping (can be enhanced)
        message_text = message_text.replace('_', r'\_').replace('*', r'\*')
        if message_text:
            content += message_text.strip() + "\n\n"

        # 2. Add media links (calculate relative paths)
        media_links_markdown = []
        link_tasks = []

        # Define helper to run in executor
        def calculate_relative(media_path, note_path):
             # Calculate relative path from the note's *parent directory*
            return get_relative_path(media_path, note_path.parent)

        for media_path in media_paths:
            if media_path and media_path.exists():
                # Run path calculation in thread pool
                task = loop.run_in_executor(None, calculate_relative, media_path, note_file_path)
                link_tasks.append(task)
            else:
                logger.warning(f"Media path {media_path} invalid or file missing, skipping link.")
                media_links_markdown.append("[missing media link]") # Placeholder

        # Gather relative paths
        relative_paths = await asyncio.gather(*link_tasks)

        for rel_path in relative_paths:
            if rel_path:
                # Obsidian link format: ![[path/to/media.ext]]
                # Ensure forward slashes and URL encoding for spaces/special chars if needed
                encoded_path = rel_path.replace(' ', '%20') # Basic space encoding
                media_links_markdown.append(f"![[{encoded_path}]]")
            else:
                media_links_markdown.append("[error calculating media link]")

        if media_links_markdown:
            content += "\n".join(media_links_markdown) + "\n\n"

        # 3. Add Metadata (Timestamp, Author - optional)
        # Example: Add timestamp to the end
        # message_date = getattr(message, 'date', datetime.now())
        # content += f"\n---\n*Posted: {message_date.strftime('%Y-%m-%d %H:%M:%S')}*"

        return content.strip()

    def _get_note_filename(self, message: Message, sanitized_title: str) -> str:
        """Generates the filename for the note (without directory)."""
        message_date = getattr(message, 'date', datetime.now())
        # Format: YYYY-MM-DD.Title.md or YYYY-MM-DD.Media-only.md
        date_str = message_date.strftime("%Y-%m-%d")

        # Check if message has text
        message_text = getattr(message, 'text', '') or ""
        if message_text:
            # Use sanitized title with 30 char limit
            short_title = sanitized_title[:30].strip('_')
            filename = f"{date_str}.{short_title}.md"
        else:
            # For messages with only media
            filename = f"{date_str}.Media-only.md"

        return filename

    async def create_note(
        self,
        message: Message,
        media_paths: List[Path], # List of absolute paths to media files
        entity_id: Union[str, int], # Added entity_id
        entity_export_path: Path  # Base path for this entity's notes
    ) -> Optional[Path]:
        """
        Creates or updates a Markdown note file for a given message within the entity's export path.
        Now calculates relative media links.
        """
        try:
            # 1. Prepare Filename Parts (Run title sanitization in parallel)
            first_line = (getattr(message, 'text', '') or "").split('\n', 1)[0]
            sanitize_task = self._sanitize_title_async(first_line, 30)
            sanitized_title = await sanitize_task

            # Generate filename
            filename = self._get_note_filename(message, sanitized_title)

            # Determine full note path within the entity's export directory
            # Optional: Add year subfolder if desired
            message_date = getattr(message, 'date', datetime.now())
            year_dir = entity_export_path / str(message_date.year)
            note_path = year_dir / filename

            # Ensure the directory exists (async)
            await self._ensure_dir_exists_async(note_path.parent)

            # 2. Generate Markdown Content (includes relative link calculation)
            new_content = await self._generate_markdown_content(message, media_paths, note_path)

            # 3. Write to file asynchronously with locking and semaphore
            file_lock = await self._get_file_lock(note_path)
            async with self.io_semaphore:
                async with file_lock:
                    try:
                        async with aiofiles.open(note_path, 'w', encoding='utf-8') as f:
                            await f.write(new_content)
                        logger.debug(f"Created note: {note_path} for msg {message.id} in entity {entity_id}")
                        return note_path
                    except Exception as e:
                        logger.error(f"Failed to write note {note_path}: {e}", exc_info=self.config.verbose)
                        return None # Return None on write failure

        except Exception as e:
            logger.error(f"Failed to prepare note creation for message {getattr(message, 'id', 'unknown')} "
                         f"in entity {entity_id}: {e}", exc_info=self.config.verbose)
            return None

    # --- Sync Wrapper for run_in_executor ---
    # This allows calling the async create_note logic easily from a thread pool executor if needed
    # It simplifies the call site in main.py when offloading note creation.

    def create_note_sync(
        self,
        message: Message,
        media_paths: List[Path],
        entity_id: Union[str, int],
        entity_export_path: Path
    ) -> Optional[Path]:
        """Synchronous wrapper for create_note."""
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        try:
            # Set the loop as the current event loop for this thread
            asyncio.set_event_loop(loop)
            # Run the async function in this new loop and wait for its completion
            return loop.run_until_complete(
                self.create_note(message, media_paths, entity_id, entity_export_path)
            )
        finally:
            # Clean up: close the loop when done
            loop.close()
