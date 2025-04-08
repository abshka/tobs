from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Optional
from telethon.tl.types import Message
from src.config import Config
from src.utils import logger, sanitize_filename, ensure_dir_exists
import aiofiles
import asyncio
from concurrent.futures import ThreadPoolExecutor

class NoteGenerator:
    def __init__(self, config: Config):
        self.config = config
        # Create a thread pool for file operations
        self.executor = ThreadPoolExecutor(max_workers=config.max_workers if hasattr(config, 'max_workers') else 5)

    async def create_note(self, message: Message, media_links: List[Tuple[str, Optional[str]]]) -> Optional[Path]:
        """Creates a Markdown note file for a given message."""
        try:
            # 1. Determine Filename - CPU-bound operations
            first_line = (getattr(message, 'text', '') or "").split('\n', 1)[0]
            sanitized_title = sanitize_filename(first_line, max_length=30)
            message_date = getattr(message, 'date', datetime.now())
            timestamp = message_date.strftime("%Y.%m.%d")
            filename = f"{timestamp}.{sanitized_title}.md"

            # 2. Determine Directory (Year-based)
            year = getattr(message_date, 'year', datetime.now().year)
            year_dir = self.config.obsidian_path / str(year)
            ensure_dir_exists(year_dir)
            note_path = year_dir / filename

            # 3. Generate Markdown Content - CPU-bound operations
            content = ""
            # Add message text (handle None)
            if hasattr(message, 'text') and message.text:
                content += message.text + "\n\n"

            # Add media links and captions
            for link, caption in media_links:
                content += link + "\n"
                if caption:
                    content += caption + "\n"
                content += "\n" # Add space after media block

            # 4. Write to File asynchronously - use thread pool for I/O-bound operation
            loop = asyncio.get_running_loop()

            # Define the file writing function that will run in a separate thread
            def write_file(path, data):
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(data)
                return path

            # Run the file writing in a thread pool
            await loop.run_in_executor(
                self.executor,
                write_file,
                note_path,
                content
            )

            logger.info(f"Created note: {note_path}")
            return note_path

        except Exception as e:
            logger.error(f"Failed to create note for message {message.id}: {e}", exc_info=self.config.verbose)
            return None
