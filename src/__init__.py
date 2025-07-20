import asyncio


class DownloadManager:
    """Manages parallel downloading of files using asyncio."""

    def __init__(self, max_workers=5):
        """Initialize the DownloadManager with a max_workers limit (not used in pure asyncio)."""
        self.max_workers = max_workers

    async def download_files_parallel(self, files_to_download, download_function):
        """
        Downloads files in parallel using asyncio.

        :param files_to_download: List of files to download.
        :param download_function: Async function to download a single file.
        :return: List of download results.
        """
        download_tasks = [download_function(file_info) for file_info in files_to_download]
        results = await asyncio.gather(*download_tasks, return_exceptions=True)
        return results


    def shutdown(self):
        """Shuts down the thread pool executor."""
        self.executor.shutdown()

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
