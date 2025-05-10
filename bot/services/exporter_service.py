import asyncio
import logging
from typing import List, Dict, Optional, Union
from pathlib import Path

# Импорты из основного проекта telegram-obsidian
# Предполагается, что telegram-obsidian/src находится в PYTHONPATH
# или бот запускается так, что эти импорты работают (например, python -m bot.main из корня)
try:
    from src.config import Config, ExportTarget, load_config
    from src.telegram_client import TelegramManager
    from src.cache_manager import CacheManager
    from src.media_processor import MediaProcessor
    from src.note_generator import NoteGenerator
    from src.reply_linker import ReplyLinker
    from src.exceptions import TelegramConnectionError, ExporterError, ConfigError
    
    # Нам также понадобится функция export_single_target из main.py вашего проекта
    # Это немного не стандартно - импортировать из main, но если она там и доступна...
    # В идеале, export_single_target была бы частью какого-то сервисного модуля в src/
    from main import export_single_target # Попытка импорта из telegram-obsidian/main.py
except ImportError as e:
    logging.critical(f"ExporterService: Failed to import modules from 'src' or 'main.py': {e}. "
                     f"Ensure the bot is run from the project root or PYTHONPATH is set correctly.")
    # Заглушки, чтобы модуль мог быть загружен, но сервис не будет работать
    Config = None
    TelegramManager = None
    # ... и так далее для всех импортируемых классов/функций

logger = logging.getLogger(__name__)

