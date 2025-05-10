import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hbold, hcode, hitalic

from ..config import ALLOWED_USER_IDS, DEFAULT_ARCHIVE_NAME_PREFIX
from ..services.archiver import create_archive_from_directory

# Импортируем сервисы
from ..services.exporter_service import ExporterService

logger = logging.getLogger(__name__)

# Определяем состояния FSM для диалога экспорта
class ExportStates(StatesGroup):
    waiting_for_method = State()     # Ожидание выбора метода: недавние чаты или ввод ID
    waiting_for_id = State()         # Ожидание ввода ID вручную
    waiting_for_selection = State()  # Ожидание выбора из списка недавних чатов
    exporting = State()              # Экспорт в процессе
    archiving = State()              # Архивация в процессе
    sending = State()                # Отправка архива пользователю

# Создаем роутер для обработчиков экспорта
router = Router()

# Глобальный экземпляр ExporterService будет инициализирован при старте бота
# и передан сюда через параметр 'exporter_service' в aiogram.Router.message.middleware
# см. bot/main.py для инициализации
exporter_service = None

# Функция для проверки авторизованного пользователя
def is_authorized(user_id: int) -> bool:
    return user_id in ALLOWED_USER_IDS

# Функция регистрации ExporterService
def register_exporter_service(service: ExporterService):
    global exporter_service
    exporter_service = service
    logger.info("ExporterService registered with export_handlers")

