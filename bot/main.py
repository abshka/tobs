import asyncio
import logging
import os
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher

# Добавляем корневой каталог проекта в PYTHONPATH для корректного импорта src/ из основного проекта
sys.path.insert(0, str(Path(__file__).parent.parent.absolute()))

# Импортируем конфигурацию
# Важно, чтобы config.py был доступен в PYTHONPATH или через относительные импорты
try:
    from .config import BOT_TOKEN, ALLOWED_USER_IDS, LOG_FILE
    # Загружаем обработчики (они будут созданы в следующих шагах)
    from .handlers import common_handlers, export_handlers
    # Импортируем ExporterService
    from .services.exporter_service import ExporterService
    # Импортируем load_config из основного проекта
    from src.config import load_config
except ImportError as e:
    # Это может произойти, если запускать main.py напрямую из директории bot/
    # без установки пакета или правильной настройки PYTHONPATH
    print(f"Error: Could not import modules: {e}")
    print("Try running from the 'telegram-obsidian' directory using 'python -m bot.main'")
    # Для отладки можно попробовать абсолютные импорты, если структура позволяет
    # from bot.config import BOT_TOKEN, ALLOWED_USER_IDS, LOG_FILE
    # from bot.handlers import common_handlers, export_handlers
    BOT_TOKEN = None # Предотвращаем падение при импорте, но бот не запустится
    LOG_FILE = "bot_error.log" # Fallback лог
    ALLOWED_USER_IDS = []


# Настройка логирования
# Создаем директорию для логов, если она не существует (на случай если LOG_FILE указывает на поддиректорию)
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Глобальная переменная для ExporterService
exporter_service = None

# Создаем главный роутер для Dispatcher
# В aiogram 3.x рекомендуется использовать Router для организации хэндлеров
dp = Dispatcher()

async def startup_message(bot: Bot):
    global exporter_service
    logger.info("Bot has started polling.")
    
    # Инициализируем ExporterService
    try:
        logger.info("Loading config for the core telegram-obsidian project...")
        obsidian_config = load_config()
        logger.info(f"Config loaded. Export base path: {obsidian_config.export_base_path}")
        
        logger.info("Initializing ExporterService...")
        exporter_service = ExporterService(obsidian_config)
        await exporter_service.initialize()
        logger.info("ExporterService initialized successfully.")
        
        # Регистрируем exporter_service в export_handlers
        export_handlers.register_exporter_service(exporter_service)
        logger.info("ExporterService registered with export_handlers.")
        
        # Можно добавить отправку сообщения админу о старте бота, если нужно
        for admin_id in ALLOWED_USER_IDS:
            try:
                await bot.send_message(admin_id, "Бот успешно запущен и готов к работе!")
            except Exception as e:
                logger.error(f"Could not send startup message to admin {admin_id}: {e}")
    except Exception as e:
        logger.critical(f"Failed to initialize ExporterService: {e}", exc_info=True)
        for admin_id in ALLOWED_USER_IDS:
            try:
                await bot.send_message(admin_id, f"⚠️ Бот запущен с ошибками. ExporterService не инициализирован: {str(e)[:100]}")
            except Exception as e:
                logger.error(f"Could not send error message to admin {admin_id}: {e}")


async def shutdown_message(bot: Bot):
    global exporter_service
    logger.info("Bot is shutting down.")
    
    # Закрываем ExporterService
    if exporter_service:
        try:
            logger.info("Closing ExporterService...")
            await exporter_service.close()
            logger.info("ExporterService closed successfully.")
        except Exception as e:
            logger.error(f"Error closing ExporterService: {e}", exc_info=True)
    
    # Можно добавить отправку сообщения админу об остановке бота
    for admin_id in ALLOWED_USER_IDS:
        try:
            await bot.send_message(admin_id, "Бот остановлен.")
        except Exception as e:
            logger.error(f"Could not send shutdown message to admin {admin_id}: {e}")


async def main() -> None:
    if not BOT_TOKEN:
        logger.error("Fatal: No bot token provided. Please set TELEGRAM_BOT_TOKEN in your .env file.")
        print("Fatal: No bot token provided. Please set TELEGRAM_BOT_TOKEN in your .env file.")
        return

    bot = Bot(token=BOT_TOKEN)

    # Регистрация обработчиков из других модулей
    dp.include_router(common_handlers.router) # Подключаем роутер с общими командами
    dp.include_router(export_handlers.router) # Подключаем роутер с командами экспорта

    # Регистрация функций startup и shutdown
    dp.startup.register(startup_message)
    dp.shutdown.register(shutdown_message)

    logger.info("Bot starting polling...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Error during bot polling: {e}", exc_info=True)
    finally:
        await bot.session.close() # Важно закрывать сессию при завершении работы
        logger.info("Bot polling finished and session closed.")


if __name__ == "__main__":
    # Для корректной работы относительных импортов, если вы запускаете этот файл напрямую,
    # убедитесь, что корневая директория проекта (telegram-obsidian) находится в PYTHONPATH,
    # или запускайте командой: python -m bot.main из директории telegram-obsidian
    # Пример:
    # cd .. (если вы в bot/)
    # python -m bot.main

    # Проверяем, что мы не в директории bot, чтобы относительные импорты работали
    if os.path.basename(os.getcwd()) == "bot":
        logger.warning("Running main.py directly from /bot directory. Relative imports might fail.")
        logger.warning("Consider running 'python -m bot.main' from the parent 'telegram-obsidian' directory.")


    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped manually.")
    except Exception as e:
        logger.critical(f"Bot failed to run: {e}", exc_info=True)

```
