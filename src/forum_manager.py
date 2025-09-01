import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import aiofiles
from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    PeerIdInvalidError,
)
from telethon.tl.functions.channels import GetForumTopicsRequest
from telethon.tl.types import (
    Channel,
    Chat,
    ForumTopic,
    Message,
)

from src.config import Config, ExportTarget
from src.retry_manager import TELEGRAM_API_CONFIG, retry_manager
from src.utils import logger, sanitize_filename
class TopicInfo:
    """Информация о топике форума."""

    def __init__(self, topic_id: int, title: str, icon_emoji: str = "",
                 creator_id: Optional[int] = None, created_date: Optional[datetime] = None,
                 message_count: int = 0, is_closed: bool = False, is_pinned: bool = False):
        self.topic_id = topic_id
        self.title = title
        self.icon_emoji = icon_emoji
        self.creator_id = creator_id
        self.created_date = created_date
        self.message_count = message_count
        self.is_closed = is_closed
        self.is_pinned = is_pinned
        self.safe_name = sanitize_filename(f"{title}_{topic_id}")

    def __repr__(self):
        status = []
        if self.is_pinned:
            status.append("📌")
        if self.is_closed:
            status.append("🔒")
        status_str = " ".join(status)
        return f"Topic({self.topic_id}, '{self.title}', {self.message_count} msgs{' ' + status_str if status_str else ''})"
