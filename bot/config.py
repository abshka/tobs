import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла, находящегося в корне проекта
# Убедитесь, что у вас есть файл .env в директории telegram-obsidian/
# и в нем определена переменная TELEGRAM_BOT_TOKEN
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(dotenv_path=dotenv_path)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if BOT_TOKEN is None:
    print("Warning: TELEGRAM_BOT_TOKEN is not set in .env file. Please set it.")
    # Можно установить токен-заглушку для разработки, но для продакшена это небезопасно
    # BOT_TOKEN = "YOUR_BOT_TOKEN_PLACEHOLDER"


# Список разрешенных Telegram User ID
# Замените на реальные ID пользователей, которым разрешен доступ
# Вы можете получить свой ID, например, у бота @userinfobot
ALLOWED_USER_IDS = [
    123456789,  # Пример ID, замените его
    # 987654321,  # Еще один пример ID
]

# Настройки для архивации
MAX_ARCHIVE_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB в байтах
DEFAULT_ARCHIVE_NAME_PREFIX = "obsidian_export"

# Директория для временного хранения создаваемых архивов перед отправкой
# Она должна быть относительно корня проекта telegram-obsidian/
ARCHIVES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'archives')

# Убедимся, что директория для архивов существует
if not os.path.exists(ARCHIVES_DIR):
    try:
        os.makedirs(ARCHIVES_DIR)
        print(f"Created directory for archives: {ARCHIVES_DIR}")
    except OSError as e:
        print(f"Error creating directory {ARCHIVES_DIR}: {e}")
        # Если директорию создать не удалось, это может вызвать проблемы позже
        # Рассмотрите возможность остановки или другого способа обработки этой ошибки

# Путь к сессионному файлу Telethon, если он используется вашим основным проектом
# Это может понадобиться для доступа к "недавним чатам"
# Укажите правильное имя файла, если оно отличается
TELETHON_SESSION_NAME = "telegram_obsidian_session" # Имя файла без .session
# Полный путь к сессии, если main.py бота будет в bot/
TELETHON_SESSION_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), TELETHON_SESSION_NAME)


# Настройки для подключения к API Telegram (для Telethon, если используется в telegram-obsidian)
# Эти значения также должны быть в .env файле
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if API_ID is None or API_HASH is None:
    print("Warning: TELEGRAM_API_ID or TELEGRAM_API_HASH are not set in .env file.")
    print("These are required if your project uses Telethon for export.")

# Путь к лог-файлу (если потребуется для бота)
LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bot.log')

```
