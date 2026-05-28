import os
import asyncio
import sqlite3
import logging
from datetime import datetime, date, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiohttp import web  # Для health-check на Render

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # Токен из переменных окружения Render
TARGET_CHAT_ID = int(os.environ.get("CHAT_ID", "-1001234567890"))
REPORT_HOUR = int(os.environ.get("REPORT_HOUR", "10"))
DB_FILE = "countdown_bot.db"
PORT = int(os.environ.get("PORT", 8080))  # Порт для Render
# =============================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# 🔹 БД
def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            chat_id INTEGER PRIMARY KEY,
            target_date TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def get_target_date(chat_id: int):
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT target_date FROM settings WHERE chat_id=?", (chat_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_target_date(chat_id: int, date_str: str):
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (chat_id, target_date) VALUES (?, ?)", (chat_id, date_str))    conn.commit()
    conn.close()

def remove_target_date(chat_id: int):
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM settings WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()

# 🔹 Логика отсчёта
def calculate_status(date_dm: str) -> str:
    try:
        day, month = map(int, date_dm.split('-'))
        current_year = date.today().year
        target_this_year = date(current_year, month, day)
        today = date.today()
        delta = (target_this_year - today).days
        
        if delta > 0:
            return f"⏳ До {day}.{month} осталось **{delta}** дней"
        elif delta == 0:
            return f"🎉 **Сегодня {day}.{month}!**"
        else:
            return f"⌛ {day}.{month} было **{abs(delta)}** дней назад"
    except ValueError:
        return "❌ Ошибка в дате"

# 🔹 Рассылка
async def daily_broadcast(bot: Bot):
    target_date = get_target_date(TARGET_CHAT_ID)
    if not target_date:
        return
    
    try:
        text = calculate_status(target_date)
        await bot.send_message(TARGET_CHAT_ID, text, parse_mode=ParseMode.MARKDOWN)
        logging.info(f"Отчёт отправлен: {text}")
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")

# 🔹 Планировщик
async def start_scheduler(bot: Bot):
    while True:
        try:
            now = datetime.now()
            next_run = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
            if now >= next_run:
                next_run += timedelta(days=1)
                        sleep_sec = (next_run - now).total_seconds()
            logging.info(f"Следующая отправка через {sleep_sec:.0f} сек")
            await asyncio.sleep(sleep_sec)
            await daily_broadcast(bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Ошибка планировщика: {e}")
            await asyncio.sleep(60)  # Ждём минуту перед повтором

# 🔹 Health check для Render (чтобы не усыплял)
async def handle_health(request):
    return web.Response(text="OK")

async def init_web_app():
    app = web.Application()
    app.router.add_get('/health', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"Health-check запущен на порту {PORT}")

# 🔹 Обработчики команд
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот-календарь.\n\n"
        "📌 Команды:\n"
        "• `/set_date DD-MM` — установить дату\n"
        "• `/status` — показать отсчёт\n"
        "• `/remove` — удалить дату"
    )

@dp.message(Command("set_date"))
async def cmd_set_date(message: Message):
    args = message.text.split()
    if len(args) != 2:
        return await message.answer("❌ Формат: `/set_date DD-MM`")
    
    date_str = args[1]
    try:
        day, month = map(int, date_str.split('-'))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            raise ValueError
    except ValueError:
        return await message.answer(" Неверный формат. Пример: `15-03`")
        set_target_date(TARGET_CHAT_ID, date_str)
    await message.answer(f"✅ Дата: **{date_str}**")
    await daily_broadcast(message.bot)

@dp.message(Command("status"))
async def cmd_status(message: Message):
    d = get_target_date(TARGET_CHAT_ID)
    if not d:
        return await message.answer("📭 Дата не установлена")
    await message.answer(calculate_status(d), parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("remove"))
async def cmd_remove(message: Message):
    remove_target_date(TARGET_CHAT_ID)
    await message.answer("🗑️ Дата удалена")

# 🔹 Запуск
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    
    # Проверка подключения
    try:
        me = await bot.get_me()
        logging.info(f"✓ Бот @{me.username} подключён")
    except Exception as e:
        logging.error(f"✗ Не удалось подключиться: {e}")
        return
    
    # Запускаем задачи
    asyncio.create_task(start_scheduler(bot))
    asyncio.create_task(init_web_app())
    
    # Polling с авто-переподключением
    while True:
        try:
            logging.info("Запуск polling...")
            await dp.start_polling(bot)
        except Exception as e:
            logging.error(f"Ошибка polling: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())