class ForumManager:
    """Менеджер для работы с форумами и топиками в Telegram."""

    def __init__(self, client: TelegramClient, config: Config):
        self.client = client
        self.config = config
        self.topics_cache: Dict[Union[str, int], List[TopicInfo]] = {}
        self.entity_cache: Dict[Union[str, int], Any] = {}

    async def is_forum_chat(self, entity: Any) -> bool:
        """
        Проверяет, является ли чат форумом с топиками.

        Args:
            entity: Telegram entity (Chat или Channel)

        Returns:
            bool: True если это форум
        """
        try:
            logger.info(f"Checking if entity is forum: {type(entity)}")
            logger.info(f"Entity attributes: {[attr for attr in dir(entity) if not attr.startswith('_')]}")

            if isinstance(entity, Channel):
                forum_attr = getattr(entity, 'forum', False)
                logger.info(f"Entity forum attribute: {forum_attr}")
                return forum_attr
            elif isinstance(entity, Chat):
                # Обычные чаты не поддерживают топики
                logger.info("Entity is Chat type - not a forum")
                return False

            logger.info(f"Entity is {type(entity)} - not a known forum type")
            return False
        except Exception as e:
            logger.warning(f"Error checking if entity is forum: {e}")
            return False

    async def get_forum_topics(self, entity: Any, force_refresh: bool = False) -> List[TopicInfo]:
        """
        Получает список всех топиков форума.

        Args:
            entity: Telegram entity форума
            force_refresh: Принудительно обновить кэш

        Returns:
            List[TopicInfo]: Список топиков
        """
        entity_id = str(getattr(entity, 'id', entity))

        if not force_refresh and entity_id in self.topics_cache:
            return self.topics_cache[entity_id]

        topics = []

        try:
            # Используем retry manager для надежности
            async def _fetch_topics():
                result = await self.client(GetForumTopicsRequest(
                    channel=entity,
                    offset_date=None,
                    offset_id=0,
                    offset_topic=0,
                    limit=100
                ))
                return result

            result = await retry_manager.retry_async(
                _fetch_topics,
                f"fetch_forum_topics_{entity_id}",
                TELEGRAM_API_CONFIG
            )

            # Детальная отладка ответа API
            logger.info(f"API Response type: {type(result)}")
            logger.info(f"API Response attributes: {[attr for attr in dir(result) if not attr.startswith('_')]}")

            if hasattr(result, 'topics'):
                logger.info(f"Found {len(result.topics)} topics in API response")

                for i, topic in enumerate(result.topics):
                    # Проверяем, что это правильный тип ForumTopic
                    if not isinstance(topic, ForumTopic):
                        logger.warning(f"Topic {i+1} is not ForumTopic type: {type(topic)}")
                        continue

                    logger.info(f"\n--- Topic {i+1} Debug Info ---")
                    logger.info(f"Topic type: {type(topic)}")

                    # Извлекаем основные данные
                    topic_id = topic.id
                    title = topic.title

                    logger.info(f"Processing topic: {title} (ID: {topic_id})")

                    # Получаем дополнительные атрибуты
                    icon_emoji_id = getattr(topic, 'icon_emoji_id', None)
                    icon_color = getattr(topic, 'icon_color', None)

                    # Определяем эмодзи для топика
                    if icon_emoji_id:
                        icon_emoji = "💬"  # Пока используем дефолтную, нужно отдельно получать эмодзи по ID
                    else:
                        icon_emoji = "💬"

                    # Получаем статусы
                    is_closed = getattr(topic, 'closed', False)
                    is_pinned = getattr(topic, 'pinned', False)

                    # Получаем количество сообщений
                    message_count = await self._get_topic_message_count_via_api(entity, topic_id)

                    # Дата создания
                    created_date = getattr(topic, 'date', None)

                    logger.info(f"Extracted: ID={topic_id}, Title='{title}', Messages={message_count}, Closed={is_closed}, Pinned={is_pinned}")

                    if message_count == 0:
                        message_count = await self.get_topic_message_count(entity, topic_id)

                    # Создаем TopicInfo
                    topic_info = TopicInfo(
                        topic_id=topic_id,
                        title=title,
                        icon_emoji=icon_emoji,
                        created_date=created_date,
                        is_closed=is_closed,
                        is_pinned=is_pinned,
                        message_count=message_count
                    )
                    topics.append(topic_info)

                    logger.info(f"Created TopicInfo: {topic_info}")
            else:
                logger.error("API result has no 'topics' attribute")

            # Кэшируем результат
            self.topics_cache[entity_id] = topics

            logger.info(f"Found {len(topics)} topics in forum {entity_id}")

        except FloodWaitError as e:
            logger.warning(f"Rate limited when fetching topics: {e.seconds}s wait")
            await asyncio.sleep(e.seconds + 1)
            return await self.get_forum_topics(entity, force_refresh=True)
        except (ChannelPrivateError, ChatAdminRequiredError, PeerIdInvalidError) as e:
            logger.error(f"Access denied to forum {entity_id}: {type(e).__name__}")
            return []
        except Exception as e:
            logger.error(f"Error fetching forum topics for {entity_id}: {e}")
            return []

        return topics

    async def _get_topic_message_count_via_api(self, entity: Any, topic_id: int) -> int:
        """
        Получает количество сообщений в топике.

        Args:
            entity: Telegram entity форума
            topic_id: ID топика

        Returns:
            int: Количество сообщений в топике
        """
        try:
            result = await self.client.get_messages(
                entity,
                reply_to=topic_id,
                limit=0
            )

            count = getattr(result, 'total', 0)
            if count > 0:
                count -= 1

            return count

        except Exception as e:
            logger.debug(f"Failed to get message count for topic {topic_id}: {e}")
            count = 0
            try:
                async for message in self.client.iter_messages(entity, reply_to=topic_id, limit=100):
                    if message.id != topic_id:
                        count += 1
                return count
            except Exception as e2:
                logger.error(f"Error counting messages for topic {topic_id}: {e2}")
                return 0

    async def get_topic_message_count(self, entity: Any, topic_id: int) -> int:
        """
        Получает количество сообщений в топике.

        Args:
            entity: Telegram entity форума
            topic_id: ID топика

        Returns:
            int: Количество сообщений в топике
        """
        try:
            result = await self.client.get_messages(
                entity,
                reply_to=topic_id,
                limit=0
            )

            count = getattr(result, 'total', 0)
            if count > 0:
                count -= 1

            return count

        except Exception as e:
            logger.error(f"Error getting message count for topic {topic_id}: {e}")
            return 0

    async def get_topic_messages(self, entity: Any, topic_id: int,
                                limit: Optional[int] = None,
                                min_id: Optional[int] = None) -> List[Message]:
        """
        Получает сообщения из конкретного топика.

        Args:
            entity: Telegram entity форума
            topic_id: ID топика
            limit: Максимальное количество сообщений
            min_id: Минимальный ID сообщения (для продолжения экспорта)

        Returns:
            List[Message]: Список сообщений топика
        """
        messages = []
        entity_id = str(getattr(entity, 'id', entity))

        try:
            logger.info(f"Fetching messages from topic {topic_id} in forum {entity_id}")

            async for message in self.client.iter_messages(
                entity=entity,
                reply_to=topic_id,
                limit=limit,
                min_id=min_id or 0,
                reverse=True
            ):
                if message.id != topic_id:
                    messages.append(message)

            logger.info(f"Retrieved {len(messages)} messages from topic {topic_id}")

        except FloodWaitError as e:
            logger.warning(f"Rate limited when fetching topic messages: {e.seconds}s wait")
            await asyncio.sleep(e.seconds + 1)
            return await self.get_topic_messages(entity, topic_id, limit, min_id)
        except Exception as e:
            logger.error(f"Error fetching messages from topic {topic_id}: {e}")

        return messages

    def _message_belongs_to_topic(self, message: Message, topic_id: int) -> bool:
        """
        Проверяет принадлежность сообщения к топику.

        Args:
            message: Сообщение для проверки
            topic_id: ID топика

        Returns:
            bool: True если сообщение принадлежит топику
        """
        try:
            if not message or not hasattr(message, 'id'):
                return False

            if message.id == topic_id:
                return True

            if hasattr(message, 'reply_to') and message.reply_to is not None:
                top_id = getattr(message.reply_to, 'reply_to_top_id', None)
                if top_id == topic_id:
                    return True

                reply_msg_id = getattr(message.reply_to, 'reply_to_msg_id', None)
                if reply_msg_id == topic_id:
                    return True

            return False

        except Exception:
            return False

    async def create_export_targets_from_forum(self, entity: Any,
                                              topic_filter: Optional[List[int]] = None) -> List[ExportTarget]:
        """
        Создает цели экспорта для топиков форума.

        Args:
            entity: Telegram entity форума
            topic_filter: Список ID топиков для экспорта (None = все)

        Returns:
            List[ExportTarget]: Список целей экспорта для каждого топика
        """
        entity_id = str(getattr(entity, 'id', entity))
        entity_name = getattr(entity, 'title', f"Forum_{entity_id}")

        topics = await self.get_forum_topics(entity)
        if not topics:
            logger.warning(f"No topics found in forum {entity_id}")
            return []

        targets = []

        for topic in topics:
            # Фильтруем топики если указан фильтр
            if topic_filter and topic.topic_id not in topic_filter:
                continue

            # Пропускаем закрытые топики если не указано иное
            if topic.is_closed and not self.config.export_closed_topics:
                logger.info(f"Skipping closed topic: {topic.title}")
                continue

            target = ExportTarget(
                id=entity_id,
                name=f"{entity_name} > {topic.title}",
                type="forum_topic",
                is_forum=True,
                topic_id=topic.topic_id,
                export_all_topics=False,
                estimated_messages=topic.message_count
            )
            targets.append(target)

        logger.info(f"Created {len(targets)} export targets for forum topics")
        return targets

    async def detect_topic_from_url(self, url: str) -> Optional[Tuple[str, int]]:
        """
        Извлекает chat_id и topic_id из URL топика.

        Args:
            url: URL в формате https://t.me/c/chat_id/topic_id

        Returns:
            Optional[Tuple[str, int]]: (chat_id, topic_id) или None
        """
        # Регулярное выражение для парсинга URL топика
        topic_pattern = r't\.me/c/(\d+)/(\d+)'
        match = re.search(topic_pattern, url)

        if match:
            chat_id = f"-100{match.group(1)}"  # Преобразуем в полный chat_id
            topic_id = int(match.group(2))
            return chat_id, topic_id

        return None

    def get_forum_export_path(self, base_path: Path, entity_name: str) -> Path:
        """
        Создает путь для экспорта форума.

        Args:
            base_path: Базовый путь экспорта
            entity_name: Имя форума

        Returns:
            Path: Путь для экспорта форума
        """
        safe_entity_name = sanitize_filename(entity_name)
        forum_path = base_path / f"Forum_{safe_entity_name}"

        # Создаем структуру папок
        forum_path.mkdir(parents=True, exist_ok=True)
        (forum_path / "media").mkdir(exist_ok=True)

        return forum_path

    def get_topic_note_path(self, forum_path: Path, topic_info: TopicInfo) -> Path:
        """
        Создает путь для заметки топика.

        Args:
            forum_path: Путь к папке форума
            topic_info: Информация о топике

        Returns:
            Path: Путь к файлу заметки топика
        """
        # Создаем имя файла с эмодзи и безопасным названием
        emoji = topic_info.icon_emoji if topic_info.icon_emoji else "📝"
        safe_title = sanitize_filename(topic_info.title)
        filename = f"{emoji} {safe_title} (Topic_{topic_info.topic_id}).md"

        return forum_path / filename

    def get_topic_media_path(self, forum_path: Path, topic_info: TopicInfo) -> Path:
        """
        Создает путь для медиа файлов топика.

        Args:
            forum_path: Путь к папке форума
            topic_info: Информация о топике

        Returns:
            Path: Путь к папке медиа топика
        """
        media_path = forum_path / "media" / f"topic_{topic_info.topic_id}"
        media_path.mkdir(parents=True, exist_ok=True)
        (media_path / "images").mkdir(exist_ok=True)
        (media_path / "videos").mkdir(exist_ok=True)
        (media_path / "documents").mkdir(exist_ok=True)
        (media_path / "audio").mkdir(exist_ok=True)

        return media_path

    async def create_topic_note_header(self, topic_info: TopicInfo, forum_name: str,
                                      message_count: int = 0) -> str:
        """
        Создает заголовок для заметки топика.

        Args:
            topic_info: Информация о топике
            forum_name: Название форума
            message_count: Количество экспортированных сообщений

        Returns:
            str: Заголовок заметки в формате markdown
        """
        emoji = topic_info.icon_emoji if topic_info.icon_emoji else "📝"
        status_icons = []

        if topic_info.is_pinned:
            status_icons.append("📌")
        if topic_info.is_closed:
            status_icons.append("🔒")

        status_str = " ".join(status_icons)

        header = f"""# {emoji} {topic_info.title}

> **Forum:** {forum_name}
> **Topic ID:** {topic_info.topic_id}
> **Status:** {'🔒 Closed' if topic_info.is_closed else '🔓 Open'} {status_str}
> **Messages:** {message_count}
> **Created:** {topic_info.created_date.strftime('%Y-%m-%d %H:%M:%S') if topic_info.created_date else 'Unknown'}
> **Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

"""
        return header

    async def append_message_to_topic_note(self, note_path: Path, message_content: str):
        """
        Добавляет сообщение к заметке топика.

        Args:
            note_path: Путь к файлу заметки
            message_content: Содержимое сообщения в markdown
        """
        try:
            # Проверяем, существует ли файл
            if note_path.exists():
                # Добавляем к существующему файлу
                async with aiofiles.open(note_path, 'a', encoding='utf-8') as f:
                    await f.write(f"\n{message_content}\n")
            else:
                # Создаем новый файл (заголовок должен быть добавлен отдельно)
                async with aiofiles.open(note_path, 'w', encoding='utf-8') as f:
                    await f.write(f"{message_content}\n")

        except Exception as e:
            logger.error(f"Error appending to topic note {note_path}: {e}")
            raise

    async def append_message_to_topic_note(self, topic_file: Path, message: any,
                                          media_processor: any = None,
                                          relative_media_path: str = "_media",
                                          is_pinned: bool = False) -> bool:
        """
        Добавляет сообщение в заметку топика в стиле Telegram.

        Args:
            topic_file: Путь к файлу заметки топика
            message: Telegram сообщение
            media_processor: Процессор медиа (опционально)
            relative_media_path: Относительный путь к медиа
            is_pinned: Является ли сообщение закрепленным

        Returns:
            bool: True если сообщение было добавлено
        """
        try:
            # Форматируем время
            msg_time = message.date.strftime('%H:%M') if message.date else "??:??"
            msg_date = message.date.strftime('%d.%m.%Y') if message.date else "Unknown"

            # Получаем информацию об отправителе
            sender_name = "Unknown"
            sender_display = "👤 Unknown"

            if hasattr(message, 'sender') and message.sender:
                first_name = getattr(message.sender, 'first_name', '')
                last_name = getattr(message.sender, 'last_name', '')
                username = getattr(message.sender, 'username', '')

                if first_name:
                    sender_name = first_name
                    if last_name:
                        sender_name += f" {last_name}"
                elif username:
                    sender_name = username
                else:
                    sender_name = f"User_{message.sender.id}"

                # Добавляем эмодзи для визуального различия
                sender_display = f"👤 **{sender_name}**"

            # Обрабатываем ответы на сообщения
            reply_block = ""
            if hasattr(message, 'reply_to') and message.reply_to:
                reply_block = """
> 💬 *Ответ на сообщение*
>
"""

            # Формируем основной контент
            content_parts = []

            # Обрабатываем медиа
            if message.media:
                media_info = self._format_media_info(message, relative_media_path)
                if media_info:
                    content_parts.append(media_info)

            # Основной текст
            if message.text:
                # Форматируем текст с учетом длинных сообщений
                text_content = message.text
                if len(text_content) > 200:
                    # Добавляем отступы для длинных сообщений
                    text_content = f"```\n{text_content}\n```"
                content_parts.append(text_content)

            # Определяем тип сообщения для иконки
            msg_icon = "📌" if is_pinned else "💬"

            # Формируем блок сообщения в стиле Telegram
            if is_pinned:
                message_block = f"""
## {msg_icon} Закрепленное сообщение

**{sender_name}** • {msg_time}

{reply_block}{"".join(content_parts) if content_parts else "*[Сообщение без текста]*"}

---

"""
            else:
                message_block = f"""
{sender_display} • `{msg_time}`

{reply_block}{"".join(content_parts) if content_parts else "*[Сообщение без текста]*"}

---

"""

            # Добавляем в файл
            with open(topic_file, 'a', encoding='utf-8') as f:
                f.write(message_block)

            return True

        except Exception as e:
            logger.error(f"Error appending message to topic note: {e}")
            return False

    def _format_media_info(self, message: any, relative_media_path: str) -> str:
        """Форматирует информацию о медиа в стиле Telegram UI."""
        if not message.media:
            return ""

        media_type = type(message.media).__name__

        if hasattr(message.media, 'photo'):
            return f"📷 *Photo*\n\n![📸 Изображение]({relative_media_path}/images/photo_{message.id}.jpg)\n"
        elif hasattr(message.media, 'document'):
            doc = message.media.document
            if doc.mime_type and doc.mime_type.startswith('video/'):
                return f"📹 *Video*\n\n▶️ [Видео]({relative_media_path}/videos/video_{message.id}.mp4)\n"
            elif doc.mime_type and doc.mime_type.startswith('audio/'):
                if hasattr(doc, 'attributes'):
                    # Проверяем, является ли это голосовым сообщением
                    for attr in doc.attributes:
                        if hasattr(attr, 'voice') and attr.voice:
                            return f"🎵 *Voice message*\n\n🎤 [Голосовое сообщение]({relative_media_path}/audio/voice_{message.id}.ogg)\n"
                return f"🎵 *Audio*\n\n🎶 [Аудио файл]({relative_media_path}/audio/audio_{message.id}.ogg)\n"
            else:
                filename = getattr(doc, 'file_name', f'document_{message.id}')
                file_size = getattr(doc, 'size', 0)
                size_str = f" ({self._format_file_size(file_size)})" if file_size > 0 else ""
                return f"📎 *Document*: `{filename}`{size_str}\n\n📄 [Скачать документ]({relative_media_path}/documents/{filename})\n"
        elif hasattr(message.media, 'webpage'):
            webpage = message.media.webpage
            title = getattr(webpage, 'title', 'Ссылка')
            return f"🔗 *Link*: **{title}**\n\n[🌐 Открыть ссылку]({webpage.url})\n"

        return f"📎 *{media_type}*\n"

    def _format_file_size(self, size_bytes: int) -> str:
        """Форматирует размер файла в человекочитаемом виде."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    async def create_topic_note(self, topic_info: TopicInfo, forum_name: str,
                               note_path: Path, member_count: int = 0) -> Path:
        """
        Создает основную заметку для топика в стиле Telegram.

        Args:
            topic_info: Информация о топике
            forum_name: Название форума
            note_path: Путь для создания заметки
            member_count: Количество участников

        Returns:
            Path: Путь к созданной заметке
        """
        try:
            # Создаем заголовок заметки в стиле Telegram
            icon = topic_info.icon_emoji or "💬"
            created_date = topic_info.created_date.strftime('%d.%m.%Y') if topic_info.created_date else "Unknown"

            # Формируем статус топика
            status_icons = []
            if topic_info.is_pinned:
                status_icons.append("📌")
            if topic_info.is_closed:
                status_icons.append("🔒")

            status_text = " ".join(status_icons) + " " if status_icons else ""

            # Информация о участниках
            member_text = f"{member_count} members" if member_count > 0 else "участники"

            header = f"""# {status_text}{icon} {topic_info.title}

