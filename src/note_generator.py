import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

import aiofiles
from telethon.tl.types import Message

from src.config import Config
from src.utils import (
    ensure_dir_exists,
    get_relative_path,
    logger,
    run_in_thread_pool,
    sanitize_filename,
)


class NoteGenerator:
    def __init__(self, config: Config):
        self.config = config
        self.file_locks: Dict[Path, asyncio.Lock] = {}
        self.io_semaphore = asyncio.Semaphore(20)

    async def create_note(
        self,
        message: Message,
        media_paths: List[Path],
        entity_id: Union[str, int],
        entity_export_path: Path
    ) -> Optional[Path]:
        """Creates a Markdown note file for a message with relative media links."""
        try:
            # Generate filename from message
            note_path = await self._prepare_note_path(message, entity_id, entity_export_path)
            if not note_path:
                return None

            # Generate note content
            content = await self._generate_note_content(message, media_paths, note_path)

            # Write the file
            return await self._write_note_file(note_path, content, entity_id)

        except Exception as e:
            logger.error(f"Failed to create note for message {getattr(message, 'id', 'unknown')} "
                        f"in entity {entity_id}: {e}",
                        exc_info=self.config.verbose)
            return None

    def create_note_sync(
        self,
        message: Message,
        media_paths: List[Path],
        entity_id: Union[str, int],
        entity_export_path: Path
    ) -> Optional[Path]:
        """Synchronous wrapper for create_note."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.create_note(message, media_paths, entity_id, entity_export_path)
            )
        finally:
            loop.close()

    async def _prepare_note_path(self, message: Message, entity_id: Union[str, int],
                               entity_export_path: Path) -> Optional[Path]:
        """Determines the appropriate note path based on message content and date."""
        try:
            # Get first line of text for title
            message_text = getattr(message, 'text', '') or ""
            first_line = message_text.split('\n', 1)[0]

            # Sanitize title
            sanitized_title = await run_in_thread_pool(
                sanitize_filename, first_line, max_length=30
            )

            # Generate filename
            message_date = getattr(message, 'date', datetime.now())
            date_str = message_date.strftime("%Y-%m-%d")

            if message_text:
                short_title = sanitized_title[:30].strip('_')
                filename = f"{date_str}.{short_title}.md"
            else:
                filename = f"{date_str}.Media-only.md"

            # Create year directory
            year_dir = entity_export_path / str(message_date.year)
            note_path = year_dir / filename

            # Ensure directory exists
            await run_in_thread_pool(ensure_dir_exists, note_path.parent)

            return note_path

        except Exception as e:
            logger.error(f"Failed to prepare note path for message in entity {entity_id}: {e}")
            return None

    async def _generate_note_content(self, message: Message, media_paths: List[Path],
                                  note_path: Path) -> str:
        """Generates the markdown content with escaped text and media links."""
        # Process message text
        message_text = getattr(message, 'text', '') or ""
        # Escape markdown special characters
        message_text = message_text.replace('_', r'\_').replace('*', r'\*')

        content = message_text.strip() + "\n\n" if message_text else ""

        # Generate media links
        if media_paths:
            media_links = await self._generate_media_links(media_paths, note_path)
            if media_links:
                content += "\n".join(media_links) + "\n\n"

        return content.strip()

    async def _generate_media_links(self, media_paths: List[Path], note_path: Path) -> List[str]:
        """Generates markdown links for media files."""
        media_links = []

        # Calculate relative paths in parallel
        link_tasks = []
        for media_path in media_paths:
            if media_path and await run_in_thread_pool(media_path.exists):
                task = asyncio.create_task(run_in_thread_pool(
                    get_relative_path, media_path, note_path.parent
                ))
                link_tasks.append((media_path, task))
            else:
                media_links.append("[missing media link]")

        # Process results
        for media_path, task in link_tasks:
            rel_path = await task
            if rel_path:
                # Ensure spaces in the path are properly URL-encoded
                encoded_path = rel_path.replace(' ', '%20')
                media_links.append(f"![[{encoded_path}]]")
            else:
                logger.warning(f"Failed to calculate relative path for {media_path}")
                media_links.append("[error calculating media link]")

        return media_links

    async def _write_note_file(self, note_path: Path, content: str, entity_id: Union[str, int]) -> Optional[Path]:
        """Writes content to note file with proper locking."""
        # Get or create lock for this file
        if note_path not in self.file_locks:
            self.file_locks[note_path] = asyncio.Lock()
        file_lock = self.file_locks[note_path]

        # Write with lock to prevent concurrent access
        async with self.io_semaphore:
            async with file_lock:
                try:
                    async with aiofiles.open(note_path, 'w', encoding='utf-8') as f:
                        await f.write(content)
                    return note_path
                except Exception as e:
                    logger.error(f"Failed to write note {note_path}: {e}",
                                exc_info=self.config.verbose)
                    return None
