"""
Unified cache manager combining simple and advanced caching.
"""

import asyncio
import base64
import logging
import pickle
import time
import zlib
from collections import OrderedDict
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Union

import aiofiles
import orjson

logger = logging.getLogger(__name__)


class CacheStrategy(Enum):
    """Caching strategies."""

    SIMPLE = "simple"
    LRU = "lru"
    TTL = "ttl"


class CompressionType(Enum):
    """Data compression types."""

    NONE = "none"
    GZIP = "gzip"
    PICKLE = "pickle"


@dataclass
class CacheEntry:
    """Cache entry."""

    data: Any
    created_at: float
    last_accessed: float
    access_count: int = 0
    ttl: Optional[float] = None
    compressed: bool = False
    compression_type: str = "none"

    def is_expired(self) -> bool:
        """Check TTL expiration."""
        if self.ttl is None:
            return False
        return (time.time() - self.created_at) > self.ttl

    def update_access(self):
        """Update access statistics."""
        self.last_accessed = time.time()
        self.access_count += 1


@dataclass
class CacheStats:
    """Cache statistics."""

    hits: int = 0
    misses: int = 0
    sets: int = 0
    deletes: int = 0
    evictions: int = 0
    compression_saves: int = 0
    compression_fallbacks: int = 0
    total_size_mb: float = 0.0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total > 0 else 0.0


def _json_default(obj):
    if isinstance(obj, set):
        return list(obj)
    raise TypeError


