import asyncio
import logging
from datetime import datetime
from typing import Dict
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import os 
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TOKEN")
ADMIN_USERNAME = "walletgitler"
ADMIN_ID = 5907622429

DEFAULT_DURATION = 90
DB_PATH = "bot_database.db"

EXCLUDED_PREFIXES = ["@bot", "@admin", "@moder", "Bot", "Admin", "[bot]"]

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_games (
                chat_id INTEGER PRIMARY KEY,
                duration INTEGER,
                prize TEXT,
                prize_type TEXT,
                is_active BOOLEAN DEFAULT 1
            )
        """)
        await db.commit()

async def save_game(chat_id: int, duration: int, prize: str, prize_type: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO active_games (chat_id, duration, prize, prize_type, is_active)
            VALUES (?, ?, ?, ?, 1)
        """, (chat_id, duration, prize, prize_type))
        await db.commit()

async def get_game(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM active_games WHERE chat_id = ? AND is_active = 1", (chat_id,))
        return await cursor.fetchone()

async def end_game(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE active_games SET is_active = 0 WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def is_game_active(chat_id: int) -> bool:
    game = await get_game(chat_id)
    return game is not None

def has_excluded_prefix(name: str) -> bool:
    if not name:
        return False
    name_lower = name.lower()
    for prefix in EXCLUDED_PREFIXES:
        if name_lower.startswith(prefix.lower()):
            return True
    return False

def get_admin_menu():
    return InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("Создать ивент", callback_data="admin:create"),
        InlineKeyboardButton("Активные ивенты", callback_data="admin:list"),
        InlineKeyboardButton("Закрыть", callback_data="admin:close")
    )

def get_duration_keyboard():
    return InlineKeyboardMarkup(row_width=3).add(
        InlineKeyboardButton("30 сек", callback_data="dur:30"),
        InlineKeyboardButton("60 сек", callback_data="dur:60"),
        InlineKeyboardButton("90 сек", callback_data="dur:90"),
        InlineKeyboardButton("120 сек", callback_data="dur:120"),
        InlineKeyboardButton("180 сек", callback_data="dur:180"),
        InlineKeyboardButton("Своё", callback_data="dur:custom")
    )

def get_prize_type_keyboard():
    return InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("Telegram Stars", callback_data="prize:stars"),
        InlineKeyboardButton("NFT Подарок", callback_data="prize:nft"),
        InlineKeyboardButton("Ссылка", callback_data="prize:link"),
        InlineKeyboardButton("Без приза", callback_data="prize:none")
    )

def get_launch_keyboard(chat_id: int):
    return InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("ЗАПУСТИТЬ", callback_data=f"launch:{chat_id}"),
        InlineKeyboardButton("Назад", callback_data="back:main")
    )

class EventCreation(StatesGroup):
    waiting_for_duration = State()
    waiting_for_prize = State()
    waiting_for_group = State()

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
active_timers: Dict[int, asyncio.Task] = {}
user_data = {}

async def game_timer(chat_id: int, duration: int, prize: str, prize_type: str):
    try:
        await bot.send_message(
            chat_id,
            f"""
ИВЕНТ НАЧАЛСЯ

1 сообщение = 19 звёзд
Длительность: {duration} сек
Пишите любые сообщения

ПОЕХАЛИ
"""
        )
        
        await asyncio.sleep(duration)
        await end_game(chat_id)
        
        await bot.send_message(
            chat_id,
            f"""
ИВЕНТ ЗАВЕРШЁН

Время вышло

Приз: {prize if prize else 'Не указан'}

Победителя выберет @{ADMIN_USERNAME}
"""
        )
            
    except asyncio.CancelledError:
        await end_game(chat_id)
        await bot.send_message(chat_id, "Ивент остановлен")

async def cmd_start(message: types.Message):
    if message.chat.type == "private":
        if message.from_user.id == ADMIN_ID or message.from_user.username == ADMIN_USERNAME:
            await message.reply(f"Привет, админ @{ADMIN_USERNAME}\n/admin - управление")
        else:
            await message.reply("Привет\nБот для ивентов в группах")

async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID and message.from_user.username != ADMIN_USERNAME:
        await message.reply("Нет прав")
        return
        
    await message.reply(
        "Панель управления",
        reply_markup=get_admin_menu()
    )

async def create_event(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID and callback.from_user.username != ADMIN_USERNAME:
        await callback.answer("Нет прав", show_alert=True)
        return
    
    user_data[callback.from_user.id] = {}
    
    await callback.message.edit_text(
        "Выберите длительность",
        reply_markup=get_duration_keyboard()
    )
    await EventCreation.waiting_for_duration.set()
    await callback.answer()

async def process_duration(callback: types.CallbackQuery, state: FSMContext):
    data = callback.data.split(':')[1]
    
    if data == "custom":
        await callback.message.edit_text("Введите длительность в секундах (10-600)")
        await callback.answer()
        return
    
    duration = int(data)
    user_data[callback.from_user.id]['duration'] = duration
    
    await callback.message.edit_text(
        f"Длительность: {duration} сек\n\nВыберите приз",
        reply_markup=get_prize_type_keyboard()
    )
    await EventCreation.waiting_for_prize.set()
    await callback.answer()

async def process_custom_duration(message: types.Message, state: FSMContext):
    try:
        duration = int(message.text)
        if 10 <= duration <= 600:
            user_data[message.from_user.id]['duration'] = duration
            await message.reply(
                f"Длительность: {duration} сек\n\nВыберите приз",
                reply_markup=get_prize_type_keyboard()
            )
            await EventCreation.waiting_for_prize.set()
        else:
            await message.reply("От 10 до 600 секунд")
    except ValueError:
        await message.reply("Введите число")

async def process_prize_type(callback: types.CallbackQuery, state: FSMContext):
    prize_type = callback.data.split(':')[1]
    user_data[callback.from_user.id]['prize_type'] = prize_type
    
    if prize_type == "none":
        user_data[callback.from_user.id]['prize'] = "Без приза"
        await show_groups(callback)
        return
    
    messages = {
        'stars': "Отправьте количество звёзд (например 100)",
        'nft': "Отправьте ссылку на NFT",
        'link': "Отправьте ссылку или описание"
    }
    
    await callback.message.edit_text(messages[prize_type])
    await callback.answer()

async def process_prize_value(message: types.Message, state: FSMContext):
    user_data[message.from_user.id]['prize'] = message.text
    await show_groups(message)
    await EventCreation.waiting_for_group.set()

async def show_groups(event):
    text = """
Отправьте ID группы

1. Добавьте бота в группу
2. Отправьте в группе /id
3. Скопируйте число и отправьте сюда
"""
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Назад", callback_data="back:main"))
    
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb)
        await event.answer()
    else:
        await event.reply(text)

