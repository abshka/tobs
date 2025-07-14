class ExporterError(Exception):
    """
    Base exception for the exporter application.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.

    Returns:
        None
    """
    pass

class ConfigError(ExporterError):
    """
    Exception raised for errors related to configuration loading or validation.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.

    Returns:
        None
    """
    pass

class CacheError(ExporterError):
    """
    Exception raised for errors related to cache operations.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.

    Returns:
        None
    """
    pass

class MediaProcessingError(ExporterError):
    """
    Exception raised for errors during media download or optimization.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.

    Returns:
        None
    """
    pass

class NoteGenerationError(ExporterError):
    """
    Exception raised for errors during Markdown note generation or saving.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.

    Returns:
        None
    """
    pass

class TelegramConnectionError(ExporterError):
    """
    Exception raised for errors specific to Telegram connection or API interaction.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.

    Returns:
        None
    """
    pass

class ThreadPoolError(ExporterError):
    """
    Exception raised for errors related to thread pool execution.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.

    Returns:
        None
    """
    pass

class ParallelProcessingError(ExporterError):
    """
    Exception raised for errors during parallel processing of tasks.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.

    Returns:
        None
    """
    pass

class BatchProcessingError(ExporterError):
    """
    Exception raised for errors during batch processing of messages.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.

    Returns:
        None
    """
    pass
