import asyncio
import sqlite3
import aiohttp
import logging
import io
import random
import textwrap
import os

from PIL import Image, ImageDraw, ImageFont
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from deep_translator import GoogleTranslator

# --- НАСТРОЙКИ ---
API_TOKEN = os.getenv("API_TOKEN")  # токен из переменной окружения

if not API_TOKEN:
    raise RuntimeError("API_TOKEN не задан в переменных окружения")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
db = sqlite3.connect('facts_bot.db')
cur = db.cursor()
cur.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, lang TEXT)')
cur.execute('CREATE TABLE IF NOT EXISTS seen_facts (user_id INTEGER, fact_id TEXT)')
db.commit()

# --- СПИСОК ЛОЖНЫХ ФАКТОВ ---
FAKE_FACTS_RU = [
    "Великую Китайскую стену видно с Луны невооруженным глазом.",
    "Золотые рыбки помнят события только три секунды.",
    "Быков в ярость приводит именно красный цвет тряпки.",
    "Человек задействует свой мозг только на 10 процентов.",
    "Хамелеоны меняют цвет исключительно ради маскировки.",
    "В космосе абсолютно нет гравитации.",
    "Летучие мыши полностью слепы."
]

# --- ГЕНЕРАЦИЯ КАРТИНКИ ---
def create_fact_image(fact_text, is_quiz=False):
    width, height = 1080, 1080
    top_color = (60, 20, 80) if is_quiz else (40, 60, 120)
    bottom_color = (20, 10, 30) if is_quiz else (10, 10, 20)

    base = Image.new('RGB', (width, height), (20, 20, 30))
    draw = ImageDraw.Draw(base)
    for i in range(height):
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * (i / height))
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * (i / height))
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * (i / height))
        draw.line([(0, i), (width, i)], fill=(r, g, b))

    try:
        font = ImageFont.truetype("arial.ttf", 45)
        title_font = ImageFont.truetype("arial.ttf", 70)
    except:
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()

    if is_quiz:
        draw.text((width/2 - 280, 150), "ПРАВДА ИЛИ ЛОЖЬ?", font=title_font, fill=(255, 215, 0))

    wrapper = textwrap.TextWrapper(width=35)
    lines = wrapper.wrap(text=fact_text)
    full_text = "\n".join(lines)

    w, h = draw.multiline_textbbox((0, 0), full_text, font=font, spacing=20)[2:]
    draw.multiline_text(
        ((width - w) / 2, (height - h) / 2),
        full_text,
        font=font,
        fill=(255, 255, 255),
        align="center",
        spacing=20
    )

    byte_arr = io.BytesIO()
    base.save(byte_arr, format='PNG')
    byte_arr.seek(0)
    return byte_arr

# --- ЛОГИКА ---
async def get_unique_fact(user_id, lang):
    url = "https://uselessfacts.jsph.pl/api/v2/facts/random"
    async with aiohttp.ClientSession() as session:
        for _ in range(5):
            async with session.get(url) as resp:
                data = await resp.json()
                f_id, text = data['id'], data['text']
                cur.execute(
                    "SELECT 1 FROM seen_facts WHERE user_id=? AND fact_id=?",
                    (user_id, f_id)
                )
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO seen_facts VALUES (?, ?)",
                        (user_id, f_id)
                    )
                    db.commit()
                    return GoogleTranslator(source='auto', target='ru').translate(text) if lang == 'ru' else text
        return "Факты временно закончились!"

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardBuilder()
    kb.add(types.InlineKeyboardButton(text="Русский 🇷🇺", callback_data="setlang_ru"))
    kb.add(types.InlineKeyboardButton(text="English 🇺🇸", callback_data="setlang_en"))
    await message.answer("Выбери язык / Choose language:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("setlang_"))
async def set_lang(call: types.CallbackQuery):
    lang = call.data.split('_')[1]
    cur.execute("INSERT OR REPLACE INTO users (id, lang) VALUES (?, ?)", (call.from_user.id, lang))
    db.commit()
    kb = ReplyKeyboardBuilder()
    kb.button(text="Интересный факт 💡")
    kb.button(text="Викторина ❓")
    await call.message.answer(
        "Готово! Используй кнопки или команды /fact и /quiz",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )
    await call.answer()

@dp.message(F.text.in_(["Интересный факт 💡", "Interesting fact 💡"]))
@dp.message(Command("fact"))
async def send_fact(message: types.Message):
    cur.execute("SELECT lang FROM users WHERE id=?", (message.from_user.id,))
    res = cur.fetchone()
    lang = res[0] if res else 'ru'

    wait_msg = await message.answer("⌛ Генерирую...")
    fact_text = await get_unique_fact(message.from_user.id, lang)
    img_data = create_fact_image(fact_text, is_quiz=False)

    kb = InlineKeyboardBuilder()
    kb.button(text="↗️ Поделиться", switch_inline_query=fact_text)

    photo = BufferedInputFile(img_data.getvalue(), filename="fact.png")
    await bot.send_photo(message.chat.id, photo=photo, caption=fact_text, reply_markup=kb.as_markup())
    await wait_msg.delete()

@dp.message(F.text.in_(["Викторина ❓", "Quiz ❓"]))
@dp.message(Command("quiz"))
async def start_quiz(message: types.Message):
    is_true = random.choice([True, False])
    fact_text = (await get_unique_fact(message.from_user.id, 'ru')) if is_true else random.choice(FAKE_FACTS_RU)
    img_data = create_fact_image(fact_text, is_quiz=True)

    kb = InlineKeyboardBuilder()
    correct_str = "true" if is_true else "false"
    kb.add(types.InlineKeyboardButton(text="Правда ✅", callback_data=f"quiz_{correct_str}_true"))
    kb.add(types.InlineKeyboardButton(text="Ложь ❌", callback_data=f"quiz_{correct_str}_false"))

    photo = BufferedInputFile(img_data.getvalue(), filename="quiz.png")
    await message.answer_photo(photo=photo, caption="Правда или ложь?", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("quiz_"))
async def check_quiz(call: types.CallbackQuery):
    _, correct, user_choice = call.data.split("_")
    result = "✅ Верно!" if correct == user_choice else "❌ Ошибка!"

    kb = InlineKeyboardBuilder()
    kb.button(text="↗️ Поделиться", switch_inline_query=f"Викторина: {call.message.caption}")

    await call.message.edit_caption(
        caption=f"{result}\n\nЭто был {'правдивый факт' if correct == 'true' else 'миф'}.",
        reply_markup=kb.as_markup()
    )
    await call.answer()

# --- ЗАПУСК ---
async def main():
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