# Команда /export - начало процесса экспорта
@router.message(Command("export"))
async def cmd_export(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_authorized(user_id):
        logger.warning(f"Unauthorized access attempt to /export by user ID: {user_id}")
        await message.answer("У вас нет доступа к этой команде.")
        return

    # Проверяем, что ExporterService инициализирован
    if not exporter_service:
        logger.error("ExporterService not initialized for export handlers")
        await message.answer("Ошибка: сервис экспорта не инициализирован. Попробуйте позже или обратитесь к администратору.")
        return

    # Создаем клавиатуру с выбором метода экспорта
    builder = InlineKeyboardBuilder()
    builder.button(text="Выбрать из недавних чатов", callback_data="export_method:recent")
    builder.button(text="Ввести ID канала/чата", callback_data="export_method:input_id")
    builder.adjust(1)  # По одной кнопке в ряд

    await message.answer(
        "Выберите, как вы хотите указать чат/канал для экспорта:",
        reply_markup=builder.as_markup()
    )

    # Устанавливаем состояние ожидания выбора метода
    await state.set_state(ExportStates.waiting_for_method)

# Обработчик выбора метода экспорта
@router.callback_query(ExportStates.waiting_for_method, F.data.startswith("export_method:"))
async def process_export_method_selection(callback: CallbackQuery, state: FSMContext):
    method = callback.data.split(":", 1)[1]

    await callback.answer()  # Подтверждаем обработку CallbackQuery

    if method == "recent":
        # Показываем список недавних чатов
        await show_recent_dialogs(callback.message, state)
    elif method == "input_id":
        # Запрашиваем ввод ID чата/канала
        await callback.message.answer(
            "Пожалуйста, введите ID канала/чата для экспорта.\n"
            "Это должно быть числовое значение, например: -1001234567890.\n\n"
            "Вы также можете использовать username канала без @, например: durov"
        )
        await state.set_state(ExportStates.waiting_for_id)
    else:
        # Неизвестный метод, перезапрашиваем выбор
        await callback.message.answer("Неизвестный метод выбора. Пожалуйста, попробуйте снова с командой /export.")
        await state.clear()

# Функция для отображения списка недавних чатов
async def show_recent_dialogs(message: Message, state: FSMContext):
    # Индикатор загрузки
    loading_msg = await message.answer("Загрузка списка недавних чатов...")

    try:
        # Получаем список диалогов через ExporterService
        dialogs = await exporter_service.get_recent_dialogs(limit=20)

        if not dialogs:
            await loading_msg.edit_text("Не удалось получить список недавних чатов. Проверьте логи или попробуйте ввести ID вручную.")
            await state.clear()
            return

        # Сохраняем диалоги в состояние для дальнейшего использования
        await state.update_data(dialogs=dialogs)

        # Создаем клавиатуру со списком чатов
        builder = InlineKeyboardBuilder()

        for dialog in dialogs:
            # Создаем callback_data с ID чата и типом
            dialog_type = dialog['type']
            dialog_id = dialog['id']
            dialog_title = dialog['title']

            # Обрежем title до 30 символов, если он длиннее
            display_title = dialog_title[:30] + "..." if len(dialog_title) > 30 else dialog_title

            # Добавим эмодзи в зависимости от типа
            emoji = "📢 " if dialog_type == "channel" else "👥 " if dialog_type == "group" else "👤 "

            builder.button(
                text=f"{emoji}{display_title}",
                callback_data=f"export_dialog:{dialog_id}:{dialog_type}:{dialog_title}"
            )

        # Добавляем кнопку отмены
        builder.button(text="❌ Отмена", callback_data="export_cancel")

        # По одной кнопке в ряд
        builder.adjust(1)

        await loading_msg.edit_text(
            "Выберите чат или канал для экспорта из списка недавних:",
            reply_markup=builder.as_markup()
        )

        # Устанавливаем состояние ожидания выбора чата
        await state.set_state(ExportStates.waiting_for_selection)

    except Exception as e:
        logger.error(f"Error showing recent dialogs: {e}", exc_info=True)
        await loading_msg.edit_text(f"Произошла ошибка при получении списка чатов: {str(e)[:100]}...")
        await state.clear()

# Обработчик ввода ID чата/канала вручную
@router.message(ExportStates.waiting_for_id)
async def process_input_id(message: Message, state: FSMContext):
    target_id = message.text.strip()

    if not target_id:
        await message.answer("Пожалуйста, введите корректный ID канала/чата или юзернейм.")
        return

    # Запускаем процесс экспорта
    await start_export_process(message, state, target_id)

# Обработчик выбора чата из списка недавних
@router.callback_query(ExportStates.waiting_for_selection, F.data.startswith("export_dialog:"))
async def process_dialog_selection(callback: CallbackQuery, state: FSMContext):
    # Парсим данные из callback_data
    parts = callback.data.split(":", 3)
    if len(parts) < 3:
        await callback.answer("Некорректные данные. Попробуйте снова.")
        return

    dialog_id = parts[1]
    dialog_type = parts[2]
    dialog_title = parts[3] if len(parts) > 3 else f"ID: {dialog_id}"

    await callback.answer()  # Подтверждаем обработку CallbackQuery

    # Запускаем процесс экспорта
    await start_export_process(callback.message, state, dialog_id, dialog_title, dialog_type)

# Обработчик отмены экспорта
@router.callback_query(F.data == "export_cancel")
async def process_export_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Экспорт отменен")
    await callback.message.edit_text("Экспорт отменен. Используйте /export, чтобы начать заново.")
    await state.clear()

# Общая функция для запуска процесса экспорта
async def start_export_process(
    message: Message,
    state: FSMContext,
    target_id: str,
    target_name: Optional[str] = None,
    target_type: Optional[str] = "unknown"
):
    # Устанавливаем состояние экспорта
    await state.set_state(ExportStates.exporting)

    # Отправляем сообщение о начале экспорта
    if target_name:
        status_msg = await message.answer(f"Начинаю экспорт {target_type} {hbold(target_name)}...")
    else:

        status_msg = await message.answer(f"Начинаю экспорт для ID/username {hbold(target_id)}...")

    try:
        # Вызываем экспорт через ExporterService
        export_path = await exporter_service.trigger_export_for_target(
            target_id=target_id,
            target_name=target_name,
            target_type=target_type
        )

        if not export_path:
            await status_msg.edit_text(f"Не удалось выполнить экспорт для {hbold(target_name or target_id)}. Проверьте логи.")
            await state.clear()
            return

        await status_msg.edit_text(f"Экспорт для {hbold(target_name or target_id)} успешно завершен. Начинаю создание архива...")

        # Переходим к архивации
        await state.set_state(ExportStates.archiving)
        await state.update_data(export_path=export_path, target_name=target_name or target_id)

        # Запускаем процесс архивации
        asyncio.create_task(archive_and_send(message, status_msg.message_id, state))

    except Exception as e:
        logger.error(f"Error starting export process: {e}", exc_info=True)
        await status_msg.edit_text(f"Произошла ошибка при экспорте: {str(e)[:100]}...")
        await state.clear()

# Функция для архивации и отправки результатов
async def archive_and_send(message: Message, status_msg_id: int, state: FSMContext):
    try:
        # Получаем данные из состояния
        data = await state.get_data()
        export_path = data.get("export_path")
        target_name = data.get("target_name", "unknown")

        if not export_path or not os.path.exists(export_path):
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg_id,
                text=f"Ошибка: директория экспорта {hcode(export_path)} не существует."
            )
            await state.clear()
            return

        # Архивируем директорию
        archive_name_prefix = f"{DEFAULT_ARCHIVE_NAME_PREFIX}_{Path(export_path).name}"

        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg_id,
            text=f"Создаю архив для {hbold(target_name)}... Это может занять некоторое время."
        )

        # Создаем архивы асинхронно в отдельном потоке, чтобы не блокировать основной цикл
        archive_paths = await asyncio.to_thread(
            create_archive_from_directory,
            export_path,
            archive_name_prefix
        )

        if not archive_paths:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg_id,
                text=f"Не удалось создать архив для {hbold(target_name)}. Возможно, директория пуста."
            )
            await state.clear()
            return

        # Переходим к отправке
        await state.set_state(ExportStates.sending)

        # Информируем о количестве частей архива
        total_parts = len(archive_paths)
        if total_parts == 1:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg_id,
                text=f"Архив создан успешно. Отправляю архив для {hbold(target_name)}..."
            )
        else:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg_id,
                text=f"Архив создан успешно. Отправляю архив для {hbold(target_name)} в {total_parts} частях..."
            )

        # Отправляем архивы
        for i, archive_path in enumerate(archive_paths, 1):
            # Проверяем, что файл существует и не пустой
            if not os.path.exists(archive_path) or os.path.getsize(archive_path) == 0:
                logger.error(f"Archive file {archive_path} does not exist or is empty.")
                continue

            # Индикатор процесса для нескольких файлов
            if total_parts > 1:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=status_msg_id,
                    text=f"Отправка части {i}/{total_parts} архива для {hbold(target_name)}..."
                )

            # Отправляем файл
            try:
                # Создаем FSInputFile из пути к архиву
                file = FSInputFile(archive_path)

                # Отправляем файл
                await message.bot.send_document(
                    chat_id=message.chat.id,
                    document=file,
                    caption=f"Архив экспорта {hbold(target_name)}" + (f" (часть {i}/{total_parts})" if total_parts > 1 else "")
                )

            except Exception as e:
                logger.error(f"Error sending archive {archive_path}: {e}", exc_info=True)
                await message.bot.send_message(
                    chat_id=message.chat.id,
                    text=f"Ошибка при отправке архива {Path(archive_path).name}: {str(e)[:100]}..."
                )

        # Завершаем процесс
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=status_msg_id,
            text=f"Экспорт для {hbold(target_name)} успешно завершен и отправлен." +
                 (f" ({total_parts} архивов)" if total_parts > 1 else "")
        )

    except Exception as e:
        logger.error(f"Error in archive_and_send: {e}", exc_info=True)
        await message.bot.send_message(
            chat_id=message.chat.id,
            text=f"Произошла ошибка при архивации или отправке: {str(e)[:100]}..."
        )
    finally:
        # Очищаем состояние в любом случае
        await state.clear()