> 📱 **Экспорт из Telegram**
> 🏠 Форум: **{forum_name}**
> 👥 {member_text}
> 📅 Создан: {created_date}
> 🆔 Topic ID: `{topic_info.topic_id}`

---

"""

            # Создаем файл
            with open(note_path, 'w', encoding='utf-8') as f:
                f.write(header)

            logger.info(f"Created topic note: {note_path}")
            return note_path

        except Exception as e:
            logger.error(f"Error creating topic note: {e}")
            raise

    async def get_topic_statistics(self, entity: Any) -> Dict[str, Any]:
        """
        Получает статистику по топикам форума.

        Args:
            entity: Telegram entity форума

        Returns:
            Dict[str, Any]: Статистика топиков
        """
        topics = await self.get_forum_topics(entity)

        stats = {
            "total_topics": len(topics),
            "closed_topics": sum(1 for t in topics if t.is_closed),
            "pinned_topics": sum(1 for t in topics if t.is_pinned),
            "estimated_total_messages": sum(t.message_count for t in topics),
            "topics_by_name": {t.title: t.topic_id for t in topics}
        }

        return stats

    def format_topic_info_for_display(self, topics: List[TopicInfo]) -> str:
        """
        Форматирует информацию о топиках для отображения пользователю.

        Args:
            topics: Список топиков

        Returns:
            str: Отформатированная строка
        """
        if not topics:
            return "No topics found in this forum."

        lines = ["Available Forum Topics:"]
        lines.append("=" * 50)

        for i, topic in enumerate(topics, 1):
            status_icons = []
            if topic.is_pinned:
                status_icons.append("📌")
            if topic.is_closed:
                status_icons.append("🔒")

            status_str = " ".join(status_icons)

            # Используем дефолтную эмодзи если icon_emoji пустая или содержит ID
            icon = topic.icon_emoji if topic.icon_emoji and len(topic.icon_emoji) <= 2 else "💬"

            lines.append(f"{i:2d}. {icon} {topic.title} (ID: {topic.topic_id})")
            lines.append(f"     Messages: ~{topic.message_count} {status_str}")

        return "\n".join(lines)

    async def resolve_forum_entity(self, identifier: str) -> Optional[Any]:
        """
        Разрешает идентификатор форума в Telegram entity.

        Args:
            identifier: ID, username или URL форума

        Returns:
            Optional[Any]: Telegram entity или None
        """
        try:
            # Если это URL топика, извлекаем chat_id
            if '/c/' in identifier:
                result = await self.detect_topic_from_url(identifier)
                if result:
                    chat_id, _ = result
                    identifier = chat_id

            # Используем существующую логику разрешения entity
            entity = await self.client.get_entity(identifier)

            # Проверяем, что это действительно форум
            if await self.is_forum_chat(entity):
                return entity
            else:
                logger.warning(f"Entity {identifier} is not a forum chat")
                return None

        except Exception as e:
            logger.error(f"Error resolving forum entity {identifier}: {e}")
            return None

    def clear_cache(self):
        """Очищает кэш топиков."""
        self.topics_cache.clear()
        self.entity_cache.clear()
        logger.info("Forum manager cache cleared")
