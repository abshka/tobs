import asyncio
import functools
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
        self.file_locks: Dict[Path, asyncio.Lock] = {}
        self.io_semaphore = asyncio.Semaphore(20)

    async def _get_file_lock(self, path: Path) -> asyncio.Lock:
        """Gets or creates an asyncio Lock for a specific file path."""
        if path not in self.file_locks:
            self.file_locks[path] = asyncio.Lock()
        return self.file_locks[path]

    async def _sanitize_title_async(self, text: str, max_length: int) -> str:
        """Run sanitize_filename in the default executor (thread pool)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(sanitize_filename, text, max_length=max_length)
        )

    async def _ensure_dir_exists_async(self, path: Path):
        """Ensure directory exists asynchronously using the default executor."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, ensure_dir_exists, path)

    async def _generate_markdown_content(
        self,
        message: Message,
        media_paths: List[Path],
        note_file_path: Path
        ) -> str:
        """Generates markdown content, calculating relative media links."""
        loop = asyncio.get_running_loop()
        content = ""

        message_text = getattr(message, 'text', '') or ""
        message_text = message_text.replace('_', r'\_').replace('*', r'\*')
        if message_text:
            content += message_text.strip() + "\n\n"

        media_links_markdown = []
        link_tasks = []

        def calculate_relative(media_path, note_path):
            return get_relative_path(media_path, note_path.parent)

        for media_path in media_paths:
            if media_path and media_path.exists():
                task = loop.run_in_executor(None, calculate_relative, media_path, note_file_path)
                link_tasks.append(task)
            else:
                logger.warning(f"Media path {media_path} invalid or file missing, skipping link.")
                media_links_markdown.append("[missing media link]")

        relative_paths = await asyncio.gather(*link_tasks)

        for rel_path in relative_paths:
            if rel_path:
                encoded_path = rel_path.replace(' ', '%20')
                media_links_markdown.append(f"![[{encoded_path}]]")
            else:
                media_links_markdown.append("[error calculating media link]")

        if media_links_markdown:
            content += "\n".join(media_links_markdown) + "\n\n"

        return content.strip()

    def _get_note_filename(self, message: Message, sanitized_title: str) -> str:
        """Generates the filename for the note (without directory)."""
        message_date = getattr(message, 'date', datetime.now())
        date_str = message_date.strftime("%Y-%m-%d")

        message_text = getattr(message, 'text', '') or ""
        if message_text:
            short_title = sanitized_title[:30].strip('_')
            filename = f"{date_str}.{short_title}.md"
        else:
            filename = f"{date_str}.Media-only.md"

        return filename

    async def create_note(
        self,
        message: Message,
        media_paths: List[Path],
        entity_id: Union[str, int],
        entity_export_path: Path
    ) -> Optional[Path]:
        """
        Creates or updates a Markdown note file for a given message within the entity's export path.
        Now calculates relative media links.
        """
        try:
            first_line = (getattr(message, 'text', '') or "").split('\n', 1)[0]
            sanitize_task = self._sanitize_title_async(first_line, 30)
            sanitized_title = await sanitize_task

            filename = self._get_note_filename(message, sanitized_title)

            message_date = getattr(message, 'date', datetime.now())
            year_dir = entity_export_path / str(message_date.year)
            note_path = year_dir / filename

            await self._ensure_dir_exists_async(note_path.parent)

            new_content = await self._generate_markdown_content(message, media_paths, note_path)

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
                        return None

        except Exception as e:
            logger.error(f"Failed to prepare note creation for message {getattr(message, 'id', 'unknown')} "
                         f"in entity {entity_id}: {e}", exc_info=self.config.verbose)
            return None

    def create_note_sync(
        self,
        message: Message,
        media_paths: List[Path],
        entity_id: Union[str, int],
        entity_export_path: Path
    ) -> Optional[Path]:
        """Synchronous wrapper for create_note."""
        # Don't create a new event loop - instead create a fresh NoteGenerator
        # with new locks that aren't bound to any event loop yet
        temp_generator = NoteGenerator(self.config)
        
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                temp_generator.create_note(message, media_paths, entity_id, entity_export_path)
            )
        finally:
            loop.close()