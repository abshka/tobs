class ExporterError(Exception):
    """Base exception for the exporter application."""
    pass

class ConfigError(ExporterError):
    """Error related to configuration loading or validation."""
    pass

class CacheError(ExporterError):
    """Error related to cache operations."""
    pass

class MediaProcessingError(ExporterError):
    """Error during media download or optimization."""
    pass

class NoteGenerationError(ExporterError):
    """Error during Markdown note generation or saving."""
    pass

class TelegramConnectionError(ExporterError):
     """Error specific to Telegram connection or API interaction."""
     pass
