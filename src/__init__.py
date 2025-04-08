import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

# Оптимизация загрузки файлов с использованием пула потоков
class DownloadManager:
    def __init__(self, max_workers=5):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    async def download_files_parallel(self, files_to_download, download_function):
        """
        Загружает файлы параллельно используя пул потоков

        :param files_to_download: список файлов для загрузки
        :param download_function: функция загрузки одного файла
        :return: список результатов загрузки
        """
        loop = asyncio.get_running_loop()
        download_tasks = []

        for file_info in files_to_download:
            # Создаем частичную функцию с предустановленными аргументами
            download_task = partial(download_function, file_info)
            # Запускаем задачу в отдельном потоке
            task = loop.run_in_executor(self.executor, download_task)
            download_tasks.append(task)

        # Ждем завершения всех задач
        results = await asyncio.gather(*download_tasks, return_exceptions=True)
        return results

    def shutdown(self):
        """Завершает работу пула потоков"""
        self.executor.shutdown()

# Функция-хелпер для пакетной обработки
async def process_in_batches(items, batch_size, process_func):
    """
    Обрабатывает элементы пакетами для лучшего управления ресурсами

    :param items: список элементов для обработки
    :param batch_size: размер пакета
    :param process_func: асинхронная функция для обработки одного пакета
    """
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        await process_func(batch)
