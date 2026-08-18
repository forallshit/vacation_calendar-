# -*- coding: utf-8 -*-
"""
Планировщик: раз в день проверяет всех детей всех пользователей
и отправляет напоминание, если прививка через 3 дня или сегодня.
"""

import logging
from datetime import datetime, date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

import database
import vaccines

# За сколько дней до прививки присылать напоминание
REMIND_DAYS_BEFORE = [14, 3, 0]  # за 2 недели, за 3 дня и в день прививки

# Во сколько по времени сервера проверять и слать напоминания (24-часовой формат)
CHECK_HOUR = 9
CHECK_MINUTE = 0


async def check_and_notify(bot: Bot):
    """Проверяет всех детей и рассылает напоминания тем, у кого прививка скоро."""
    today = date.today()
    all_children = database.get_all_children()

    for child_id, telegram_user_id, name, birth_date_str in all_children:
        birth_date = datetime.strptime(birth_date_str, "%Y-%m-%d").date()
        schedule = vaccines.get_vaccines_for_child(birth_date, today)
        completed_ids = database.get_completed_vaccine_ids(child_id)

        for v in schedule:
            if v["id"] in completed_ids:
                continue  # уже отмечено сделанным — не напоминаем
            if v["days_left"] in REMIND_DAYS_BEFORE:
                if v["days_left"] == 0:
                    when_text = "СЕГОДНЯ"
                elif v["days_left"] == 14:
                    when_text = f"через 2 недели, {v['due_date'].strftime('%d.%m.%Y')}"
                else:
                    when_text = f"через {v['days_left']} дн. ({v['due_date'].strftime('%d.%m.%Y')})"

                text = (
                    f"🔔 Напоминание про {name}\n\n"
                    f"{v['name']} — {when_text}\n"
                    f"{v['info']}"
                )
                try:
                    await bot.send_message(telegram_user_id, text)
                except Exception as e:
                    logging.warning(f"Не удалось отправить напоминание {telegram_user_id}: {e}")


def setup_scheduler(bot: Bot):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_and_notify,
        trigger="cron",
        hour=CHECK_HOUR,
        minute=CHECK_MINUTE,
        args=[bot],
    )
    scheduler.start()
    logging.info(f"Планировщик запущен, проверка каждый день в {CHECK_HOUR}:{CHECK_MINUTE:02d}")
