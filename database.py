# -*- coding: utf-8 -*-
"""
Простая база данных на SQLite (файл на диске, не требует установки сервера).
Хранит: пользователей Telegram и их детей (имя + дата рождения).
"""

import sqlite3
import os
from contextlib import contextmanager

# Путь к файлу базы данных. Если задана переменная окружения DB_PATH
# (например, указывает на подключённый Railway Volume) — используем её,
# иначе базу данных храним рядом с кодом (подходит только для локальных тестов,
# на Railway без Volume данные будут теряться при каждом новом деплое).
DB_PATH = os.getenv("DB_PATH", "privivki_bot.db")


def init_db():
    """Создаёт таблицы, если их ещё нет. Вызывается один раз при старте бота."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS children (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                birth_date TEXT NOT NULL,
                parent_name TEXT,
                parent_username TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS completed_vaccines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                child_id INTEGER NOT NULL,
                vaccine_id TEXT NOT NULL,
                completed_date TEXT NOT NULL,
                UNIQUE(child_id, vaccine_id)
            )
        """)
        # Миграция для баз, созданных до появления parent_name/parent_username —
        # SQLite не поддерживает "ADD COLUMN IF NOT EXISTS", поэтому просто
        # пробуем добавить колонку и молча пропускаем ошибку, если она уже есть.
        for column in ("parent_name", "parent_username"):
            try:
                conn.execute(f"ALTER TABLE children ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError:
                pass
        conn.commit()


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def add_child(telegram_user_id: int, name: str, birth_date: str, parent_name: str = None, parent_username: str = None):
    """birth_date в формате YYYY-MM-DD"""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO children (telegram_user_id, name, birth_date, parent_name, parent_username) "
            "VALUES (?, ?, ?, ?, ?)",
            (telegram_user_id, name, birth_date, parent_name, parent_username),
        )
        conn.commit()


def get_children(telegram_user_id: int):
    """Возвращает список детей конкретного пользователя."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id, name, birth_date FROM children WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        return cursor.fetchall()


def get_all_children():
    """Возвращает всех детей всех пользователей — нужно для планировщика напоминаний."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id, telegram_user_id, name, birth_date FROM children"
        )
        return cursor.fetchall()


def get_all_children_full():
    """Возвращает всех детей вместе с данными о родителе — для админ-панели клиники.
    (id, name, birth_date, parent_name, parent_username), отсортировано по дате добавления."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id, name, birth_date, parent_name, parent_username "
            "FROM children ORDER BY id"
        )
        return cursor.fetchall()


def delete_child(child_id: int, telegram_user_id: int):
    """Удаляет ребёнка вместе с его отметками о прививках. telegram_user_id — защита,
    чтобы пользователь мог удалить только своего ребёнка, а не чужого по id."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM completed_vaccines WHERE child_id = ? AND child_id IN "
            "(SELECT id FROM children WHERE id = ? AND telegram_user_id = ?)",
            (child_id, child_id, telegram_user_id),
        )
        conn.execute(
            "DELETE FROM children WHERE id = ? AND telegram_user_id = ?",
            (child_id, telegram_user_id),
        )
        conn.commit()


def delete_all_children(telegram_user_id: int):
    """Удаляет всех детей конкретного пользователя вместе с их отметками о прививках."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id FROM children WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        child_ids = [row[0] for row in cursor.fetchall()]
        if child_ids:
            placeholders = ",".join("?" for _ in child_ids)
            conn.execute(
                f"DELETE FROM completed_vaccines WHERE child_id IN ({placeholders})",
                child_ids,
            )
        conn.execute("DELETE FROM children WHERE telegram_user_id = ?", (telegram_user_id,))
        conn.commit()


def mark_vaccine_done(child_id: int, vaccine_id: str, completed_date: str):
    """Отмечает прививку как сделанную. Если уже отмечена — просто обновляет дату."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO completed_vaccines (child_id, vaccine_id, completed_date)
               VALUES (?, ?, ?)
               ON CONFLICT(child_id, vaccine_id)
               DO UPDATE SET completed_date = excluded.completed_date""",
            (child_id, vaccine_id, completed_date),
        )
        conn.commit()


def unmark_vaccine_done(child_id: int, vaccine_id: str):
    """Убирает отметку "сделано" (на случай, если отметили по ошибке)."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM completed_vaccines WHERE child_id = ? AND vaccine_id = ?",
            (child_id, vaccine_id),
        )
        conn.commit()


def get_completed_vaccine_ids(child_id: int):
    """Возвращает множество vaccine_id, которые уже отмечены сделанными у этого ребёнка."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT vaccine_id FROM completed_vaccines WHERE child_id = ?",
            (child_id,),
        )
        return {row[0] for row in cursor.fetchall()}


def get_completed_vaccines_with_dates(child_id: int):
    """Возвращает список (vaccine_id, completed_date) сделанных прививок этого ребёнка,
    отсортированный по дате выполнения."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT vaccine_id, completed_date FROM completed_vaccines "
            "WHERE child_id = ? ORDER BY completed_date",
            (child_id,),
        )
        return cursor.fetchall()
