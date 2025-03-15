"""
Модуль для интерактивного взаимодействия с пользователем.
"""

import os
from classes.config import Config


class InteractiveMenu:
    """Класс для интерактивного меню настройки скрипта."""

    def __init__(self, config: Config):
        self.config = config

    async def run(self) -> Config:
        """Запускает интерактивное меню и возвращает обновленную конфигурацию."""
        print("\n===== Telegram-Obsidian Exporter - Интерактивное меню =====\n")

        # Проверка и настройка API Telegram
        if not self.config.api_id or not self.config.api_hash:
            print("Необходимо указать API_ID и API_HASH для работы с Telegram API.")
            self.config.api_id = input("Введите ваш API_ID: ").strip()
            self.config.api_hash = input("Введите ваш API_HASH: ").strip()

        # Настройка канала
        print("\n--- Настройка источника данных ---")
        current_channel = (
            self.config.channel if self.config.channel != 0 else "не указан"
        )
        print(f"Текущий канал: {current_channel}")
        change_channel = (
                input("Хотите изменить ID канала? (y/n): ").strip().lower() == "y"
        )

        if change_channel:
            channel_input = input(
                "Введите ID канала (добавьте префикс -100 для приватных каналов): "
            ).strip()
            try:
                self.config.channel = int(channel_input)
            except ValueError:
                print("Ошибка: ID канала должен быть числом.")
                return self.config

        # Настройка пути к Obsidian
        print("\n--- Настройка пути к Obsidian ---")
        current_path = (
            self.config.obsidian_path if self.config.obsidian_path else "не указан"
        )
        print(f"Текущий путь: {current_path}")
        change_path = (
                input("Хотите изменить путь к Obsidian? (y/n): ").strip().lower() == "y"
        )

        if change_path:
            path_input = input("Введите путь к директории Obsidian: ").strip()
            if os.path.exists(path_input):
                self.config.obsidian_path = path_input
            else:
                create_dir = (
                        input(f"Директория {path_input} не существует. Создать? (y/n): ")
                        .strip()
                        .lower()
                        == "y"
                )
                if create_dir:
                    try:
                        os.makedirs(path_input, exist_ok=True)
                        self.config.obsidian_path = path_input
                    except Exception as e:
                        print(f"Ошибка при создании директории: {e}")
                        return self.config
                else:
                    print("Путь к Obsidian не изменен.")

        # Настройка параметров загрузки
        print("\n--- Настройка параметров загрузки ---")
        try:
            max_size_input = input(
                f"Максимальный размер видео в МБ (текущий: {self.config.max_video_size_mb}): "
            ).strip()
            if max_size_input:
                self.config.max_video_size_mb = int(max_size_input)

            concurrent_downloads = input(
                f"Количество одновременных загрузок (текущее: {self.config.max_concurrent_downloads}): "
            ).strip()
            if concurrent_downloads:
                self.config.max_concurrent_downloads = int(concurrent_downloads)
        except ValueError:
            print("Ошибка: параметры должны быть целыми числами.")

        # Настройка обработки
        print("\n--- Настройка обработки ---")
        skip_processed = (
            input(
                f"Пропускать обработанные сообщения? (y/n, текущее: {'y' if self.config.skip_processed else 'n'}): "
            )
            .strip()
            .lower()
        )
        if skip_processed in ["y", "n"]:
            self.config.skip_processed = skip_processed == "y"

        print("\nНастройка завершена. Запуск экспорта...")
        return self.config
