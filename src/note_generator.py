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
        """Создает Markdown-файл для сообщения."""
        try:
            note_path = await self._prepare_note_path(message, entity_id, entity_export_path)
            if not note_path:
                return None

            content = await self._generate_note_content(message, media_paths, note_path)

            return await self._write_note_file(note_path, content)

        except Exception as e:
            logger.error(f"Failed to create note for message {getattr(message, 'id', 'unknown')} "
                         f"in entity {entity_id}: {e}",
                         exc_info=self.config.verbose)
            return None

    async def create_note_from_telegraph_url(
            self,
            session: aiohttp.ClientSession,
            url: str,
            notes_export_path: Path,
            media_export_path: Path,
            media_processor: Any
        ) -> Optional[Path]:
            """
            Создает .md файл из статьи Telegra.ph, включая изображения.
            """
            article_data = await fetch_and_parse_telegraph_to_markdown(
                session, url, media_export_path, media_processor
            )
            if not article_data:
                return None

            title = article_data['title']
            content = article_data['content']

            sanitized_title = await run_in_thread_pool(sanitize_filename, title, 80)
            date_str = datetime.now().strftime('%Y-%m-%d')
            filename = f"{date_str}.{sanitized_title}.md"

            telegraph_dir = notes_export_path / 'telegra_ph'
            await run_in_thread_pool(ensure_dir_exists, telegraph_dir)
            note_path = telegraph_dir / filename

            final_content = f"# {title}\n\n{content}\n\n---\n*Источник: {url}*"

            return await self._write_note_file(note_path, final_content)

    async def read_note_content(self, note_path: Path) -> str:
        """Читает содержимое файла заметки."""
        if note_path not in self.file_locks:
            self.file_locks[note_path] = asyncio.Lock()

        async with self.io_semaphore, self.file_locks[note_path]:
            async with aiofiles.open(note_path, 'r', encoding='utf-8') as f:
                return await f.read()

    async def write_note_content(self, note_path: Path, content: str):
        """Перезаписывает содержимое файла заметки."""
        await self._write_note_file(note_path, content)

    async def _prepare_note_path(self, message: Message, entity_id: Union[str, int],
                                 entity_export_path: Path) -> Optional[Path]:
        """Определяет путь для файла заметки на основе даты и заголовка."""
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
            logger.error(f"Failed to prepare note path for message in entity {entity_id}: {e}")
            return None

    async def _generate_note_content(self, message: Message, media_paths: List[Path],
                                     note_path: Path) -> str:
        """Генерирует Markdown-содержимое заметки."""
        message_text = getattr(message, 'text', '') or ""
        content = message_text.strip() + "\n\n" if message_text else ""
        if media_paths:
            media_links = await self._generate_media_links(media_paths, note_path)
            if media_links:
                content += "\n".join(media_links) + "\n\n"
        return content.strip()

    async def _generate_media_links(self, media_paths: List[Path], note_path: Path) -> List[str]:
        """Создает ссылки в формате Obsidian `![[filename.ext]]` для медиафайлов."""
        media_links = []
        for media_path in media_paths:
            if media_path and await run_in_thread_pool(media_path.exists):
                media_links.append(f"![[{media_path.name}]]")
            else:
                logger.warning(f"Media file path does not exist: {media_path}")
        return media_links

    async def _write_note_file(self, note_path: Path, content: str) -> Optional[Path]:
        """Записывает контент в файл с блокировкой для потокобезопасности."""
        if note_path not in self.file_locks:
            self.file_locks[note_path] = asyncio.Lock()

        async with self.io_semaphore, self.file_locks[note_path]:
            try:
                async with aiofiles.open(note_path, 'w', encoding='utf-8') as f:
                    await f.write(content.strip() + '\n')
                logger.info(f"Note file written: {note_path}")
                return note_path
            except Exception as e:
                logger.error(f"Failed to write note file {note_path}: {e}")
                return None

    async def postprocess_all_notes(self, export_root: Path, entity_id: str, cache: dict):
        """
        Второй проход по всем .md-файлам для замены markdown-ссылок t.me
        на внутренние ссылки Obsidian `[[...]]` с использованием полного кэша.
        """
        logger.info(f"[Postprocessing] Starting for all notes in {export_root} for entity '{entity_id}'")

        processed_messages = cache.get("entities", {}).get(entity_id, {}).get("processed_messages", {})
        if not processed_messages:
            logger.warning(f"[Postprocessing] No cache for entity '{entity_id}'. Skipping.")
            return

        url_to_data = {data["telegram_url"]: data for data in processed_messages.values() if data.get("telegram_url")}
        msg_id_to_data = {msg_id: data for msg_id, data in processed_messages.items()}
        logger.info(f"[Postprocessing] Built lookup maps: {len(url_to_data)} URLs, {len(msg_id_to_data)} msg_ids.")

        def replacer(match: re.Match) -> str:
            link_text, url = match.groups()
            url = url.rstrip('/')

            data = url_to_data.get(url)
            if not data:
                msg_id_match = re.search(r"/(\d+)$", url)
                if msg_id_match:
                    data = msg_id_to_data.get(msg_id_match.group(1))

            if data and data.get("filename"):
                fname = data["filename"]
                title = data.get("title", "").replace("\n", " ").strip()
                display = title if title else link_text
                logger.debug(f"Found link '{url}' -> {fname}")
                return f"[[{Path(fname).stem}|{display}]]"

            logger.warning(f"[Postprocessing] No local file found for link: '{url}'")
            return match.group(0)

        pattern = re.compile(r"\[([^\]]+)\]\((https?://t\.me/[^\)]+)\)")
        for md_file in export_root.rglob("*.md"):
            try:
                async with aiofiles.open(md_file, "r+", encoding="utf-8") as f:
                    content = await f.read()
                    new_content = pattern.sub(replacer, content)
                    if new_content != content:
                        await f.seek(0)
                        await f.truncate()
                        await f.write(new_content)
                        logger.info(f"[Postprocessing] Updated links in: {md_file.name}")
            except Exception as e:
                logger.error(f"[Postprocessing] Failed to process file {md_file}: {e}")
        logger.info(f"[Postprocessing] Finished for entity '{entity_id}'.")
