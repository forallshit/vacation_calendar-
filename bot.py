# -*- coding: utf-8 -*-
"""
Главный файл бота-напоминалки о прививках.

Как запустить:
1. pip install -r requirements.txt
2. Вставь свой токен в переменную BOT_TOKEN ниже (или задай через переменную окружения BOT_TOKEN)
3. python bot.py

Подробности — в README.md
"""

import asyncio
import logging
import os
from datetime import datetime, date

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

import database
import vaccines
from scheduler import setup_scheduler

# ==== НАСТРОЙКИ ====
# Вставь сюда токен, который дал @BotFather, ИЛИ задай переменную окружения BOT_TOKEN
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_СЮДА_СВОЙ_ТОКЕН")

logging.basicConfig(level=logging.INFO)

router = Router()


# ==== Постоянное меню с кнопками внизу экрана ====
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👶 Мои дети")],
        [KeyboardButton(text="➕ Добавить ребёнка"), KeyboardButton(text="✅ Отметить прививку")],
    ],
    resize_keyboard=True,
)


# ==== Состояния диалога добавления ребёнка (FSM — машина состояний) ====
class AddChild(StatesGroup):
    waiting_for_name = State()
    waiting_for_birth_date = State()


# ==== Команда /start ====
@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "Привет! Я помогу не пропустить прививки твоего ребёнка по "
        "национальному календарю РФ.\n\n"
        "👶 Мои дети — выбери ребёнка и посмотри его прививки\n"
        "➕ Добавить ребёнка\n"
        "✅ Отметить прививку сделанной"
    )
    await message.answer(text, reply_markup=main_menu)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Я слежу за графиком прививок по датам рождения детей, которых ты добавишь.\n"
        "Раз в день проверяю, у кого скоро прививка, и присылаю напоминание заранее.\n\n"
        "👶 Мои дети — выбери ребёнка, дальше увидишь его предстоящие и уже "
        "поставленные прививки. У каждой прививки — описание, от чего она и "
        "почему важна.\n"
        "➕ Добавить ребёнка\n"
        "✅ Отметить прививку сделанной (чтобы не напоминал зря)",
        reply_markup=main_menu,
    )


# ==== Добавление ребёнка: шаг 1 — имя ====
@router.message(Command("add"))
@router.message(F.text == "➕ Добавить ребёнка")
async def cmd_add(message: Message, state: FSMContext):
    await message.answer(
        "Как зовут малыша?", reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AddChild.waiting_for_name)


@router.message(AddChild.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer(
        "Дата рождения в формате ДД.ММ.ГГГГ (например, 15.03.2024):"
    )
    await state.set_state(AddChild.waiting_for_birth_date)


# ==== Добавление ребёнка: шаг 2 — дата рождения ====
@router.message(AddChild.waiting_for_birth_date)
async def process_birth_date(message: Message, state: FSMContext):
    raw = message.text.strip()
    try:
        birth_date = datetime.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "Не получилось распознать дату. Введи в формате ДД.ММ.ГГГГ, "
            "например: 15.03.2024"
        )
        return

    if birth_date > date.today():
        await message.answer("Дата рождения не может быть в будущем. Попробуй ещё раз.")
        return

    data = await state.get_data()
    name = data["name"]

    database.add_child(
        telegram_user_id=message.from_user.id,
        name=name,
        birth_date=birth_date.isoformat(),
    )
    await state.clear()

    await message.answer(
        f"Готово! Добавил(а) {name}, дата рождения {raw}.\n"
        f"Посмотреть прививки — кнопка «👶 Мои дети»",
        reply_markup=main_menu,
    )


