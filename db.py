# db.py
import sqlite3
import os

DB_PATH = "reminders.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Users table — stores chat_id and timezone
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            timezone TEXT DEFAULT 'Asia/Kolkata'
        )
    ''')

    # Reminders table
    c.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            message TEXT,
            remind_at TEXT,
            job_id TEXT,
            FOREIGN KEY(chat_id) REFERENCES users(chat_id)
        )
    ''')

    conn.commit()
    conn.close()

def save_user(chat_id, timezone):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO users (chat_id, timezone)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET timezone=excluded.timezone
    ''', (chat_id, timezone))
    conn.commit()
    conn.close()

def get_user_timezone(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT timezone FROM users WHERE chat_id = ?', (chat_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 'Asia/Kolkata'

def save_reminder(chat_id, message, remind_at, job_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO reminders (chat_id, message, remind_at, job_id)
        VALUES (?, ?, ?, ?)
    ''', (chat_id, message, remind_at, job_id))
    conn.commit()
    conn.close()

def get_reminders(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, message, remind_at FROM reminders WHERE chat_id = ?', (chat_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_reminder(reminder_id, chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT job_id FROM reminders WHERE id = ? AND chat_id = ?', (reminder_id, chat_id))
    row = c.fetchone()
    if row:
        c.execute('DELETE FROM reminders WHERE id = ?', (reminder_id,))
        conn.commit()
    conn.close()
    return row[0] if row else None
