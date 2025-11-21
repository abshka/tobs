import asyncio
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import aiofiles
import aiohttp
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    User,
)

# ConcurrencyManager removed - now handled by core system
from src.config import Config
from src.media import MediaProcessor
from src.utils import (
    ensure_dir_exists,
    fetch_and_parse_telegraph_to_markdown,
    get_relative_path,
    logger,
    sanitize_filename,
)


class NoteGenerator:
    """
    Handles creation and post-processing of Markdown notes from Telegram messages.
    """

    def __init__(self, config: Config, connection_manager=None, cache_manager=None):
        """
        Initialize NoteGenerator.

        Args:
            config (Config): Configuration object.
            connection_manager: Connection manager for IO operations.
            cache_manager: Cache manager for reply linking.
        """
        self.config = config
        self.connection_manager = connection_manager
        self.cache_manager = cache_manager
        self.file_locks: Dict[Path, asyncio.Lock] = {}

    async def create_note(
        self,
        message: Message,
        media_paths: List[Path],
        entity_id: Union[str, int],
        entity_export_path: Path,
        client=None,
        export_comments: bool = False,
        entity_media_path: Optional[Path] = None,
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
            note_path = await self._prepare_note_path(
                message, entity_id, entity_export_path
            )
            if not note_path:
                return None

            content = await self._generate_note_content(message, media_paths, note_path)

            if export_comments and client is not None:
                media_dir = entity_export_path / "media"
                ensure_dir_exists(media_dir)

                media_processor = MediaProcessor(
                    self.config, client, connection_manager=self.connection_manager
                )
                comments_md = await self.export_comments_md(
                    message,
                    client,
                    media_dir,
                    media_processor=media_processor,
                    entity_id=str(entity_id),
                    progress=self.progress if hasattr(self, "progress") else None,
                )
                if comments_md:
                    content += "\n\n---\n### Comments\n" + comments_md

            return await self._write_note_file(note_path, content)

        except Exception as e:
            logger.error(
                f"Failed to create note for message {getattr(message, 'id', 'unknown')} in entity {entity_id}: {e}",
                exc_info=True,
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
        telegraph_mapping: Optional[dict] = None,
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
            session,
            url,
            media_export_path,
            media_processor,
            cache,
            entity_id,
            telegraph_mapping,
        )
        if not article_data:
            return None

        title = article_data["title"]
        content = article_data["content"]

        pub_date = article_data.get("pub_date")
        date_str = pub_date if pub_date else datetime.now().strftime("%Y-%m-%d")

        sanitized_title = sanitize_filename(title, 80)
        filename = f"{date_str}.{sanitized_title}.md"

        telegraph_dir = notes_export_path / "telegra_ph"
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

        async with self.connection_manager.io_semaphore, self.file_locks[note_path]:
            async with aiofiles.open(note_path, "r", encoding="utf-8") as f:
                return await f.read()

    async def export_comments_md(
        self,
        main_post: Message,
        client,
        media_dir: Path,
        media_processor=None,
        entity_id=None,
        progress=None,
    ) -> str:
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
        # Retry logic for FloodWaitError
        max_retries = 3
        retry_count = 0
        last_error = None

        while retry_count < max_retries:
            try:
                channel_id = getattr(main_post, "chat_id", None)
                if channel_id is None and getattr(main_post, "to_id", None):
                    channel_id = getattr(main_post.to_id, "channel_id", None)
                if channel_id is None:
                    return ""
                
                comments = []
                async for comment in client.iter_messages(
                    channel_id, reply_to=main_post.id, reverse=True
                ):
                    comments.append(comment)
                
                if not comments:
                    return "\n\n*No comments.*\n"

                md = ""
                # Собираем все комментарии с медиа
                comment_media_info = []
                for comment in comments:
                    sender = comment.sender
                    sender_name = self._get_sender_name(sender)
                    comment_text = (
                        comment.text.replace("\n", "\n> ") if comment.text else ""
                    )
                    md += f"\n> **{sender_name}** ({comment.date.strftime('%Y-%m-%d %H:%M')}):\n"
                    if comment_text:
                        md += f"> {comment_text}\n"
                    if (
                        comment.media
                        and media_processor is not None
                        and entity_id is not None
                    ):
                        media_obj = (
                            comment.media.photo
                            if hasattr(comment.media, "photo")
                            else getattr(comment.media, "document", None)
                        )
                        if media_obj:
                            media_type = self.get_media_type(comment)
                            type_subdir = (
                                media_dir / f"{media_type}s"
                                if media_type != "unknown"
                                else media_dir
                            )
                            ensure_dir_exists(type_subdir)
                            filename = (
                                media_processor._get_filename(
                                    media_obj, comment.id, media_type, entity_id
                                )
                                if hasattr(media_processor, "_get_filename")
                                else None
                            )
                            comment_media_info.append(
                                {
                                    "comment": comment,
                                    "media_type": media_type,
                                    "type_subdir": type_subdir,
                                    "filename": filename,
                                }
                            )
                        elif hasattr(comment.media, "webpage") and hasattr(
                            comment.media.webpage, "url"
                        ):
                            md += f"> {comment.media.webpage.url}\n"
                    elif comment.media:
                        if hasattr(comment.media, "webpage") and hasattr(
                            comment.media.webpage, "url"
                        ):
                            md += f"> {comment.media.webpage.url}\n"
                        # Add to list for download only if it's not a webpage we've handled
                        elif not hasattr(comment.media, "webpage"):
                            comment_media_info.append(
                                {
                                    "comment": comment,
                                    "media_type": "unknown",
                                    "type_subdir": media_dir,
                                    "filename": None,
                                }
                            )

                # Прогресс-бар для скачивания медиа из комментариев
                if comment_media_info and progress is not None:
                    tasks = []
                for info in comment_media_info:
                    comment = info["comment"]
                    filename = info["filename"] or f"comment_media_{comment.id}"
                    media_type = info["media_type"]
                    type_subdir = info["type_subdir"]
                    file_obj = getattr(comment, "file", None)
                    total_size = (
                        file_obj.size if file_obj and hasattr(file_obj, "size") else 0
                    )
                    task_id = progress.add_task(
                        "download", filename=filename, total=total_size
                    )
                    tasks.append((comment, type_subdir, filename, media_type, task_id))
                # Скачиваем медиа асинхронно с прогрессом
                results = await asyncio.gather(
                    *[
                        t[0].download_media(file=t[1] / t[2] if t[2] else t[1])
                        for t in tasks
                    ]
                )
                # Добавляем ссылки на медиа в markdown
                for idx, info in enumerate(comment_media_info):
                    file_path = results[idx]
                    if file_path:
                        file_name = Path(file_path).name
                        media_type = info["media_type"]
                        relative_media_path = (
                            f"media/{media_type}s/{file_name}"
                            if media_type != "unknown"
                            else f"media/{file_name}"
                        )
                        md += f"> ![[{relative_media_path}]]\n"
                return md
            
            except FloodWaitError as e:
                # Handle FloodWait with retry
                retry_count += 1
                wait_time = e.seconds
                logger.warning(
                    f"⏳ FloodWait detected while exporting comments for post {main_post.id}: need to wait {wait_time}s (attempt {retry_count}/{max_retries})"
                )
                if retry_count < max_retries:
                    logger.info(f"⏱️  Waiting {wait_time + 1} seconds before retry...")
                    await asyncio.sleep(wait_time + 1)
                    # Continue to next retry iteration
                else:
                    logger.error(
                        f"❌ Max retries reached for comments export on post {main_post.id}, aborting comments"
                    )
                    last_error = e
                    break
            
            except Exception as e:
                # Other errors
                if "The message ID used in the peer was invalid" in str(e):
                    return ""
                logger.error(f"Failed to export comments for post {main_post.id}: {e}")
                return ""
        
        # If all retries failed due to FloodWait, return empty
        if last_error:
            logger.warning(f"Comments export failed for post {main_post.id} after {max_retries} retries")
            return ""
        
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
        if hasattr(sender, "title"):
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

    async def _prepare_note_path(
        self, message: Message, entity_id: Union[str, int], entity_export_path: Path
    ) -> Optional[Path]:
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
            message_text = getattr(message, "text", "") or ""
            first_line = message_text.split("\n", 1)[0]
            sanitized_title = sanitize_filename(first_line, 60)
            message_date = getattr(message, "date", datetime.now())
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
            logger.error(
                f"Failed to prepare note path for message in entity {entity_id}: {e}",
                exc_info=True,
            )
            return None

    async def _generate_note_content(
        self, message: Message, media_paths: List[Path], note_path: Path
    ) -> str:
        """
        Generate the content for a note.

        Args:
            message (Message): Telegram message object.
            media_paths (List[Path]): List of media file paths.
            note_path (Path): Path to the note file.

        Returns:
            str: Generated note content.
        """
        message_text = getattr(message, "text", "") or ""
        content = message_text.strip() + "\n\n" if message_text else ""
        if media_paths:
            media_links = await self._generate_media_links(media_paths, note_path)
            if media_links:
                content += "\n".join(media_links) + "\n\n"
        return content.strip()

    async def _generate_media_links(
        self, media_paths: List[Path], note_path: Path
    ) -> List[str]:
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

        async with self.connection_manager.io_semaphore, self.file_locks[note_path]:
            try:
                async with aiofiles.open(note_path, "w", encoding="utf-8") as f:
                    await f.write(content.strip() + "\n")
                return note_path
            except Exception as e:
                logger.error(
                    f"Failed to write note file {note_path}: {e}", exc_info=True
                )
                return None

    # --- Methods from ReplyLinker ---

    async def link_replies(self, entity_id: str, entity_export_path: Path):
        logger.info(f"[{entity_id}] Starting reply linking process...")
        processed_messages = await self.cache_manager.get_all_processed_messages_async(
            entity_id
        )
        if not processed_messages:
            return

        links_to_add = []
        for child_id, msg_data in processed_messages.items():
            parent_id = msg_data.get("reply_to")
            child_filename = msg_data.get("filename")
            if child_filename and parent_id:
                parent_data = processed_messages.get(str(parent_id))
                if parent_data and parent_data.get("filename"):
                    links_to_add.append((parent_data["filename"], child_filename))

        await self._process_reply_links(links_to_add, entity_export_path, entity_id)

    async def _process_reply_links(
        self, links: List[Tuple[str, str]], path: Path, id: str
    ):
        tasks = [self._link_parent_to_child(p, c, path, id) for p, c in links]
        await asyncio.gather(*tasks)

    async def _link_parent_to_child(
        self, p_fname: str, c_fname: str, path: Path, id: str
    ):
        p_path = await self._find_note_path(path, p_fname)
        c_path = await self._find_note_path(path, c_fname)
        if p_path and c_path:
            rel_path = get_relative_path(c_path, p_path.parent)
            if rel_path:
                link = f"Reply to: [[{urllib.parse.unquote(rel_path.replace('.md', ''))}]]\n"
                await self._add_line_to_file(p_path, link, "Reply to:", id)

    async def _find_note_path(self, base_path: Path, filename: str) -> Optional[Path]:
        note_path = base_path / filename
        if note_path.exists():
            return note_path
        try:
            year = filename.split("-")[0]
            if year.isdigit() and len(year) == 4:
                year_path = base_path / year / filename
                if year_path.exists():
                    return year_path
        except Exception:
            pass
        return None

    async def _add_line_to_file(self, file_path: Path, line: str, check: str, id: str):
        if file_path not in self.file_locks:
            self.file_locks[file_path] = asyncio.Lock()
        async with self.connection_manager.io_semaphore, self.file_locks[file_path]:
            try:
                async with aiofiles.open(file_path, "r+", encoding="utf-8") as f:
                    content = await f.read()
                    if not content.lstrip().startswith(check):
                        await f.seek(0)
                        await f.write(line + content)
                        await f.truncate()
            except Exception as e:
                logger.warning(f"[{id}] Failed to update {file_path}: {e}")

    # --- Methods from ForumManager ---

    def get_forum_export_path(self, base_path: Path, entity_name: str) -> Path:
        safe_entity_name = sanitize_filename(entity_name)
        forum_path = base_path / f"Forum_{safe_entity_name}"
        forum_path.mkdir(parents=True, exist_ok=True)
        (forum_path / "media").mkdir(exist_ok=True)
        return forum_path

    def get_topic_note_path(self, forum_path: Path, topic_info) -> Path:
        emoji = topic_info.icon_emoji if topic_info.icon_emoji else "📝"
        safe_title = sanitize_filename(topic_info.title)
        filename = f"{emoji} {safe_title} (Topic_{topic_info.topic_id}).md"
        return forum_path / filename

    def get_topic_media_path(self, forum_path: Path, topic_info) -> Path:
        media_path = forum_path / "media" / f"topic_{topic_info.topic_id}"
        media_path.mkdir(parents=True, exist_ok=True)
        (media_path / "images").mkdir(exist_ok=True)
        (media_path / "videos").mkdir(exist_ok=True)
        (media_path / "documents").mkdir(exist_ok=True)
        (media_path / "audios").mkdir(exist_ok=True)
        return media_path

    async def create_topic_note_header(
        self, topic_info, forum_name: str, message_count: int = 0
    ) -> str:
        emoji = topic_info.icon_emoji if topic_info.icon_emoji else "📝"
        status_icons = []
        if topic_info.is_pinned:
            status_icons.append("📌")
        if topic_info.is_closed:
            status_icons.append("🔒")

        header = f"""# {emoji} {topic_info.title}

📱 Экспорт из Telegram
🏛️ Форум: {forum_name}

"""
        return header

    async def append_message_to_topic_note(self, note_path: Path, message_content: str):
        try:
            if note_path.exists():
                async with aiofiles.open(note_path, "a", encoding="utf-8") as f:
                    await f.write(f"\n{message_content}\n")
            else:
                async with aiofiles.open(note_path, "w", encoding="utf-8") as f:
                    await f.write(f"{message_content}\n")
        except Exception as e:
            logger.error(f"Error appending to topic note {note_path}: {e}")
            raise

    def _format_media_info(self, message: Any, relative_media_path: str) -> str:
        if not message.media:
            return ""
        media_type = type(message.media).__name__
        if hasattr(message.media, "photo"):
            return f"📷 *Photo*\n\n![📸 Изображение]({relative_media_path}/images/photo_{message.id}.jpg)\n"
        elif hasattr(message.media, "document"):
            doc = message.media.document
            if doc.mime_type and doc.mime_type.startswith("video/"):
                return f"📹 *Video*\n\n▶️ [Видео]({relative_media_path}/videos/video_{message.id}.mp4)\n"
            elif doc.mime_type and doc.mime_type.startswith("audio/"):
                if hasattr(doc, "attributes"):
                    for attr in doc.attributes:
                        if hasattr(attr, "voice") and attr.voice:
                            return f"🎵 *Voice message*\n\n🎤 [Голосовое сообщение]({relative_media_path}/audios/voice_{message.id}.ogg)\n"
                return f"🎵 *Audio*\n\n🎶 [Аудио файл]({relative_media_path}/audios/audio_{message.id}.ogg)\n"
            else:
                filename = getattr(doc, "file_name", f"document_{message.id}")
                file_size = getattr(doc, "size", 0)
                size_str = (
                    f" ({self._format_file_size(file_size)})" if file_size > 0 else ""
                )
                return f"📎 *Document*: `{filename}`{size_str}\n\n📄 [Скачать документ]({relative_media_path}/documents/{filename})\n"
        elif hasattr(message.media, "webpage"):
            webpage = message.media.webpage
            title = getattr(webpage, "title", "Ссылка")
            return f"🔗 *Link*: **{title}**\n\n[🌐 Открыть ссылку]({webpage.url})\n"
        return f"📎 *{media_type}*\n"

    def _format_file_size(self, size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    async def format_message_content(
        self,
        message: Message,
        media_processor: Any = None,
        relative_media_path: str = "_media",
        is_pinned: bool = False,
        topic_title: str = "",
    ) -> str:
        try:
            msg_time = message.date.strftime("%H:%M") if message.date else "??:??"
            msg_date = message.date.strftime("%d.%m.%Y") if message.date else "Unknown"
            sender_info = "👤 Unknown"
            if hasattr(message, "sender") and message.sender:
                first_name = getattr(message.sender, "first_name", "")
                last_name = getattr(message.sender, "last_name", "")
                if first_name:
                    sender_name = first_name + (f" {last_name}" if last_name else "")
                    sender_info = f"👤 {sender_name}"
                elif getattr(message.sender, "username", ""):
                    sender_info = f"👤 {message.sender.username}"
                elif hasattr(message.sender, "id"):
                    sender_info = f"👤 User {message.sender.id}"

            reply_info = ""
            if (
                hasattr(message, "reply_to")
                and message.reply_to
                and (reply_to_id := getattr(message.reply_to, "reply_to_msg_id", None))
            ):
                reply_info = f"💬 Ответ на сообщение: {reply_to_id}  \n"

            message_header = f"\n---\n###### Сообщение {message.id}\n📅 {msg_date} | {msg_time}  \n{sender_info}  \n{reply_info}\n"

            message_text = ""
            if hasattr(message, "message") and message.message:
                text = message.message
                text = text.replace("\\", "\\\\")  # Escape backslashes
                text = text.replace("*", "\\*")  # Escape asterisks
                text = text.replace("_", "\\_")  # Escape underscores
                text = text.replace("[", "\\[")  # Escape open brackets
                text = text.replace("]", "\\]")  # Escape close brackets
                text = text.replace("`", "\\`")  # Escape backticks
                newline = "\n"
                message_text = f"> {text.replace(newline, newline + '> ')}\n\n"

            media_content = ""
            if hasattr(message, "media") and message.media:
                media_info = self._format_media_info(message, relative_media_path)
                if media_info:
                    media_content = f"**📎 Медиа:**\n{media_info}\n\n"

            return message_header + message_text + media_content
        except Exception as e:
            logger.error(
                f"Error formatting message {getattr(message, 'id', 'unknown')}: {e}"
            )
            return f"\n---\n## Сообщение {getattr(message, 'id', 'unknown')}\n**Ошибка форматирования**\n\n"
