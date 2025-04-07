from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Optional
from telethon.tl.types import Message
from src.config import Config
from src.utils import logger, sanitize_filename, ensure_dir_exists
import aiofiles

class NoteGenerator:
    def __init__(self, config: Config):
        self.config = config

    async def create_note(self, message: Message, media_links: List[Tuple[str, Optional[str]]]) -> Optional[Path]:
        """Creates a Markdown note file for a given message."""
        try:
            # 1. Determine Filename
            first_line = (message.text or "").split('\n', 1)[0]
            sanitized_title = sanitize_filename(first_line, max_length=30)
            timestamp = message.date.strftime("%Y.%m.%d")
            filename = f"{timestamp}.{sanitized_title}.md"

            # 2. Determine Directory (Year-based)
            year_dir = self.config.obsidian_path / str(message.date.year)
            ensure_dir_exists(year_dir)
            note_path = year_dir / filename

            # 3. Generate Markdown Content
            content = ""
            # Add message text (handle None)
            if message.text:
                content += message.text + "\n\n"

            # Add media links and captions
            for link, caption in media_links:
                content += link + "\n"
                if caption:
                    content += caption + "\n"
                content += "\n" # Add space after media block

            # 4. Write to File asynchronously
            async with aiofiles.open(note_path, mode='w', encoding='utf-8') as f:
                await f.write(content)

            logger.info(f"Created note: {note_path}")
            return note_path

        except Exception as e:
            logger.error(f"Failed to create note for message {message.id}: {e}", exc_info=self.config.verbose)
            return None
