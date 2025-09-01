import asyncio

class ConcurrencyManager:
    """
    A centralized manager for handling concurrency across the application
    using asyncio Semaphores to avoid performance bottlenecks.
    """
    def __init__(self, workers: int = 8, ffmpeg_workers: int = 4):
        """
        Initializes the ConcurrencyManager with specified worker counts.

        Args:
            workers (int): The base number of workers for downloads and processing.
            ffmpeg_workers (int): The number of workers specifically for FFmpeg tasks.
        """
        self.download_semaphore = asyncio.Semaphore(workers)
        self.processing_semaphore = asyncio.Semaphore(workers // 2 or 1)
        self.io_semaphore = asyncio.Semaphore(min(workers * 2, 20))
        self.ffmpeg_semaphore = asyncio.Semaphore(min(ffmpeg_workers, workers))
