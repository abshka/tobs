import time
from typing import Any, Dict, Optional
class ExporterError(Exception):
    """
    Базовое исключение для экспортера с расширенной диагностикой.

    Добавляет timestamp, context и performance metrics для лучшего debugging.
    """

    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None,
                 performance_data: Optional[Dict[str, float]] = None):
        super().__init__(message)
        self.message = message
        self.timestamp = time.time()
        self.context = context or {}
        self.performance_data = performance_data or {}

    def __str__(self) -> str:
        base_msg = self.message
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            base_msg += f" [Context: {context_str}]"
        if self.performance_data:
            perf_str = ", ".join(f"{k}={v:.3f}s" for k, v in self.performance_data.items())
            base_msg += f" [Performance: {perf_str}]"
        return base_msg
class ConfigError(ExporterError):
    """
    Исключение для ошибок конфигурации с валидацией полей.
    """

    def __init__(self, message: str, field_name: Optional[str] = None,
                 field_value: Optional[Any] = None, **kwargs):
        context = kwargs.pop('context', {})
        if field_name:
            context['field'] = field_name
        if field_value is not None:
            context['value'] = str(field_value)[:100]  # Ограничиваем длину для безопасности
        super().__init__(message, context=context, **kwargs)
class CacheError(ExporterError):
    """
    Исключение для операций с кэшем с метриками производительности.
    """

    def __init__(self, message: str, operation: Optional[str] = None,
                 cache_size: Optional[int] = None, **kwargs):
        context = kwargs.pop('context', {})
        if operation:
            context['operation'] = operation
        if cache_size is not None:
            context['cache_size'] = cache_size
        super().__init__(message, context=context, **kwargs)
class MediaProcessingError(ExporterError):
    """
    Исключение для обработки медиа с информацией о файле и производительности.
    """

    def __init__(self, message: str, media_type: Optional[str] = None,
                 file_size: Optional[int] = None, media_id: Optional[str] = None, **kwargs):
        context = kwargs.pop('context', {})
        if media_type:
            context['media_type'] = media_type
        if file_size is not None:
            context['file_size_mb'] = round(file_size / (1024 * 1024), 2)
        if media_id:
            context['media_id'] = str(media_id)
        super().__init__(message, context=context, **kwargs)
class NoteGenerationError(ExporterError):
    """
    Исключение для генерации заметок с информацией о файле.
    """

    def __init__(self, message: str, note_path: Optional[str] = None,
                 message_id: Optional[int] = None, **kwargs):
        context = kwargs.pop('context', {})
        if note_path:
            context['note_path'] = str(note_path)
        if message_id is not None:
            context['message_id'] = message_id
        super().__init__(message, context=context, **kwargs)
class TelegramConnectionError(ExporterError):
    """
    Исключение для Telegram API с информацией о соединении.
    """

    def __init__(self, message: str, api_method: Optional[str] = None,
                 retry_count: Optional[int] = None, **kwargs):
        context = kwargs.pop('context', {})
        if api_method:
            context['api_method'] = api_method
        if retry_count is not None:
            context['retry_count'] = retry_count
        super().__init__(message, context=context, **kwargs)

class ConcurrencyError(ExporterError):
    """
    Исключение для управления конкуренцией и семафорами.
    """

    def __init__(self, message: str, semaphore_type: Optional[str] = None,
                 active_tasks: Optional[int] = None, max_concurrent: Optional[int] = None, **kwargs):
        context = kwargs.pop('context', {})
        if semaphore_type:
            context['semaphore_type'] = semaphore_type
        if active_tasks is not None:
            context['active_tasks'] = active_tasks
        if max_concurrent is not None:
            context['max_concurrent'] = max_concurrent
        super().__init__(message, context=context, **kwargs)
class StreamingError(ExporterError):
    """
    Исключение для streaming pipeline и потоковой обработки.
    """

    def __init__(self, message: str, pipeline_stage: Optional[str] = None,
                 processed_count: Optional[int] = None, **kwargs):
        context = kwargs.pop('context', {})
        if pipeline_stage:
            context['pipeline_stage'] = pipeline_stage
        if processed_count is not None:
            context['processed_count'] = processed_count
        super().__init__(message, context=context, **kwargs)
class BatchOperationError(ExporterError):
    """
    Исключение для батчевых операций с метриками.
    """

    def __init__(self, message: str, batch_size: Optional[int] = None,
                 failed_items: Optional[int] = None, **kwargs):
        context = kwargs.pop('context', {})
        if batch_size is not None:
            context['batch_size'] = batch_size
        if failed_items is not None:
            context['failed_items'] = failed_items
        super().__init__(message, context=context, **kwargs)
class PerformanceError(ExporterError):
    """
    Исключение для проблем производительности с детальными метриками.
    """

    def __init__(self, message: str, operation_name: Optional[str] = None,
                 duration: Optional[float] = None, memory_usage: Optional[int] = None, **kwargs):
        context = kwargs.pop('context', {})
        performance_data = kwargs.pop('performance_data', {})

        if operation_name:
            context['operation'] = operation_name
        if duration is not None:
            performance_data['duration'] = duration
        if memory_usage is not None:
            context['memory_mb'] = round(memory_usage / (1024 * 1024), 2)

        super().__init__(message, context=context, performance_data=performance_data, **kwargs)
class ResourceExhaustionError(ExporterError):
    """
    Исключение для исчерпания ресурсов (память, дисковое пространство, etc).
    """

    def __init__(self, message: str, resource_type: Optional[str] = None,
                 current_usage: Optional[float] = None, max_limit: Optional[float] = None, **kwargs):
        context = kwargs.pop('context', {})
        if resource_type:
            context['resource_type'] = resource_type
        if current_usage is not None and max_limit is not None:
            context['usage_percent'] = round((current_usage / max_limit) * 100, 1)
        super().__init__(message, context=context, **kwargs)

def create_performance_context(start_time: float, operation_name: str,
                             **additional_metrics) -> Dict[str, Any]:
    """
    Создает контекст производительности для исключений.

    Args:
        start_time: Время начала операции (time.time())
        operation_name: Имя операции
        **additional_metrics: Дополнительные метрики

    Returns:
        Словарь с контекстом и метриками производительности
    """
    duration = time.time() - start_time
    return {
        'context': {'operation': operation_name},
        'performance_data': {'duration': duration, **additional_metrics}
    }
def handle_with_context(func):
    """
    Декоратор для автоматического добавления контекста к исключениям.

    Пример использования:
    @handle_with_context
    async def download_media(self, message_id):
        # При любом исключении будет добавлен контекст с message_id
        pass
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ExporterError:
            raise  # Переиспускаем наши исключения как есть
        except Exception as e:
            # Оборачиваем системные исключения в наши с контекстом
            context = {
                'function': func.__name__,
                'args_count': len(args),
                'kwargs_keys': list(kwargs.keys())
            }
            raise ExporterError(f"Unexpected error in {func.__name__}: {str(e)}",
                               context=context) from e
    return wrapper