async def process_group_id(message: types.Message, state: FSMContext):
    try:
        chat_id = int(message.text)
        
        try:
            await bot.send_chat_action(chat_id, "typing")
        except:
            await message.reply("Бот не в группе или не админ")
            return
        
        data = user_data[message.from_user.id]
        data['chat_id'] = chat_id
        
        await state.finish()
        
        await message.reply(
            f"""
Ивент готов

Группа: {chat_id}
Длительность: {data['duration']} сек
Приз: {data['prize']}
""",
            reply_markup=get_launch_keyboard(chat_id)
        )
        
    except ValueError:
        await message.reply("Отправьте число")

async def launch_in_group(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID and callback.from_user.username != ADMIN_USERNAME:
        await callback.answer("Нет прав", show_alert=True)
        return
    
    chat_id = int(callback.data.split(':')[1])
    data = user_data.get(callback.from_user.id, {})
    
    duration = data.get('duration', DEFAULT_DURATION)
    prize = data.get('prize', 'Не указан')
    prize_type = data.get('prize_type', 'none')
    
    await save_game(chat_id, duration, prize, prize_type)
    
    if chat_id in active_timers:
        active_timers[chat_id].cancel()
    
    task = asyncio.create_task(game_timer(chat_id, duration, prize, prize_type))
    active_timers[chat_id] = task
    
    try:
        await bot.send_message(
            chat_id,
            f"""
АДМИН @{ADMIN_USERNAME} ЗАПУСТИЛ ИВЕНТ

19 звёзд за сообщение
{duration} секунд

ПОЕХАЛИ
"""
        )
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"Ивент запущен в группе {chat_id}",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("В меню", callback_data="back:main")
        )
    )
    await callback.answer("Запущен")

async def list_active_events(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID and callback.from_user.username != ADMIN_USERNAME:
        await callback.answer("Нет прав", show_alert=True)
        return
    
    text = "Активные ивенты\n\n"
    has_active = False
    
    for chat_id, timer in active_timers.items():
        if not timer.done():
            has_active = True
            text += f"Группа {chat_id}\n"
    
    if not has_active:
        text += "Нет активных ивентов"
    
    await callback.message.edit_text(text)
    await callback.answer()

async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await callback.message.edit_text(
        "Панель управления",
        reply_markup=get_admin_menu()
    )
    await callback.answer()

async def close_menu(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()

async def on_group_message(message: types.Message):
    if not await is_game_active(message.chat.id):
        return
    
    user_name = message.from_user.username or message.from_user.first_name or ""
    if has_excluded_prefix(user_name):
        return

async def cmd_id(message: types.Message):
    await message.reply(f"ID группы: {message.chat.id}")

async def cmd_stop(message: types.Message):
    if message.from_user.id != ADMIN_ID and message.from_user.username != ADMIN_USERNAME:
        return
    
    if message.chat.id in active_timers:
        active_timers[message.chat.id].cancel()
        del active_timers[message.chat.id]
        await message.reply("Ивент остановлен")

def register_handlers():
    dp.register_message_handler(cmd_start, commands=['start'])
    dp.register_message_handler(cmd_admin, commands=['admin', 'panel'])
    
    dp.register_callback_query_handler(create_event, text="admin:create")
    dp.register_callback_query_handler(list_active_events, text="admin:list")
    dp.register_callback_query_handler(close_menu, text="admin:close")
    dp.register_callback_query_handler(back_to_main, text_startswith="back:", state="*")
    
    dp.register_callback_query_handler(process_duration, text_startswith="dur:", state=EventCreation.waiting_for_duration)
    dp.register_message_handler(process_custom_duration, state=EventCreation.waiting_for_duration)
    dp.register_callback_query_handler(process_prize_type, text_startswith="prize:", state=EventCreation.waiting_for_prize)
    dp.register_message_handler(process_prize_value, state=EventCreation.waiting_for_prize)
    dp.register_message_handler(process_group_id, state=EventCreation.waiting_for_group)
    
    dp.register_callback_query_handler(launch_in_group, text_startswith="launch:")
    
    dp.register_message_handler(cmd_id, commands=['id'])
    dp.register_message_handler(cmd_stop, commands=['stop'])
    dp.register_message_handler(on_group_message, content_types=types.ContentTypes.ANY)

async def on_startup(dp):
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    
    try:
        await bot.send_message(ADMIN_ID, "Бот запущен\n/admin - управление")
    except:
        pass
    
    logging.info("Бот запущен")

async def on_shutdown(dp):
    for task in active_timers.values():
        task.cancel()
    await bot.close()

if __name__ == '__main__':
    register_handlers()
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)

# сделано @walletgitler
