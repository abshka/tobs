import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial


class DownloadManager:
    """Manages parallel downloading of files using a thread pool."""

    def __init__(self, max_workers=5):
        """Initialize the DownloadManager with a thread pool of given size."""
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    async def download_files_parallel(self, files_to_download, download_function):
        """
        Downloads files in parallel using a thread pool.

        :param files_to_download: List of files to download.
        :param download_function: Function to download a single file.
        :return: List of download results.
        """
        loop = asyncio.get_running_loop()
        download_tasks = []

        for file_info in files_to_download:
            # Create a partial function with preset arguments
            download_task = partial(download_function, file_info)
            # Run the task in a separate thread
            task = loop.run_in_executor(self.executor, download_task)
            download_tasks.append(task)

        # Wait for all tasks to complete
        results = await asyncio.gather(*download_tasks, return_exceptions=True)
        return results

    def shutdown(self):
        """Shuts down the thread pool executor."""
        self.executor.shutdown()

# Helper function for batch processing
async def process_in_batches(items, batch_size, process_func):
    """
    Processes items in batches for better resource management.

    :param items: List of items to process.
    :param batch_size: Size of each batch.
    :param process_func: Asynchronous function to process a single batch.
    """
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        await process_func(batch)
