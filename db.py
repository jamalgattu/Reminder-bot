# db.py
import sqlite3

DB_PATH = "reminders.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            timezone TEXT DEFAULT NULL
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER,
            message_id INTEGER,
            message TEXT,
            remind_at TEXT,
            job_id TEXT
        )
    ''')

    conn.commit()
    conn.close()

def save_user(user_id, timezone):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO users (user_id, timezone)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET timezone=excluded.timezone
    ''', (user_id, timezone))
    conn.commit()
    conn.close()

def get_user_timezone(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT timezone FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def save_reminder(chat_id, user_id, message_id, message, remind_at, job_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO reminders (chat_id, user_id, message_id, message, remind_at, job_id)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, user_id, message_id, message, remind_at, job_id))
    conn.commit()
    conn.close()

def get_reminders(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, message, remind_at FROM reminders
        WHERE chat_id = ?
    ''', (chat_id,))
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
