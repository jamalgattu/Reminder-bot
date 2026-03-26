# Telegram Reminder Bot

## Overview
A Telegram bot that allows users to set reminders for specific times. It combines a Telegram bot (using long polling) with a Flask web server for health checks and webhook support.

## Architecture
- **bot.py** — Main entry point. Initializes Telegram bot (polling), Flask app, command handlers, and reminder scheduling
- **db.py** — SQLite database layer (`reminders.db`) managing users and reminders
- **parser.py** — Utilities for timezone mapping and time string parsing
- **requirements.txt** — Python dependencies

## Stack
- **Language**: Python 3.12
- **Bot Framework**: python-telegram-bot v13.7 (v13 API style: Updater/Filters/CallbackContext)
- **Web Server**: Flask (port 5000, host 0.0.0.0)
- **Scheduler**: APScheduler 3.6.3
- **Database**: SQLite (reminders.db)
- **Key dependencies**: `urllib3<2` required for python-telegram-bot v13 compatibility with Python 3.12

## Bot Commands
- `/start` — Welcome message and command list
- `/set_timezone <tz>` — Set user's timezone (e.g., Asia/Kolkata)
- `/remind <HH:MM> <message>` — Schedule a reminder
- `/list` — List all active reminders
- `/delete <id>` — Delete a reminder by ID

## Configuration
- `TELEGRAM_BOT_TOKEN` — Set as environment variable (shared)
- `.env` file supported via python-dotenv for local development

## Deployment
- Deployment target: **vm** (always-running, needed for long polling)
- Run command: `python bot.py`
- Port: 5000

## Important Notes
- `python-telegram-bot==13.15` in the original `requirements.txt` is incompatible with Python 3.12 — downgraded to v13.7
- `urllib3<2` is pinned to ensure compatibility with python-telegram-bot v13's internal urllib3 usage
- APScheduler must be pinned to `3.6.3` (required by python-telegram-bot v13)
