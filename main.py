"""
Telegram Channel Media Downloader
Скрипт для экспорта сообщений и медиа из Telegram-каналов в Obsidian.
"""

import sys
import logging
import asyncio
import argparse
from classes.config import Config
from classes.media_processor import MediaProcessor
from classes.telegram_exporter import TelegramExporter
from classes.interactive_menu import InteractiveMenu

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("telegram_export.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def main():
    """Точка входа для асинхронного запуска."""
    # Аргументы командной строки
    parser = argparse.ArgumentParser(description="Telegram Channel Media Downloader")
    parser.add_argument("--debug", action="store_true", help="Включить режим отладки")
    parser.add_argument("--skip-cache", action="store_true", help="Игнорировать кэш")
    parser.add_argument("--limit", type=int, help="Ограничить количество сообщений")
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Запустить в интерактивном режиме",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Возобновить с последней сохраненной позиции",
    )
    parser.add_argument(
        "--optimize-images",
        action="store_true",
        help="Оптимизировать загруженные изображения для уменьшения размера",
    )
    args = parser.parse_args()

    # Загрузка конфигурации
    config = Config.from_env()

    # Настройка логирования в зависимости от режима
    if args.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger("telethon").setLevel(logging.INFO)

    if args.skip_cache:
        config.skip_processed = False
        logger.info("Кэш игнорируется, будут обработаны все сообщения")

    # Запуск интерактивного меню, если указан соответствующий флаг
    if args.interactive:
        menu = InteractiveMenu(config)
        config = await menu.run()

    # Создаем и запускаем экспортер
    exporter = TelegramExporter(config)

    if args.optimize_images:
        exporter.media_processor = MediaProcessor(
            config, exporter.cache, optimize_images=True
        )

    async with exporter.client:
        # Проверяем опцию возобновления
        if args.resume and exporter.cache.get_resume_point():
            print(
                f"Возобновление с последней сохраненной позиции: сообщение #{exporter.cache.get_resume_point()}"
            )
            await exporter.run(
                limit=args.limit, resume_from=exporter.cache.get_resume_point()
            )
        else:
            await exporter.run(limit=args.limit)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Операция прервана пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        logger.info("Работа скрипта завершена")