class ExporterService:
    def __init__(self, config: Config, loop: Optional[asyncio.AbstractEventLoop] = None):
        if not all([Config, TelegramManager, CacheManager, MediaProcessor, NoteGenerator, ReplyLinker, export_single_target]):
            raise ImportError("One or more required components for ExporterService could not be imported.")

        self.config: Config = config
        self.loop = loop if loop else asyncio.get_event_loop()
        
        # Инициализация компонентов, как в run_export
        self.cache_manager = CacheManager(self.config.cache_file)
        self.telegram_manager = TelegramManager(self.config) # TelegramManager из telegram_client.py
        self.media_processor = MediaProcessor(self.config, self.telegram_manager.get_client())
        self.note_generator = NoteGenerator(self.config)
        self.reply_linker = ReplyLinker(self.config, self.cache_manager)
        
        self.is_initialized = False

    async def initialize(self):
        """
        Асинхронная инициализация компонентов, требующих await (загрузка кэша, подключение).
        """
        if self.is_initialized:
            return
        try:
            logger.info("ExporterService: Initializing components...")
            await self.cache_manager.load_cache()
            logger.info("ExporterService: Cache loaded.")
            
            logger.info("ExporterService: Connecting TelegramManager...")
            await self.telegram_manager.connect() # connect из TelegramManager
            logger.info("ExporterService: TelegramManager connected.")
            
            # Обновим media_processor клиентом после его подключения, если это необходимо
            # В текущей реализации MediaProcessor получает клиент при __init__,
            # и TelegramManager создает клиент в своем __init__. connect() его только запускает.
            # Так что дополнительное обновление тут может и не нужно.
            # self.media_processor.client = self.telegram_manager.get_client()

            self.is_initialized = True
            logger.info("ExporterService: Initialization complete.")
        except Exception as e:
            logger.error(f"ExporterService: Failed to initialize: {e}", exc_info=True)
            self.is_initialized = False # Сбрасываем флаг при ошибке
            raise # Передаем исключение дальше, чтобы вызывающий код знал об ошибке

    async def get_recent_dialogs(self, limit: int = 20) -> List[Dict]:
        if not self.is_initialized:
            await self.initialize()
            if not self.is_initialized: # Если инициализация все равно не удалась
                 logger.error("ExporterService: Cannot get recent dialogs, service not initialized.")
                 return []
        
        dialogs_data: List[Dict] = []
        try:
            # Используем get_dialogs() напрямую из Telethon клиента
            # _list_and_select_dialogs в TelegramManager больше для интерактивного UI
            telethon_client = self.telegram_manager.get_client()
            if not telethon_client:
                logger.error("ExporterService: Telethon client not available in TelegramManager.")
                return []

            dialogs = await telethon_client.get_dialogs(limit=limit)
            for dialog in dialogs:
                entity = dialog.entity
                dialog_type = "unknown"
                if dialog.is_user:
                    dialog_type = "user"
                elif dialog.is_group:
                    dialog_type = "group"
                elif dialog.is_channel:
                    dialog_type = "channel"
                
                # Получаем title, username или id
                title = getattr(entity, 'title', None)
                if not title:
                    title = getattr(entity, 'username', None)
                if not title:
                    first_name = getattr(entity, 'first_name', '')
                    last_name = getattr(entity, 'last_name', '')
                    title = f"{first_name} {last_name}".strip() if first_name or last_name else f"ID: {entity.id}"

                dialogs_data.append({
                    'id': entity.id,
                    'title': title or f"Unnamed Dialog (ID: {entity.id})",
                    'type': dialog_type
                })
            logger.info(f"ExporterService: Fetched {len(dialogs_data)} recent dialogs.")
        except Exception as e:
            logger.error(f"ExporterService: Error fetching recent dialogs: {e}", exc_info=True)
        return dialogs_data

    async def trigger_export_for_target(
        self, 
        target_id: Union[str, int], 
        target_name: Optional[str] = None, # Имя может быть известно из списка диалогов
        target_type: Optional[str] = "unknown" # 'channel', 'chat', 'user'
    ) -> Optional[str]:
        if not self.is_initialized:
            await self.initialize()
            if not self.is_initialized:
                 logger.error(f"ExporterService: Cannot trigger export for {target_id}, service not initialized.")
                 return None

        try:
            # Убедимся, что target_id это int, если это возможно, т.к. ExportTarget ожидает int
            try:
                processed_target_id = int(target_id)
            except ValueError:
                # Если это username, resolve_entity его обработает, но ExportTarget хочет int.
                # Попробуем разрешить его сначала, чтобы получить чистый ID.
                logger.info(f"ExporterService: Target ID '{target_id}' is not int, attempting to resolve...")
                entity = await self.telegram_manager.resolve_entity(str(target_id))
                if not entity:
                    logger.error(f"ExporterService: Could not resolve entity for target '{target_id}'.")
                    return None
                processed_target_id = entity.id
                if not target_name: # Если имя не передано, берем из resolved entity
                    target_name = getattr(entity, 'title', getattr(entity, 'username', str(processed_target_id)))
                if target_type == "unknown": # Уточняем тип
                    if hasattr(entity, 'broadcast') and entity.broadcast: target_type = "channel"
                    elif hasattr(entity, 'megagroup') and entity.megagroup: target_type = "group" # или chat
                    elif hasattr(entity, 'username'): target_type = "user"


            # Создаем ExportTarget
            # ExportTarget(id=int, type=str, name=Optional[str], members=Optional[List[str]])
            export_target = ExportTarget(
                id=processed_target_id, 
                type=target_type if target_type else "unknown", # Нужен тип для ExportTarget
                name=target_name if target_name else str(processed_target_id)
            )
            logger.info(f"ExporterService: Created ExportTarget: id={export_target.id}, type='{export_target.type}', name='{export_target.name}'")

            # Путь, куда будут сохраняться файлы экспорта
            # get_export_path_for_entity принимает ID, а не объект ExportTarget
            entity_export_path = self.config.get_export_path_for_entity(export_target.id)
            entity_export_path.mkdir(parents=True, exist_ok=True) # Убедимся, что директория существует
            logger.info(f"ExporterService: Export path for target {export_target.id}: {entity_export_path}")
            
            # Запускаем экспорт для этой конкретной цели
            # export_single_target определен в telegram-obsidian/main.py
            logger.info(f"ExporterService: Calling export_single_target for '{export_target.name}' (ID: {export_target.id})...")
            await export_single_target(
                target=export_target,
                config=self.config,
                telegram_manager=self.telegram_manager,
                cache_manager=self.cache_manager,
                media_processor=self.media_processor,
                note_generator=self.note_generator,
                reply_linker=self.reply_linker
            )
            logger.info(f"ExporterService: export_single_target for '{export_target.name}' completed.")

            # Проверяем, что директория экспорта не пуста
            if not any(entity_export_path.iterdir()):
                 logger.warning(f"ExporterService: Export path {entity_export_path} is empty after export attempt for {export_target.id}.")
                 # Можно вернуть None или ошибку, или пустой путь если это ожидаемо в каких-то случаях
                 # return None # Возвращаем None, если ничего не экспортировалось

            return str(entity_export_path)

        except (TelegramConnectionError, ExporterError, ConfigError) as e:
            logger.error(f"ExporterService: Known error during export for target {target_id}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"ExporterService: Unexpected error during export for target {target_id}: {e}", exc_info=True)
        
        return None # В случае любой ошибки

    async def close(self):
        """
        Корректно закрывает соединения и сохраняет кэш.
        """
        logger.info("ExporterService: Closing service...")
        if self.telegram_manager and self.telegram_manager.client_connected:
            try:
                logger.info("ExporterService: Disconnecting TelegramManager...")
                await self.telegram_manager.disconnect()
                logger.info("ExporterService: TelegramManager disconnected.")
            except Exception as e:
                logger.error(f"ExporterService: Error during TelegramManager disconnect: {e}", exc_info=True)
        
        if self.cache_manager:
            try:
                logger.info("ExporterService: Saving cache...")
                await self.cache_manager.save_cache()
                logger.info("ExporterService: Cache saved.")
            except Exception as e:
                logger.error(f"ExporterService: Error saving cache: {e}", exc_info=True)
        self.is_initialized = False
        logger.info("ExporterService: Closed.")

# Важно: Этот сервис предполагает, что основной event loop уже запущен (что делает aiogram).
# Инициализация (connect и load_cache) должна быть вызвана асинхронно один раз
# перед первым использованием или лениво при первом вызове метода.