import logging

from aiogram import Router, types
from aiogram.filters import CommandStart, Command
from aiogram.utils.markdown import hbold

# Используем относительный импорт для доступа к config.py из той же директории 'bot'
# Это предполагает, что бот запускается как модуль (например, python -m bot.main)
from ..config import ALLOWED_USER_IDS

logger = logging.getLogger(__name__)

router = Router()

# Фильтр для проверки, авторизован ли пользователь
# В aiogram 3.x можно создавать более сложные кастомные фильтры,
# но для простой проверки ID это подойдет.
# Мы будем использовать эту проверку прямо в хэндлерах.

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name

    if user_id not in ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized access attempt by user ID {user_id} ({user_name}) via /start.")
        await message.answer("К сожалению, у вас нет доступа к этому боту. Обратитесь к администратору.")
        return

    logger.info(f"User {user_name} (ID: {user_id}) started the bot.")
    await message.answer(
        f"Привет, {hbold(user_name)}!\n"
        f"Я бот для экспорта данных из Telegram в Obsidian.\n\n"
        f"Доступные команды:\n"
        f"/help - Показать это сообщение\n"
        f"/export - Начать процесс экспорта" # Эта команда будет в export_handlers
    )

@router.message(Command(commands=['help']))
async def cmd_help(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name

    if user_id not in ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized access attempt by user ID {user_id} ({user_name}) via /help.")
        await message.answer("К сожалению, у вас нет доступа к этому боту.")
        return

    logger.info(f"User {user_name} (ID: {user_id}) requested help.")
    await message.answer(
        "Это бот для экспорта данных из Telegram в Obsidian.\n\n"
        f"{hbold('Основные команды:')}\n"
        f"/start - Перезапустить бота и показать приветственное сообщение.\n"
        f"/help - Показать это справочное сообщение.\n"
        f"/export - Запустить процесс выбора и экспорта чатов/каналов.\n\n"
        # Можно добавить больше информации по мере развития бота
        # f"{hbold('Процесс экспорта:')}\n"
        # f"1. Выберите опцию: недавние чаты или ввод ID вручную.\n"
        # f"2. Следуйте инструкциям для выбора конкретного чата/канала.\n"
        # f"3. Дождитесь завершения экспорта и получения архива.\n\n"
        f"Если у вас возникли проблемы, обратитесь к администратору бота."
    )

# Можно добавить и другие общие команды, например, /status, /cancel (если будет FSM)
```
