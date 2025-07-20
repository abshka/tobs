import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiofiles
import aiohttp
from rich import print as rprint
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    User,
)

from src.config import Config
from src.media_processor import MediaProcessor
from src.utils import (
    ensure_dir_exists,
    fetch_and_parse_telegraph_to_markdown,
    logger,
    sanitize_filename,
)


class NoteGenerator:
    """
    Handles creation and post-processing of Markdown notes from Telegram messages.
    """

    def __init__(self, config: Config):
        """
        Initialize NoteGenerator.

        Args:
            config (Config): Configuration object.
        """
        self.config = config
        self.file_locks: Dict[Path, asyncio.Lock] = {}
        self.io_semaphore = asyncio.Semaphore(20)

    async def create_note(
        self,
        message: Message,
        media_paths: List[Path],
        entity_id: Union[str, int],
        entity_export_path: Path,
        client=None,
        export_comments: bool = False,
        entity_media_path: Path = None
    ) -> Optional[Path]:
        """
        Create a note from a Telegram message and optionally add comments.

        Args:
            message (Message): Telegram message object.
            media_paths (List[Path]): List of media file paths.
            entity_id (Union[str, int]): Entity identifier.
            entity_export_path (Path): Path to export notes.
            client: Telethon client for exporting comments.
            export_comments (bool, optional): Whether to export comments. Defaults to False.
            entity_media_path (Path, optional): Path for media files.

        Returns:
            Optional[Path]: Path to the created note file, or None if failed.
        """
        try:
            note_path = await self._prepare_note_path(message, entity_id, entity_export_path)
            if not note_path:
                return None

            content = await self._generate_note_content(message, media_paths, note_path)

            if export_comments and client is not None:
                media_dir = entity_export_path / "media"
                ensure_dir_exists(media_dir)

                media_processor = MediaProcessor(self.config, client)
                comments_md = await self.export_comments_md(
                    message, client, media_dir, media_processor=media_processor, entity_id=str(entity_id), progress=self.progress if hasattr(self, "progress") else None
                )
                if comments_md:
                    content += "\n\n---\n### Comments\n" + comments_md

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
        Create a note from a Telegraph article URL.

        Args:
            session (aiohttp.ClientSession): HTTP session.
            url (str): Telegraph article URL.
            notes_export_path (Path): Path to export notes.
            media_export_path (Path): Path to export media.
            media_processor (Any): Media processor instance.
            cache (dict): Cache dictionary.
            entity_id (str): Entity identifier.
            telegraph_mapping (dict, optional): Mapping for telegraph URLs.

        Returns:
            Optional[Path]: Path to the created note file, or None if failed.
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

        sanitized_title = sanitize_filename(title, 80)
        filename = f"{date_str}.{sanitized_title}.md"

        telegraph_dir = notes_export_path / 'telegra_ph'
        ensure_dir_exists(telegraph_dir)
        note_path = telegraph_dir / filename

        if telegraph_mapping is not None:
            telegraph_mapping[url] = note_path.stem

        final_content = f"# {title}\n\n{content}\n\n---\n*Source: {url}*"

        return await self._write_note_file(note_path, final_content)

    async def read_note_content(self, note_path: Path) -> str:
        """
        Read the content of a note file.

        Args:
            note_path (Path): Path to the note file.

        Returns:
            str: Content of the note file.
        """
        if note_path not in self.file_locks:
            self.file_locks[note_path] = asyncio.Lock()

        async with self.io_semaphore, self.file_locks[note_path]:
            async with aiofiles.open(note_path, 'r', encoding='utf-8') as f:
                return await f.read()

    async def export_comments_md(self, main_post: Message, client, media_dir: Path, media_processor=None, entity_id=None, progress=None) -> str:
        """
        Export comments for a post in markdown format.

        Args:
            main_post (Message): Main post message.
            client: Telethon client.
            media_dir (Path): Directory for media files.
            media_processor (Any, optional): MediaProcessor for sorting media.
            entity_id (str, optional): String id of the entity (channel/chat).

        Returns:
            str: Markdown block with comments.
        """
        try:
            channel_id = getattr(main_post, "chat_id", None)
            if channel_id is None and getattr(main_post, "to_id", None):
                channel_id = getattr(main_post.to_id, "channel_id", None)
            if channel_id is None:
                return ""
            comments = []
            async for comment in client.iter_messages(channel_id, reply_to=main_post.id, reverse=True):
                comments.append(comment)


            if not comments:
                return "\n\n*No comments.*\n"

            md = ""
            # Собираем все комментарии с медиа
            comment_media_info = []
            for comment in comments:
                sender = comment.sender
                sender_name = self._get_sender_name(sender)
                comment_text = comment.text.replace('\n', '\n> ') if comment.text else ''
                md += f"\n> **{sender_name}** ({comment.date.strftime('%Y-%m-%d %H:%M')}):\n"
                if comment_text:
                    md += f"> {comment_text}\n"
                if comment.media and media_processor is not None and entity_id is not None:
                    media_type = self.get_media_type(comment)
                    type_subdir = media_dir / f"{media_type}s" if media_type != "unknown" else media_dir
                    ensure_dir_exists(type_subdir)
                    filename = media_processor._get_filename(
                        comment.media.photo if hasattr(comment.media, "photo") else comment.media.document,
                        comment.id,
                        media_type,
                        entity_id
                    ) if hasattr(media_processor, "_get_filename") else None
                    comment_media_info.append({
                        "comment": comment,
                        "media_type": media_type,
                        "type_subdir": type_subdir,
                        "filename": filename
                    })
                elif comment.media:
                    comment_media_info.append({
                        "comment": comment,
                        "media_type": "unknown",
                        "type_subdir": media_dir,
                        "filename": None
                    })

            # Прогресс-бар для скачивания медиа из комментариев
            if comment_media_info and progress is not None:
                tasks = []
                for info in comment_media_info:
                    comment = info["comment"]
                    filename = info["filename"] or f"comment_media_{comment.id}"
                    media_type = info["media_type"]
                    type_subdir = info["type_subdir"]
                    total_size = getattr(comment, "file", None).size if hasattr(comment, "file") and comment.file else 0
                    task_id = progress.add_task("download", filename=filename, total=total_size)
                    tasks.append((comment, type_subdir, filename, media_type, task_id))
                # Скачиваем медиа асинхронно с прогрессом
                results = await asyncio.gather(*[
                    t[0].download_media(file=t[1] / t[2] if t[2] else t[1])
                    for t in tasks
                ])
                # Добавляем ссылки на медиа в markdown
                for idx, info in enumerate(comment_media_info):
                    file_path = results[idx]
                    if file_path:
                        file_name = Path(file_path).name
                        media_type = info["media_type"]
                        relative_media_path = f"media/{media_type}s/{file_name}" if media_type != "unknown" else f"media/{file_name}"
                        md += f"> ![[{relative_media_path}]]\n"
            return md
        except Exception as e:
            # Игнорируем ошибку GetRepliesRequest (нет комментариев)
            if "The message ID used in the peer was invalid" in str(e):
                return ""
            logger.error(f"Failed to export comments for post {main_post.id}: {e}")
            return ""

    def _get_sender_name(self, sender):
        """
        Return formatted sender name.

        Args:
            sender: Sender object.

        Returns:
            str: Formatted sender name.
        """
        if sender is None:
            return "Unknown"
        if isinstance(sender, User):
            if sender.first_name and sender.last_name:
                return f"{sender.first_name} {sender.last_name}"
            return sender.first_name or sender.username or f"User {sender.id}"
        if hasattr(sender, 'title'):
            return sender.title
        return "Unknown"

    def get_media_type(self, message: Message) -> str:
        """
        Determine the media type for sorting into folders.

        Args:
            message (Message): Telegram message object.

        Returns:
            str: Media type ("image", "video", "audio", "document", or "unknown").
        """
        if hasattr(message, "media"):
            if message.media is None:
                return "unknown"

            if isinstance(message.media, MessageMediaPhoto):
                return "image"
            if isinstance(message.media, MessageMediaDocument):
                doc = message.media.document
                if doc is not None and hasattr(doc, "attributes"):
                    for attr in doc.attributes:
                        if isinstance(attr, DocumentAttributeVideo):
                            return "video"
                        if isinstance(attr, DocumentAttributeAudio):
                            return "audio"
                return "document"
        return "unknown"

    async def write_note_content(self, note_path: Path, content: str):
        """
        Write content to a note file.

        Args:
            note_path (Path): Path to the note file.
            content (str): Content to write.

        Returns:
            None
        """
        await self._write_note_file(note_path, content)

    async def _prepare_note_path(self, message: Message, entity_id: Union[str, int],
                                 entity_export_path: Path) -> Optional[Path]:
        """
        Prepare the file path for a note.

        Args:
            message (Message): Telegram message object.
            entity_id (Union[str, int]): Entity identifier.
            entity_export_path (Path): Path to export notes.

        Returns:
            Optional[Path]: Path to the note file, or None if failed.
        """
        try:
            message_text = getattr(message, 'text', '') or ""
            first_line = message_text.split('\n', 1)[0]
            sanitized_title = sanitize_filename(first_line, 60)
            message_date = getattr(message, 'date', datetime.now())
            date_str = message_date.strftime("%Y-%m-%d")

            if sanitized_title:
                filename = f"{date_str}.{sanitized_title}.md"
            else:
                filename = f"{date_str}.Message-{message.id}.md"

            year_dir = entity_export_path / str(message_date.year)
            note_path = year_dir / filename
            ensure_dir_exists(note_path.parent)
            return note_path
        except Exception as e:
            logger.error(f"Failed to prepare note path for message in entity {entity_id}: {e}", exc_info=True)
            return None

    async def _generate_note_content(self, message: Message, media_paths: List[Path],
                                     note_path: Path) -> str:
        """
        Generate the content for a note.

        Args:
            message (Message): Telegram message object.
            media_paths (List[Path]): List of media file paths.
            note_path (Path): Path to the note file.

        Returns:
            str: Generated note content.
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
        Generate markdown links for media files.

        Args:
            media_paths (List[Path]): List of media file paths.
            note_path (Path): Path to the note file.

        Returns:
            List[str]: List of markdown links for media files.
        """
        media_links = []
        for media_path in media_paths:
            if media_path and media_path.exists():
                media_links.append(f"![[{media_path.name}]]")
            else:
                logger.warning(f"Media file path does not exist: {media_path}")
        return media_links

    async def _write_note_file(self, note_path: Path, content: str) -> Optional[Path]:
        """
        Write content to a note file with concurrency control.

        Args:
            note_path (Path): Path to the note file.
            content (str): Content to write.

        Returns:
            Optional[Path]: Path to the written note file, or None if failed.
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
        Post-process all notes to update Telegram links to local note links.

        Args:
            export_root (Path): Root path where notes are exported.
            entity_id (str): Entity identifier.
            cache (dict): Cache dictionary.

        Returns:
            None
        """

        rprint(f"[green]*** Post-processing links for entity {entity_id} ***[/green]")

        processed_messages = cache.get("entities", {}).get(entity_id, {}).get("processed_messages", {})
        if not processed_messages:
            rprint(f"[yellow]No cache for entity '{entity_id}'. Skipping post-processing.[/yellow]")
            return

        url_to_data = {data["telegram_url"]: data for data in processed_messages.values() if data.get("telegram_url")}
        msg_id_to_data = {msg_id: data for msg_id, data in processed_messages.items()}

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