class CacheManager:
    """Кэш-менеджер с поддержкой различных стратегий."""

    def __init__(
        self,
        cache_path: Path,
        strategy: CacheStrategy = CacheStrategy.LRU,
        max_size: int = 1000,
        default_ttl: Optional[float] = None,
        compression: CompressionType = CompressionType.GZIP,
        auto_save_interval: float = 30.0,
        compression_threshold: int = 1024,  # Сжимать данные больше 1KB
    ):
        self.cache_path = cache_path.resolve()
        self.backup_path = self.cache_path.with_suffix(".backup")
        self.strategy = strategy
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.compression = compression
        self.compression_threshold = compression_threshold
        self.auto_save_interval = auto_save_interval

        # Основное хранилище
        self._cache: Union[OrderedDict[str, CacheEntry], Dict[str, CacheEntry]]
        if strategy == CacheStrategy.LRU:
            self._cache = OrderedDict()
        else:
            self._cache = {}

        # Блокировка и состояние
        self._lock = asyncio.Lock()
        self._stats = CacheStats()
        self._dirty = False

        # TaskGroup для управления background tasks
        self._task_group: Optional[asyncio.TaskGroup] = None
        self._task_group_runner: Optional[asyncio.Task] = None
        self._shutdown = False

    async def start(self):
        """Запуск кэш-менеджера."""
        await self._load_cache()
        # Start background tasks in TaskGroup
        self._task_group_runner = asyncio.create_task(self._run_background_tasks())
        # Backward compatibility: alias for older tests or code that expects _auto_save_task
        self._auto_save_task = self._task_group_runner
        logger.info(f"Cache manager started with {self.strategy.value} strategy")

    async def _run_background_tasks(self):
        """
        Запуск и управление background tasks в TaskGroup.

        TaskGroup автоматически:
        - Управляет жизненным циклом всех задач
        - Агрегирует исключения из всех задач
        - Отменяет все оставшиеся задачи при выходе из контекста
        """
        try:
            async with asyncio.TaskGroup() as tg:
                self._task_group = tg
                logger.debug("CacheManager TaskGroup context entered")

                # Создаём background task в TaskGroup
                tg.create_task(self._auto_save_loop())
                logger.debug("Auto-save task created in TaskGroup")

        except* Exception as exc_group:
            # except* ловит все исключения из TaskGroup
            for exc in exc_group.exceptions:
                logger.error(
                    f"Cache manager background task failed: {type(exc).__name__}: {exc}",
                    exc_info=exc,
                )
        finally:
            self._task_group = None
            logger.debug("CacheManager TaskGroup context exited and cleaned up")

    async def _auto_save_loop(self):
        """Автоматическое сохранение кэша."""
        while not self._shutdown:
            try:
                await asyncio.sleep(self.auto_save_interval)
                if self._dirty:
                    await self._save_cache()
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in auto-save loop: {e}")

    async def _cleanup_expired(self):
        """Очистка истекших записей."""
        if self.strategy != CacheStrategy.TTL and self.default_ttl is None:
            return

        async with self._lock:
            expired_keys = []
            for key, entry in self._cache.items():
                if entry.is_expired():
                    expired_keys.append(key)

            for key in expired_keys:
                del self._cache[key]
                self._stats.evictions += 1

            if expired_keys:
                self._dirty = True
                logger.debug(f"Cleaned up {len(expired_keys)} expired entries")

    def _compress_data(self, data: Any) -> tuple[Any, bool, str]:
        """Сжатие данных если нужно."""
        if self.compression == CompressionType.NONE:
            return data, False, "none"

        # Сериализуем данные для определения размера
        try:
            if isinstance(data, str):
                raw_data = data.encode("utf-8")
            elif isinstance(data, bytes):
                raw_data = data
            else:
                # For extraction of raw_data we choose pickled bytes if PICKLE compression is configured,
                # otherwise we prefer JSON (orjson) which helps decide compression threshold.
                if self.compression == CompressionType.PICKLE:
                    raw_data = pickle.dumps(data)
                else:
                    # Для сложных объектов пытаемся сериализовать в JSON с помощью orjson
                    try:
                        raw_data = orjson.dumps(data, option=orjson.OPT_NON_STR_KEYS)
                    except TypeError as e:
                        # Not JSON serializable, fallback to pickle for GZIP if configured
                        logger.debug(
                            f"Data not JSON serializable: {e}. Falling back to pickle compression."
                        )
                        if self.compression == CompressionType.GZIP:
                            pickled_raw = pickle.dumps(data)
                            # Track that we fell back from JSON to pickle serialization
                            self._stats.compression_fallbacks += 1
                            if len(pickled_raw) < self.compression_threshold:
                                return data, False, "none"
                            compressed = zlib.compress(pickled_raw)
                            if len(compressed) < len(pickled_raw) * 0.9:
                                self._stats.compression_saves += 1
                                return compressed, True, "pickle"
                        return data, False, "none"

            if len(raw_data) < self.compression_threshold:
                return data, False, "none"

            if self.compression == CompressionType.GZIP:
                compressed = zlib.compress(raw_data)
                if len(compressed) < len(raw_data) * 0.9:  # Сжатие эффективно
                    self._stats.compression_saves += 1
                    return compressed, True, "gzip"
            elif self.compression == CompressionType.PICKLE:
                pickled_raw = pickle.dumps(data)
                compressed = zlib.compress(pickled_raw)
                # For PICKLE compression we always store pickled bytes to preserve types
                self._stats.compression_saves += 1
                return compressed, True, "pickle"

        except Exception as e:
            logger.warning(f"Compression failed: {e}")

        return data, False, "none"

    def _decompress_data(self, data: Any, compression_type: str) -> Any:
        """Распаковка сжатых данных."""
        if compression_type == "none":
            return data

        if not isinstance(data, bytes):
            return data

        try:
            if compression_type == "gzip":
                decompressed_bytes = zlib.decompress(data)
                # Используем orjson для разбора JSON
                try:
                    return orjson.loads(decompressed_bytes)
                except orjson.JSONDecodeError:
                    # Если не JSON, возвращаем как строку
                    return decompressed_bytes.decode("utf-8")
            elif compression_type == "pickle":
                return pickle.loads(zlib.decompress(data))
        except Exception as e:
            logger.error(f"Decompression failed: {e}")
            return data

        return data

    async def get(self, key: str, default=None) -> Any:
        """Получение значения из кэша."""
        async with self._lock:
            if key not in self._cache:
                self._stats.misses += 1
                return default

            entry = self._cache[key]

            # Проверяем TTL
            if entry.is_expired():
                del self._cache[key]
                self._stats.misses += 1
                self._stats.evictions += 1
                self._dirty = True
                return default

            # Обновляем статистику доступа
            entry.update_access()
            self._stats.hits += 1

            # Для LRU - перемещаем в конец
            if self.strategy == CacheStrategy.LRU and isinstance(
                self._cache, OrderedDict
            ):
                self._cache.move_to_end(key)

            # Распаковываем данные если нужно
            data = self._decompress_data(entry.data, entry.compression_type)
            return data

    async def set(self, key: str, value: Any, ttl: Optional[float] = None):
        """Сохранение значения в кэш."""
        async with self._lock:
            # Сжимаем данные если нужно
            compressed_data, is_compressed, compression_type = self._compress_data(
                value
            )

            # Определяем TTL
            entry_ttl = ttl or self.default_ttl

            now = time.time()
            entry = CacheEntry(
                data=compressed_data,
                created_at=now,
                last_accessed=now,
                access_count=1,
                ttl=entry_ttl,
                compressed=is_compressed,
                compression_type=compression_type,
            )

            # Добавляем в кэш
            self._cache[key] = entry
            self._stats.sets += 1
            self._dirty = True

            # Для LRU - перемещаем в конец
            if self.strategy == CacheStrategy.LRU and isinstance(
                self._cache, OrderedDict
            ):
                self._cache.move_to_end(key)

            # Проверяем лимиты и вытесняем если нужно
            await self._evict_if_needed()

    async def _evict_if_needed(self):
        """Вытеснение записей при превышении лимитов."""
        if len(self._cache) <= self.max_size:
            return

        evict_count = len(self._cache) - self.max_size + 1

        if self.strategy == CacheStrategy.LRU and isinstance(self._cache, OrderedDict):
            # LRU - удаляем самые старые
            for _ in range(evict_count):
                if self._cache:
                    self._cache.popitem(last=False)
                    self._stats.evictions += 1
        else:
            # Удаляем по времени последнего доступа
            items = list(self._cache.items())
            items.sort(key=lambda x: x[1].last_accessed)

            for i in range(min(evict_count, len(items))):
                key = items[i][0]
                del self._cache[key]
                self._stats.evictions += 1

        logger.debug(f"Evicted {evict_count} entries, cache size: {len(self._cache)}")

    async def delete(self, key: str) -> bool:
        """Удаление ключа из кэша."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._stats.deletes += 1
                self._dirty = True
                return True
            return False

    async def clear(self):
        """Очистка всего кэша."""
        async with self._lock:
            cleared_count = len(self._cache)
            self._cache.clear()
            self._stats.deletes += cleared_count
            self._dirty = True
            logger.info(f"Cache cleared, removed {cleared_count} entries")

    async def _load_cache(self):
        """Загрузка кэша из файла."""
        if not self.cache_path.exists():
            logger.info("Cache file does not exist, starting fresh")
            return

        try:
            async with aiofiles.open(self.cache_path, "rb") as f:
                content = await f.read()

            if not content.strip():
                logger.info("Cache file is empty")
                return

            data = orjson.loads(content)

            if isinstance(data, dict) and "entries" in data:
                entries_data = data["entries"]
                loaded_count = 0

                for key, entry_data in entries_data.items():
                    try:
                        raw_data = entry_data["data"]
                        if (
                            isinstance(raw_data, str)
                            and entry_data.get("data_encoding") == "base64"
                        ):
                            try:
                                processed_data = base64.b64decode(
                                    raw_data.encode("ascii")
                                )
                            except Exception:
                                processed_data = raw_data
                        else:
                            processed_data = raw_data

                        entry = CacheEntry(
                            data=processed_data,
                            created_at=entry_data.get("created_at", time.time()),
                            last_accessed=entry_data.get("last_accessed", time.time()),
                            access_count=entry_data.get("access_count", 0),
                            ttl=entry_data.get("ttl"),
                            compressed=entry_data.get("compressed", False),
                            compression_type=entry_data.get("compression_type", "none"),
                        )

                        # Пропускаем истекшие записи
                        if not entry.is_expired():
                            self._cache[key] = entry
                            loaded_count += 1

                    except Exception as e:
                        logger.warning(f"Skipping invalid cache entry {key}: {e}")

                logger.info(f"Loaded {loaded_count} cache entries")

        except orjson.JSONDecodeError as e:
            logger.error(f"Invalid JSON in cache file: {e}")
            await self._try_restore_from_backup()
        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
            await self._try_restore_from_backup()

    async def _try_restore_from_backup(self):
        """Попытка восстановления из резервной копии."""
        if not self.backup_path.exists():
            logger.info("No backup file found")
            return

        try:
            async with aiofiles.open(self.backup_path, "rb") as f:
                content = await f.read()

            data = orjson.loads(content)
            # Аналогично загрузке основного файла
            if isinstance(data, dict) and "entries" in data:
                entries_data = data["entries"]
                loaded_count = 0

                for key, entry_data in entries_data.items():
                    try:
                        raw_data = entry_data.get("data")
                        if (
                            isinstance(raw_data, str)
                            and entry_data.get("data_encoding") == "base64"
                        ):
                            try:
                                processed_data = base64.b64decode(
                                    raw_data.encode("ascii")
                                )
                            except Exception:
                                processed_data = raw_data
                        else:
                            processed_data = raw_data

                        # Validate timestamps in backup entry
                        created_at_raw = entry_data.get("created_at", time.time())
                        last_accessed_raw = entry_data.get("last_accessed", time.time())
                        try:
                            created_at_val = float(created_at_raw)
                            last_accessed_val = float(last_accessed_raw)
                        except Exception as e:
                            logger.warning(
                                f"Skipping invalid backup entry {key}: invalid timestamps: {e}"
                            )
                            continue

                        entry = CacheEntry(
                            data=processed_data,
                            created_at=created_at_val,
                            last_accessed=last_accessed_val,
                            access_count=entry_data.get("access_count", 0),
                            ttl=entry_data.get("ttl"),
                            compressed=entry_data.get("compressed", False),
                            compression_type=entry_data.get("compression_type", "none"),
                        )
                        if not entry.is_expired():
                            self._cache[key] = entry
                            loaded_count += 1
                    except Exception as e:
                        logger.warning(f"Skipping invalid backup entry {key}: {e}")

                logger.info(f"Restored {loaded_count} entries from backup")

        except Exception as e:
            logger.error(f"Failed to restore from backup: {e}")

    async def _save_cache(self):
        """Сохранение кэша в файл."""
        if not self._dirty:
            return

        try:
            # Создаем резервную копию
            if self.cache_path.exists():
                async with aiofiles.open(self.cache_path, "rb") as src:
                    content = await src.read()
                async with aiofiles.open(self.backup_path, "wb") as dst:
                    await dst.write(content)

            # Подготавливаем данные
            cache_data: Dict[str, Any] = {
                "version": 2,
                "timestamp": time.time(),
                "strategy": self.strategy.value,
                "compression": self.compression.value,
                "entries": {},
            }

            async with self._lock:
                for key, entry in self._cache.items():
                    if not entry.is_expired():
                        entries_dict = cache_data["entries"]
                        if isinstance(entries_dict, dict):
                            # use asdict but encode bytes data to base64 to make JSON serialization safe
                            entry_dict = asdict(entry)
                            if isinstance(entry_dict.get("data"), (bytes, bytearray)):
                                entry_dict["data"] = base64.b64encode(
                                    entry_dict["data"]
                                ).decode("ascii")
                                entry_dict["data_encoding"] = "base64"
                            entries_dict[key] = entry_dict

            # Сохраняем с orjson (гораздо быстрее)
            json_bytes = orjson.dumps(
                cache_data,
                option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS,
                default=_json_default,
            )

            async with aiofiles.open(self.cache_path, "wb") as f:
                await f.write(json_bytes)

            self._dirty = False
            logger.debug(f"Cache saved with {len(self._cache)} entries")

        except Exception as e:
            logger.error(f"Failed to save cache: {e}")
            raise

    async def shutdown(self):
        """Корректное завершение работы."""
        self._shutdown = True

        # Остановка background tasks runner
        # TaskGroup автоматически отменит все задачи при выходе
        if self._task_group_runner:
            if not self._task_group_runner.done():
                logger.debug("Cancelling cache manager background task group runner...")
                self._task_group_runner.cancel()
                try:
                    await self._task_group_runner
                except asyncio.CancelledError:
                    logger.debug(
                        "Cache manager background task group runner cancelled successfully"
                    )
            else:
                logger.debug("Cache manager background task group runner already done")

        # Финальное сохранение
        if self._dirty:
            await self._save_cache()

        logger.info("Cache manager shutdown complete")

    def get_stats(self) -> CacheStats:
        """Получение статистики кэша."""
        self._stats.total_size_mb = len(self._cache) * 0.001  # Приблизительно
        return self._stats

    # Методы для обратной совместимости с старым API

    async def is_processed(self, message_id: int, entity_id: str) -> bool:
        """Проверка обработанного сообщения."""
        entity_data = await self.get(f"entity_{entity_id}", {})
        processed_messages = entity_data.get("processed_messages", {})
        return str(message_id) in processed_messages

    async def add_processed_message_async(
        self, message_id: int, entity_id: str, **kwargs
    ):
        """Добавление обработанного сообщения."""
        entity_data = await self.get(
            f"entity_{entity_id}",
            {
                "processed_messages": {},
                "last_id": None,
                "title": "Unknown",
                "type": "unknown",
            },
        )

        entity_data["processed_messages"][str(message_id)] = {
            "timestamp": time.time(),
            **kwargs,
        }
        entity_data["last_id"] = message_id

        await self.set(f"entity_{entity_id}", entity_data)

    async def update_entity_info_async(
        self, entity_id: str, title: str, entity_type: str
    ):
        """Обновление информации о сущности."""
        entity_data = await self.get(
            f"entity_{entity_id}",
            {
                "processed_messages": {},
                "last_id": None,
                "title": "Unknown",
                "type": "unknown",
            },
        )

        entity_data["title"] = title
        entity_data["type"] = entity_type

        await self.set(f"entity_{entity_id}", entity_data)

    async def get_last_processed_message_id_async(
        self, entity_id: str
    ) -> Optional[int]:
        """Получение ID последнего обработанного сообщения."""
        entity_data = await self.get(f"entity_{entity_id}")
        if entity_data and "last_id" in entity_data:
            last_id = entity_data["last_id"]
            return int(last_id) if last_id is not None else None
        return None

    async def get_all_processed_messages_async(self, entity_id: str) -> Dict[str, Any]:
        """Получение всех обработанных сообщений."""
        entity_data = await self.get(f"entity_{entity_id}", {})
        processed_messages = entity_data.get("processed_messages", {})
        return dict(processed_messages) if isinstance(processed_messages, dict) else {}

    @property
    def cache(self):
        """Свойство для обратной совместимости."""
        result: Dict[str, Any] = {"version": 2, "entities": {}}
        for key, entry in self._cache.items():
            if key.startswith("entity_"):
                entity_id = key[7:]
                entities_dict = result["entities"]
                if isinstance(entities_dict, dict):
                    entities_dict[entity_id] = entry.data
        return result

    async def flush_all_pending(self):
        """Принудительное сохранение."""
        await self._save_cache()


# Глобальный экземпляр кэш-менеджера
_cache_manager: Optional[CacheManager] = None


async def get_cache_manager() -> CacheManager:
    """Получение глобального экземпляра кэш-менеджера."""
    global _cache_manager
    if _cache_manager is None:
        # Используем временную директорию для глобального кэша
        import tempfile

        temp_dir = Path(tempfile.gettempdir()) / "tobs_cache"
        temp_dir.mkdir(exist_ok=True)
        cache_path = temp_dir / "cache.json"
        _cache_manager = CacheManager(cache_path)
        await _cache_manager.start()
    return _cache_manager


async def shutdown_cache_manager():
    """Завершение работы кэш-менеджера."""
    global _cache_manager
    if _cache_manager:
        await _cache_manager.shutdown()
        _cache_manager = None