# ==== Вспомогательное: клавиатура со списком детей ====
def build_children_keyboard(children):
    buttons = [
        [InlineKeyboardButton(text=f"👶 {name}", callback_data=f"childmenu:{child_id}")]
        for child_id, name, _ in children
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==== Список детей — тап открывает меню ребёнка ====
@router.message(Command("children"))
@router.message(F.text == "👶 Мои дети")
async def cmd_children(message: Message):
    children = database.get_children(message.from_user.id)

    if not children:
        await message.answer(
            "У тебя пока нет добавленных детей. Добавь через «➕ Добавить ребёнка»",
            reply_markup=main_menu,
        )
        return

    await message.answer("Выбери ребёнка:", reply_markup=build_children_keyboard(children))


@router.callback_query(F.data == "backchildren")
async def on_back_children(callback: CallbackQuery):
    children = database.get_children(callback.from_user.id)

    if not children:
        await callback.message.edit_text(
            "У тебя пока нет добавленных детей. Добавь через «➕ Добавить ребёнка»"
        )
        await callback.answer()
        return

    await callback.message.edit_text("Выбери ребёнка:", reply_markup=build_children_keyboard(children))
    await callback.answer()


# ==== Меню ребёнка: предстоящие / поставленные ====
@router.callback_query(F.data.startswith("childmenu:"))
async def on_child_menu(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    children = database.get_children(callback.from_user.id)
    name = next((n for cid, n, _ in children if cid == child_id), None)

    if name is None:
        await callback.answer("Не нашёл такого ребёнка", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💉 Предстоящие прививки", callback_data=f"upcoming:{child_id}")],
        [InlineKeyboardButton(text="✅ Поставленные", callback_data=f"donelist:{child_id}")],
        [InlineKeyboardButton(text="⬅️ К списку детей", callback_data="backchildren")],
    ])
    await callback.message.edit_text(f"👶 <b>{name}</b>\nЧто посмотреть?", reply_markup=keyboard)
    await callback.answer()


# ==== Предстоящие прививки конкретного ребёнка (список кнопками) ====
@router.callback_query(F.data.startswith("upcoming:"))
async def on_upcoming(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    children = database.get_children(callback.from_user.id)
    match = next(((n, b) for cid, n, b in children if cid == child_id), None)

    if match is None:
        await callback.answer("Не нашёл такого ребёнка", show_alert=True)
        return

    name, birth_date_str = match
    birth_date = datetime.strptime(birth_date_str, "%Y-%m-%d").date()
    schedule = vaccines.get_vaccines_for_child(birth_date, date.today())
    completed_ids = database.get_completed_vaccine_ids(child_id)

    not_done = [v for v in schedule if v["id"] not in completed_ids]
    not_done.sort(key=lambda v: v["days_left"])

    back_button = InlineKeyboardButton(text="⬅️ Назад", callback_data=f"childmenu:{child_id}")

    if not not_done:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])
        await callback.message.edit_text(
            f"У {name} все прививки по графику уже поставлены. 🎉", reply_markup=keyboard
        )
        await callback.answer()
        return

    buttons = []
    for v in not_done[:10]:
        if v["days_left"] < 0:
            icon = "⚪️"
        elif v["days_left"] == 0:
            icon = "🔴"
        elif v["days_left"] <= 7:
            icon = "🟡"
        else:
            icon = "⚪️"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {v['name']}",
            callback_data=f"vaccinedetail:{v['id']}:{child_id}",
        )])
    buttons.append([back_button])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        f"💉 <b>{name}</b> — предстоящие прививки:\nВыбери прививку, чтобы узнать подробности.",
        reply_markup=keyboard,
    )
    await callback.answer()


# ==== Карточка одной прививки: описание + кнопка "Поставили" ====
@router.callback_query(F.data.startswith("vaccinedetail:"))
async def on_vaccine_detail(callback: CallbackQuery):
    _, vaccine_id, child_id_str = callback.data.split(":")
    child_id = int(child_id_str)
    vaccine = vaccines.get_vaccine_by_id(vaccine_id)

    if vaccine is None:
        await callback.answer("Не нашёл информацию об этой прививке", show_alert=True)
        return

    text = f"💉 <b>{vaccine['name']}</b>\n\n{vaccine['info']}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Поставили", callback_data=f"markdone:{child_id}:{vaccine_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"upcoming:{child_id}")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# ==== Поставленные прививки конкретного ребёнка ====
@router.callback_query(F.data.startswith("donelist:"))
async def on_done_list(callback: CallbackQuery):
    child_id = int(callback.data.split(":")[1])
    children = database.get_children(callback.from_user.id)
    name = next((n for cid, n, _ in children if cid == child_id), None)

    if name is None:
        await callback.answer("Не нашёл такого ребёнка", show_alert=True)
        return

    back_button = InlineKeyboardButton(text="⬅️ Назад", callback_data=f"childmenu:{child_id}")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])

    completed = database.get_completed_vaccines_with_dates(child_id)
    if not completed:
        await callback.message.edit_text(
            f"У {name} пока нет поставленных прививок.", reply_markup=keyboard
        )
        await callback.answer()
        return

    lines = [f"✅ <b>{name}</b> — поставленные прививки:\n"]
    for vaccine_id, completed_date in completed:
        vaccine_name = vaccines.get_vaccine_name_by_id(vaccine_id)
        date_formatted = datetime.strptime(completed_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        lines.append(f"• {vaccine_name} — {date_formatted}")

    await callback.message.edit_text("\n".join(lines), reply_markup=keyboard)
    await callback.answer()


# ==== Отметить прививку сделанной — быстрый способ через кнопку меню ====
@router.message(Command("done"))
@router.message(F.text == "✅ Отметить прививку")
async def cmd_done(message: Message):
    children = database.get_children(message.from_user.id)

    if not children:
        await message.answer(
            "У тебя пока нет добавленных детей. Добавь через «➕ Добавить ребёнка»",
            reply_markup=main_menu,
        )
        return

    if len(children) == 1:
        child_id, name, _ = children[0]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💉 Предстоящие прививки", callback_data=f"upcoming:{child_id}")
        ]])
        await message.answer(f"👶 <b>{name}</b>", reply_markup=keyboard)
    else:
        await message.answer("Какого ребёнка?", reply_markup=build_children_keyboard(children))


# ==== Отметить прививку сделанной (сама отметка) ====
@router.callback_query(F.data.startswith("markdone:"))
async def on_mark_done(callback: CallbackQuery):
    _, child_id_str, vaccine_id = callback.data.split(":")
    child_id = int(child_id_str)

    database.mark_vaccine_done(
        child_id=child_id,
        vaccine_id=vaccine_id,
        completed_date=date.today().isoformat(),
    )

    vaccine_name = vaccines.get_vaccine_name_by_id(vaccine_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ К прививкам ребёнка", callback_data=f"childmenu:{child_id}")
    ]])
    await callback.message.edit_text(f"✅ Отмечено: {vaccine_name}", reply_markup=keyboard)
    await callback.answer("Готово!")


async def main():
    database.init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Планировщик, который раз в день шлёт напоминания (см. scheduler.py)
    setup_scheduler(bot)

    logging.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
