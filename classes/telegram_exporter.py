"""
Модуль для экспорта данных из Telegram.
"""

import os
import time
import asyncio
import logging
import psutil
from typing import Optional

from telethon import TelegramClient
from tqdm.asyncio import tqdm

from classes.config import Config
from classes.cache import Cache
from classes.media_processor import MediaProcessor
from classes.message_processor import MessageProcessor

logger = logging.getLogger(__name__)


class TelegramExporter:
    """Основной класс для экспорта данных из Telegram."""

    def __init__(self, config: Config):
        self.config = config
        self.cache_file = os.path.join(
            os.path.dirname(config.obsidian_path), ".telegram_cache.json"
        )
        self.cache = Cache(self.cache_file, config.cache_ttl)
        self.client = TelegramClient("session_name", config.api_id, config.api_hash)
        self.media_processor = MediaProcessor(config, self.cache)
        self.message_processor = MessageProcessor(
            config, self.cache, self.media_processor
        )
        self.memory_usage_warning_issued = False

    async def _check_resources(self):
        """Проверяет доступные системные ресурсы перед запуском."""
        # Проверка дискового пространства
        try:
            import shutil

            if os.path.exists(self.config.obsidian_path):
                free_space_bytes = shutil.disk_usage(self.config.obsidian_path).free
                free_space_mb = free_space_bytes / (1024 * 1024)

                if free_space_mb < 200:  # Проверка, что есть хотя бы 200 МБ места
                    logger.warning(
                        f"Внимание! Мало свободного места: {free_space_mb:.1f} МБ"
                    )
                    return False

            # Проверка доступности памяти
            try:


                memory_available_mb = psutil.virtual_memory().available / (1024 * 1024)
                if memory_available_mb < 100:  # Меньше 100 МБ
                    logger.warning(
                        f"Внимание! Мало доступной памяти: {memory_available_mb:.1f} МБ"
                    )
                    return False
            except ImportError:
                logger.debug("Модуль psutil не установлен, пропуск проверки памяти")

            return True
        except Exception as e:
            logger.warning(f"Не удалось проверить системные ресурсы: {e}")
            return True  # В случае ошибки продолжаем выполнение

    async def _monitor_memory_usage(self):
        """Периодически проверяет использование памяти."""
        try:
            import psutil
            import gc

            while True:
                await asyncio.sleep(30)  # Проверка каждые 30 секунд

                memory_usage_percent = psutil.virtual_memory().percent
                if memory_usage_percent > 80 and not self.memory_usage_warning_issued:
                    logger.warning(
                        f"Высокое использование памяти: {memory_usage_percent}%"
                    )
                    self.memory_usage_warning_issued = True

                    # Принудительный сбор мусора
                    collected = gc.collect()
                    logger.debug(f"Выполнен сбор мусора, собрано {collected} объектов")

                if memory_usage_percent < 70 and self.memory_usage_warning_issued:
                    self.memory_usage_warning_issued = False

        except ImportError:
            logger.debug("Модуль psutil не установлен, мониторинг памяти отключен")
        except Exception as e:
            logger.error(f"Ошибка мониторинга памяти: {e}")

    @staticmethod
    async def _confirm_continue(prompt: str) -> bool:
        """Запрашивает подтверждение пользователя для продолжения."""
        user_input = input(f"{prompt} (y/n): ").strip().lower()
        return user_input == "y"

    async def run(self, limit: Optional[int] = None, resume_from: Optional[int] = None):
        """Запуск основного процесса экспорта."""
        await self.cache.load()

        # Запуск мониторинга ресурсов в фоновом режиме
        resource_check = await self._check_resources()
        if not resource_check:
            if not await self._confirm_continue(
                "Обнаружена нехватка системных ресурсов. Продолжить?"
            ):
                logger.info("Операция отменена пользователем из-за нехватки ресурсов")
                return

        # Запуск мониторинга памяти в фоновом режиме
        memory_task = asyncio.create_task(self._monitor_memory_usage())

        try:
            logger.info("Получение сообщений из канала...")
            try:
                channel_entity = await self.client.get_entity(self.config.channel)
                logger.info(
                    f"Успешное подключение к каналу: {getattr(channel_entity, 'title', self.config.channel)}"
                )
            except Exception as e:
                logger.error(f"Не удалось получить доступ к каналу: {e}")
                if str(e).find("Cannot find any entity") != -1:
                    logger.info(
                        "Попробуйте добавить префикс '-100' к ID канала, если это приватный канал"
                    )
                return

            # Счетчики для статистики
            stats = {"processed": 0, "skipped": 0, "groups": 0, "files_created": 0}

            # Группировка сообщений
            logger.info("Получение сообщений из канала...")
            groups = {}

            start_time = time.time()
            with tqdm(desc="Загрузка сообщений") as msg_pbar:
                # Ограничение загрузки по количеству сообщений (если указано)
                iter_messages_kwargs = {"limit": limit} if limit else {}
                async for message in self.client.iter_messages(
                    self.config.channel, **iter_messages_kwargs
                ):
                    msg_pbar.update(1)

                    # Пропускаем уже обработанные сообщения
                    if self.config.skip_processed and self.cache.is_processed(
                        message.id
                    ):
                        stats["skipped"] += 1
                        continue

                    self.cache.add_message(message.id)
                    stats["processed"] += 1

                    try:
                        # Группировка сообщений по grouped_id или по одиночным сообщениям
                        if message.grouped_id:
                            groups.setdefault(message.grouped_id, []).append(message)
                        else:
                            groups[message.id] = [message]
                    except Exception as e:
                        logger.error(
                            f"Ошибка группировки сообщения {getattr(message, 'id', 'unknown')}: {e}"
                        )

                    # Обновление прогресс-бара
                    if stats["processed"] % 50 == 0:
                        elapsed = time.time() - start_time
                        rate = stats["processed"] / elapsed if elapsed > 0 else 0
                        msg_pbar.set_postfix(
                            загружено=stats["processed"],
                            групп=len(groups),
                            пропущено=stats["skipped"],
                            скорость=f"{rate:.1f} сообщ/сек",
                        )

            # Итоговая статистика
            elapsed = time.time() - start_time
            rate = stats["processed"] / elapsed if elapsed > 0 else 0
            logger.info(
                f"Всего загружено {stats['processed']} сообщений (пропущено {stats['skipped']})"
            )
            stats["groups"] = len(groups)
            logger.info(f"Найдено {stats['groups']} постов для обработки")

            # Работаем с группами для экономии памяти
            with tqdm(total=len(groups), desc="Обработка постов") as pbar:
                group_items = list(groups.items())
                for i in range(0, len(group_items), self.config.batch_size):
                    batch = group_items[i : i + self.config.batch_size]
                    batch_tasks = [
                        self.message_processor.process_message_group(key, msgs, pbar)
                        for key, msgs in batch
                    ]
                    # Запускаем batch_tasks параллельно и ждем их завершения
                    await asyncio.gather(*batch_tasks)

                    # Очистка обработанных групп для экономии памяти
                    for key, _ in batch:
                        if key in groups:
                            del groups[key]

                    # Принудительный сбор мусора каждые несколько пакетов
                    if i % (self.config.batch_size * 5) == 0 and i > 0:
                        import gc

                        gc.collect()

                    # Периодически сохраняем кэш для возможности восстановления после сбоя
                    if i % (self.config.batch_size * 2) == 0 and i > 0:
                        await self.cache.save()

                    # Обновляем статистику для файлов
                    stats["files_created"] = len(batch)

            # Окончательное сохранение кэша
            await self.cache.save()

            # Итоговая статистика
            end_time = time.time()
            duration = end_time - start_time
            logger.info(f"Экспорт завершен за {duration:.1f} секунд")
            logger.info(
                f"Обработано: {stats['processed']} сообщений, "
                f"создано постов: {stats['files_created']}, "
                f"пропущено: {stats['skipped']}"
            )
        except Exception as e:
            logger.error(f"Общая ошибка: {e}")
            # Сохраняем кэш даже при ошибке
            await self.cache.save()

        finally:
            # Отменяем задачу мониторинга памяти
            if "memory_task" in locals() and not memory_task.done():
                memory_task.cancel()
                try:
                    await memory_task
                except asyncio.CancelledError:
                    pass
