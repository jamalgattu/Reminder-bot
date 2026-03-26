# bot.py
import logging
import uuid
from datetime import datetime

import pytz
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from dotenv import load_dotenv
import os

from db import (
    init_db,
    save_user,
    get_user_timezone,
    save_reminder,
    get_reminders,
    delete_reminder,
)
from parser import (
    get_timezone_for_country,
    parse_reminder_time,
    split_time_and_message,
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///jobs.db')
}
scheduler = AsyncIOScheduler(jobstores=jobstores)

ASKING_COUNTRY = 1
ASKING_REGION = 2

pending_regions = {}


# ─── Reminder sender ─────────────────────────────────────────────────────────

async def send_reminder(chat_id: int, message: str, reminder_id: int):
    from telegram import Bot
    bot = Bot(token=TOKEN)
    await bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Reminder: {message}"
    )
    delete_reminder(reminder_id, chat_id)


# ─── /start ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tz = get_user_timezone(chat_id)

    if tz:
        await update.message.reply_text(
            f"👋 Welcome back!\n"
            f"Your timezone is set to *{tz}*.\n\n"
            f"Use /remind to set a reminder.\n"
            f"Use /settimezone to change your timezone.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "👋 Welcome to Reminder Bot!\n\n"
        "First, which country do you live in?\n"
        "_(e.g. India, USA, Nepal, UK)_",
        parse_mode="Markdown"
    )
    return ASKING_COUNTRY


# ─── /settimezone ────────────────────────────────────────────────────────────

async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌍 Which country do you live in?\n"
        "_(e.g. India, USA, Nepal, UK)_",
        parse_mode="Markdown"
    )
    return ASKING_COUNTRY


# ─── Conversation: country input ─────────────────────────────────────────────

async def receive_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    country = update.message.text.strip().lower()

    tz, needs_followup, options = get_timezone_for_country(country)

    if needs_followup:
        pending_regions[chat_id] = options
        option_list = "\n".join([f"• {k.title()}" for k in options.keys()])
        await update.message.reply_text(
            f"🌎 *{country.title()}* has multiple timezones.\n\n"
            f"Which one applies to you?\n{option_list}",
            parse_mode="Markdown"
        )
        return ASKING_REGION

    if not tz:
        await update.message.reply_text(
            "❌ Sorry, I couldn't find that country.\n"
            "Please try again or type a timezone directly (e.g. `Asia/Kolkata`)",
            parse_mode="Markdown"
        )
        return ASKING_COUNTRY

    save_user(chat_id, tz)
    await update.message.reply_text(
        f"✅ Timezone set to *{tz}*!\n\n"
        f"You can now set reminders like this:\n"
        f"`/remind 30m Call mom`\n"
        f"`/remind 2h Meeting`\n"
        f"`/remind 45s Take rice off`",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ─── Conversation: region input ──────────────────────────────────────────────

async def receive_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    region = update.message.text.strip().lower()
    options = pending_regions.get(chat_id, {})

    tz = options.get(region)

    if not tz:
        option_list = "\n".join([f"• {k.title()}" for k in options.keys()])
        await update.message.reply_text(
            f"❌ Couldn't match that. Please choose from:\n{option_list}"
        )
        return ASKING_REGION

    save_user(chat_id, tz)
    pending_regions.pop(chat_id, None)

    await update.message.reply_text(
        f"✅ Timezone set to *{tz}*!\n\n"
        f"You can now set reminders like this:\n"
        f"`/remind 30m Call mom`\n"
        f"`/remind 2h Meeting`\n"
        f"`/remind 45s Take rice off`",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ─── /remind ─────────────────────────────────────────────────────────────────

async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tz_str = get_user_timezone(chat_id)

    if not tz_str:
        await update.message.reply_text(
            "⚠️ Please set your timezone first using /start or /settimezone"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: `/remind <time> <message>`\n\n"
            "Examples:\n"
            "`/remind 30m Call mom`\n"
            "`/remind 2h Meeting`\n"
            "`/remind 45s Take rice off`\n"
            "`/remind 1h30m Doctor appointment`",
            parse_mode="Markdown"
        )
        return

    full_text = " ".join(context.args)
    time_part, message_part = split_time_and_message(full_text)

    if not time_part or not message_part:
        await update.message.reply_text(
            "❌ Wrong format. Use: `/remind <time> <message>`\n"
            "Example: `/remind 30m Call mom`",
            parse_mode="Markdown"
        )
        return

    remind_at = parse_reminder_time(time_part, tz_str)

    if not remind_at:
        await update.message.reply_text(
            "❌ Couldn't parse time. Use formats like:\n"
            "`30s` `10m` `2h` `1h30m`",
            parse_mode="Markdown"
        )
        return

    job_id = str(uuid.uuid4())
    save_reminder(chat_id, message_part, remind_at.isoformat(), job_id)

    reminders = get_reminders(chat_id)
    reminder_id = reminders[-1][0]

    scheduler.add_job(
        send_reminder,
        trigger='date',
        run_date=remind_at,
        args=[context.bot, chat_id, message_part, reminder_id],
        id=job_id,
        replace_existing=True,
    )

    formatted_time = remind_at.strftime("%d %b %Y, %I:%M:%S %p")
    await update.message.reply_text(
        f"✅ Reminder set!\n"
        f"📌 *{message_part}*\n"
        f"🕐 {formatted_time} ({tz_str})",
        parse_mode="Markdown"
    )


# ─── /reminders ──────────────────────────────────────────────────────────────

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tz_str = get_user_timezone(chat_id)
    reminders = get_reminders(chat_id)

    if not reminders:
        await update.message.reply_text("📭 You have no upcoming reminders.")
        return

    tz = pytz.timezone(tz_str)
    lines = []
    for r_id, message, remind_at in reminders:
        dt = datetime.fromisoformat(remind_at).astimezone(tz)
        formatted = dt.strftime("%d %b, %I:%M %p")
        lines.append(f"*{r_id}.* {message} — {formatted}")

    await update.message.reply_text(
        "📋 *Your Reminders:*\n\n" + "\n".join(lines) + "\n\nUse /cancel <id> to remove one.",
        parse_mode="Markdown"
    )


# ─── /cancel ─────────────────────────────────────────────────────────────────

async def cancel_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "Usage: `/cancel <id>`\nGet IDs from /reminders",
            parse_mode="Markdown"
        )
        return

    try:
        reminder_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid reminder ID number.")
        return

    job_id = delete_reminder(reminder_id, chat_id)

    if not job_id:
        await update.message.reply_text("❌ Reminder not found or doesn't belong to you.")
        return

    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    await update.message.reply_text(f"✅ Reminder *#{reminder_id}* cancelled.", parse_mode="Markdown")


# ─── /mytimezone ─────────────────────────────────────────────────────────────

async def my_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tz = get_user_timezone(chat_id)
    if tz:
        await update.message.reply_text(f"🌍 Your timezone is: *{tz}*", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ No timezone set. Use /start or /settimezone.")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    init_db()
    scheduler.start()

    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("settimezone", set_timezone),
        ],
        states={
            ASKING_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_country)],
            ASKING_REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_region)],
        },
        fallbacks=[],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("cancel", cancel_reminder))
    app.add_handler(CommandHandler("mytimezone", my_timezone))

    print("🤖 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
