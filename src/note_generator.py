# src/note_generator.py

"""
NoteGenerator: Handles creation and post-processing of Markdown notes from Telegram messages.
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiofiles
import aiohttp
from telethon.tl.types import Message

from src.config import Config
from src.utils import (
    ensure_dir_exists,
    fetch_and_parse_telegraph_to_markdown,
    logger,
    run_in_thread_pool,
    sanitize_filename,
)


class NoteGenerator:
    """
    Handles creation and post-processing of Markdown notes from Telegram messages.
    """
    def __init__(self, config: Config):
        self.config = config
        self.file_locks: Dict[Path, asyncio.Lock] = {}
        self.io_semaphore = asyncio.Semaphore(20)

    async def create_note(
        self,
        message: Message,
        media_paths: List[Path],
        entity_id: Union[str, int],
        entity_export_path: Path,
    ) -> Optional[Path]:
        """
        Creates a Markdown file for a Telegram message.
        """
        try:
            note_path = await self._prepare_note_path(message, entity_id, entity_export_path)
            if not note_path:
                return None

            content = await self._generate_note_content(message, media_paths, note_path)
            return await self._write_note_file(note_path, content)

        except Exception as e:
            logger.error(
                f"Failed to create note for message {getattr(message, 'id', 'unknown')} in entity {entity_id}: {e}",
                exc_info=True
            )
            return None

    async def create_note_from_telegraph_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
        notes_export_path: Path,
        media_export_path: Path,
        media_processor: Any,
        cache: dict,
        entity_id: str,
        telegraph_mapping: dict = None
    ) -> Optional[Path]:
        """
        Creates a .md file from a Telegra.ph article, including images.
        Also updates telegraph_mapping with url -> note_name.
        The note filename uses the publication date from the article if available.
        """
        article_data = await fetch_and_parse_telegraph_to_markdown(
            session, url, media_export_path, media_processor, cache, entity_id, telegraph_mapping
        )
        if not article_data:
            return None

        title = article_data['title']
        content = article_data['content']

        pub_date = article_data.get('pub_date')
        date_str = pub_date if pub_date else datetime.now().strftime('%Y-%m-%d')

        sanitized_title = await run_in_thread_pool(sanitize_filename, title, 80)
        filename = f"{date_str}.{sanitized_title}.md"

        telegraph_dir = notes_export_path / 'telegra_ph'
        await run_in_thread_pool(ensure_dir_exists, telegraph_dir)
        note_path = telegraph_dir / filename

        if telegraph_mapping is not None:
            telegraph_mapping[url] = note_path.stem

        final_content = f"# {title}\n\n{content}\n\n---\n*Source: {url}*"

        return await self._write_note_file(note_path, final_content)

    async def read_note_content(self, note_path: Path) -> str:
        """
        Reads the content of a note file.
        """
        if note_path not in self.file_locks:
            self.file_locks[note_path] = asyncio.Lock()

        async with self.io_semaphore, self.file_locks[note_path]:
            async with aiofiles.open(note_path, 'r', encoding='utf-8') as f:
                return await f.read()

    async def write_note_content(self, note_path: Path, content: str):
        """
        Overwrites the content of a note file.
        """
        await self._write_note_file(note_path, content)

    async def _prepare_note_path(self, message: Message, entity_id: Union[str, int],
                                 entity_export_path: Path) -> Optional[Path]:
        """
        Determines the path for a note file based on date and title.
        """
        try:
            message_text = getattr(message, 'text', '') or ""
            first_line = message_text.split('\n', 1)[0]
            sanitized_title = await run_in_thread_pool(sanitize_filename, first_line, 60)
            message_date = getattr(message, 'date', datetime.now())
            date_str = message_date.strftime("%Y-%m-%d")

            if sanitized_title:
                filename = f"{date_str}.{sanitized_title}.md"
            else:
                filename = f"{date_str}.Message-{message.id}.md"

            year_dir = entity_export_path / str(message_date.year)
            note_path = year_dir / filename
            await run_in_thread_pool(ensure_dir_exists, note_path.parent)
            return note_path
        except Exception as e:
            logger.error(f"Failed to prepare note path for message in entity {entity_id}: {e}", exc_info=True)
            return None

    async def _generate_note_content(self, message: Message, media_paths: List[Path],
                                     note_path: Path) -> str:
        """
        Generates Markdown content for a note.
        """
        message_text = getattr(message, 'text', '') or ""
        content = message_text.strip() + "\n\n" if message_text else ""
        if media_paths:
            media_links = await self._generate_media_links(media_paths, note_path)
            if media_links:
                content += "\n".join(media_links) + "\n\n"
        return content.strip()

    async def _generate_media_links(self, media_paths: List[Path], note_path: Path) -> List[str]:
        """
        Creates Obsidian-style links for media files.
        """
        media_links = []
        for media_path in media_paths:
            if media_path and await run_in_thread_pool(media_path.exists):
                media_links.append(f"![[{media_path.name}]]")
            else:
                logger.warning(f"Media file path does not exist: {media_path}")
        return media_links

    async def _write_note_file(self, note_path: Path, content: str) -> Optional[Path]:
        """
        Writes content to a note file with locking for thread safety.
        """
        if note_path not in self.file_locks:
            self.file_locks[note_path] = asyncio.Lock()

        async with self.io_semaphore, self.file_locks[note_path]:
            try:
                async with aiofiles.open(note_path, 'w', encoding='utf-8') as f:
                    await f.write(content.strip() + '\n')
                return note_path
            except Exception as e:
                logger.error(f"Failed to write note file {note_path}: {e}", exc_info=True)
                return None

    async def postprocess_all_notes(self, export_root: Path, entity_id: str, cache: dict):
        """
        Second pass through all .md files to replace t.me markdown links
        with internal Obsidian links using the full cache.
        """
        from rich import print as rprint
        rprint(f"[green]*** Post-processing links for entity {entity_id} ***[/green]")

        processed_messages = cache.get("entities", {}).get(entity_id, {}).get("processed_messages", {})
        if not processed_messages:
            rprint(f"[yellow]No cache for entity '{entity_id}'. Skipping post-processing.[/yellow]")
            return

        url_to_data = {data["telegram_url"]: data for data in processed_messages.values() if data.get("telegram_url")}
        msg_id_to_data = {msg_id: data for msg_id, data in processed_messages.items()}
        # logger.info(f"[Postprocessing] Built lookup maps: {len(url_to_data)} URLs, {len(msg_id_to_data)} msg_ids.")

        def replacer(match: re.Match) -> str:
            link_text, url = match.groups()
            clean_url = url.split('?')[0].rstrip('/')

            data = url_to_data.get(clean_url)
            if not data:
                if msg_id_match := re.search(r"/(\d+)$", clean_url):
                    data = msg_id_to_data.get(msg_id_match.group(1))

            if data and (fname := data.get("filename")):
                title = data.get("title", "").replace("\n", " ").strip()
                display = title if title else link_text
                logger.debug(f"Found link '{url}' -> {fname}")
                return f"[[{Path(fname).stem}|{display}]]"
            rprint(f"[yellow]No local file found for link: '{url}'[/yellow]")
            return match.group(0)

        pattern = re.compile(r"\[([^\]]+)\]\((https?://t\.me/[^\)]+)\)")
        updated_files_count = 0
        for md_file in export_root.rglob("*.md"):
            try:
                async with aiofiles.open(md_file, "r+", encoding="utf-8") as f:
                    content = await f.read()
                    new_content = pattern.sub(replacer, content)
                    if new_content != content:
                        await f.seek(0)
                        await f.truncate()
                        await f.write(new_content)
                        updated_files_count += 1
            except Exception as e:
                logger.error(f"[Postprocessing] Failed to process file {md_file}: {e}", exc_info=True)

        rprint(f"[green]Post-processing complete. Updated links in {updated_files_count} file(s).[/green]")