# Команда отмены текущей операции
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        await message.answer("Текущая операция отменена. Используйте /export, чтобы начать заново.")
    else:
        await message.answer("Нет активных операций для отмены.")

# Обработчик неизвестного состояния (на всякий случай)
@router.message(ExportStates.waiting_for_method)
@router.message(ExportStates.waiting_for_selection)
@router.message(ExportStates.exporting)
@router.message(ExportStates.archiving)
@router.message(ExportStates.sending)
async def process_unknown_state_input(message: Message, state: FSMContext):
    current_state = await state.get_state()

    if current_state == ExportStates.waiting_for_method.state:
        await message.answer("Пожалуйста, выберите метод из предложенных кнопок.")
    elif current_state == ExportStates.waiting_for_selection.state:
        await message.answer("Пожалуйста, выберите чат из предложенного списка.")
    elif current_state in [ExportStates.exporting.state, ExportStates.archiving.state, ExportStates.sending.state]:
        await message.answer(
            f"В данный момент идет процесс {hitalic('экспорта/архивации/отправки')}. "
            f"Пожалуйста, дождитесь его завершения или используйте /cancel для отмены."
        )
    else:
        # Неожиданное состояние, сбрасываем
        await state.clear()
        await message.answer("Произошла ошибка. Используйте /export, чтобы начать заново.")
