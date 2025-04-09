from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Optional
from telethon.tl.types import Message
from src.config import Config
from src.utils import logger, sanitize_filename, ensure_dir_exists
import aiofiles
import asyncio
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import functools

class NoteGenerator:
    def __init__(self, config: Config):
        self.config = config
        # Create thread and process pools for different kinds of operations
        self.io_executor = ThreadPoolExecutor(max_workers=config.max_workers if hasattr(config, 'max_workers') else 5)
        self.cpu_executor = ProcessPoolExecutor(max_workers=max(1, (config.max_workers // 2) if hasattr(config, 'max_workers') else 2))
        self.semaphore = asyncio.Semaphore(10)  # Limit concurrent file operations

    async def _sanitize_title_async(self, text, max_length):
        """Run sanitize_filename in the process pool as it's CPU-bound."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.cpu_executor,
            functools.partial(sanitize_filename, text, max_length=max_length)
        )

    async def _ensure_dir_exists_async(self, path):
        """Ensure directory exists asynchronously."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.io_executor,
            ensure_dir_exists,
            path
        )

    async def _write_file_async(self, path, content):
        """Write to file using aiofiles for true async I/O."""
        async with self.semaphore:
            async with aiofiles.open(path, 'w', encoding='utf-8') as f:
                await f.write(content)
            return path

    async def _generate_markdown_content(self, message, media_links):
        """Generate markdown content in a separate task."""
        content = ""
        # Add message text (handle None)
        if hasattr(message, 'text') and message.text:
            content += message.text + "\n\n"

        # Add media links and captions
        for link, caption in media_links:
            content += link + "\n"
            if caption:
                content += caption + "\n"
            content += "\n"  # Add space after media block

        return content

    async def create_note(self, message: Message, media_links: List[Tuple[str, Optional[str]]]) -> Optional[Path]:
        """Creates a Markdown note file for a given message."""
        try:
            # Run multiple operations concurrently where possible
            first_line = (getattr(message, 'text', '') or "").split('\n', 1)[0]

            # Create tasks for concurrent operations
            sanitize_task = self._sanitize_title_async(first_line, 30)

            # These operations can run in parallel
            message_date = getattr(message, 'date', datetime.now())
            year = getattr(message_date, 'year', datetime.now().year)
            year_dir = self.config.obsidian_path / str(year)

            # Run tasks concurrently
            sanitized_title = await sanitize_task

            # Start content generation early
            content_task = self._generate_markdown_content(message, media_links)

            # Prepare directory while content is being generated
            dir_task = self._ensure_dir_exists_async(year_dir)

            # Prepare filename parts
            timestamp = message_date.strftime("%Y.%m.%d")
            filename = f"{timestamp}.{sanitized_title}.md"
            note_path = year_dir / filename

            # Wait for directory to be ready
            await dir_task

            # Wait for content to be ready
            content = await content_task

            # Write the file asynchronously
            await self._write_file_async(note_path, content)

            logger.info(f"Created note: {note_path}")
            return note_path

        except Exception as e:
            logger.error(f"Failed to create note for message {message.id}: {e}", exc_info=self.config.verbose)
            return None
