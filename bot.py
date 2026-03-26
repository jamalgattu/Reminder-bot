from flask import Flask, request, jsonify
from telegram import Update, Bot, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import (
    Updater, CommandHandler, MessageHandler,
    Filters, CallbackContext, InlineQueryHandler, ChosenInlineResultHandler
)
from telegram.error import TelegramError
import logging
from datetime import datetime, timedelta
import pytz
import os
import uuid
from dotenv import load_dotenv
import db
from parser import parse_time_string, parse_reminder_time

load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Initialize bot
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
bot = Bot(token=BOT_TOKEN)
updater = Updater(token=BOT_TOKEN, use_context=True)
dispatcher = updater.dispatcher
job_queue = updater.job_queue

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize database
db.init_db()

# ========== Helpers ==========

def format_remind_dt(remind_dt, tz_str):
    """Format a timezone-aware datetime nicely."""
    tz = pytz.timezone(tz_str)
    local_dt = remind_dt.astimezone(tz)
    return local_dt.strftime("%-d %b %Y, %I:%M:%S %p") + f" ({tz_str})"


def parse_remind_args(args, user_timezone):
    """
    Parse reminder args. Supports:
      - Duration: 30s / 5m / 2h / 2h30m  -> fires after that delay
      - Clock:    14:30                   -> fires at that time today (or tomorrow)
    Returns (remind_dt, error_str) where one is None.
    """
    if len(args) < 2:
        return None, "Usage: /remind <time> <message>\nExamples:\n  /remind 30m Drink water\n  /remind 14:30 Team meeting"

    time_str = args[0]
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)

    # Try duration format first (30s, 5m, 2h, 2h30m)
    seconds = parse_time_string(time_str)
    if seconds:
        remind_dt = now + timedelta(seconds=seconds)
        return remind_dt, None

    # Try HH:MM clock format
    try:
        remind_time = datetime.strptime(time_str, "%H:%M").time()
        remind_dt = tz.localize(datetime.combine(now.date(), remind_time))
        if remind_dt <= now:
            remind_dt += timedelta(days=1)
        return remind_dt, None
    except ValueError:
        pass

    return None, "❌ Invalid time format.\nUse a duration like `30s`, `5m`, `2h30m`\nor a clock time like `14:30`."


def schedule_reminder(chat_id, message, remind_dt, user_timezone):
    """Schedule a reminder job and save it to the DB. Returns the job."""
    now = datetime.now(pytz.utc)
    delay = (remind_dt.astimezone(pytz.utc) - now).total_seconds()
    job = job_queue.run_once(
        send_reminder,
        delay,
        context={'chat_id': chat_id, 'message': message}
    )
    db.save_reminder(chat_id, message, remind_dt.isoformat(), job.id)
    return job


# ========== Command Handlers ==========

def start(update: Update, context: CallbackContext):
    """Start command handler"""
    chat_id = update.effective_chat.id
    db.save_user(chat_id, timezone=None)
    update.message.reply_text(
        "👋 Welcome to Reminder Bot!\n\n"
        "Commands:\n"
        "/set_timezone <timezone> — Set your timezone\n"
        "  e.g. /set_timezone Asia/Kolkata\n\n"
        "/remind <time> <message> — Set a reminder\n"
        "  Duration: /remind 30m Drink water\n"
        "  Clock:    /remind 14:30 Team meeting\n\n"
        "/list — Show all reminders\n"
        "/delete <id> — Delete a reminder\n\n"
        "You can also use me inline: type @<botname> in any chat!"
    )


def set_timezone(update: Update, context: CallbackContext):
    """Set user's timezone"""
    chat_id = update.effective_chat.id

    if not context.args:
        update.message.reply_text(
            "Usage: /set_timezone <timezone>\nExample: /set_timezone Asia/Kolkata"
        )
        return

    timezone = context.args[0]
    try:
        pytz.timezone(timezone)
        db.save_user(chat_id, timezone)
        update.message.reply_text(f"✅ Timezone set to {timezone}")
    except pytz.exceptions.UnknownTimeZoneError:
        update.message.reply_text(f"❌ Unknown timezone: {timezone}")


def remind(update: Update, context: CallbackContext):
    """Set a reminder"""
    chat_id = update.effective_chat.id
    user_timezone = db.get_user_timezone(chat_id) or 'UTC'

    remind_dt, error = parse_remind_args(context.args, user_timezone)
    if error:
        update.message.reply_text(error)
        return

    message = ' '.join(context.args[1:])
    schedule_reminder(chat_id, message, remind_dt, user_timezone)

    time_label = format_remind_dt(remind_dt, user_timezone)
    update.message.reply_text(
        f"✅ Reminder set!\n"
        f"📌 {message}\n"
        f"🕐 {time_label}"
    )


def send_reminder(context: CallbackContext):
    """Send reminder notification"""
    chat_id = context.job.context['chat_id']
    message = context.job.context['message']
    inline_message_id = context.job.context.get('inline_message_id')

    try:
        if inline_message_id:
            # Edit the original inline message in the group/chat where it was sent
            bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=f"⏰ Reminder: {message}"
            )
        else:
            bot.send_message(
                chat_id=chat_id,
                text=f"⏰ Reminder: {message}"
            )
    except TelegramError as e:
        logger.error(f"Failed to send reminder: {e}")


def list_reminders(update: Update, context: CallbackContext):
    """List all reminders for the user"""
    chat_id = update.effective_chat.id
    reminders = db.get_reminders(chat_id)

    if not reminders:
        update.message.reply_text("📭 You have no active reminders.")
        return

    lines = ["📋 Your Reminders:\n"]
    for reminder_id, reminder_msg, remind_at in reminders:
        lines.append(f"🔹 ID {reminder_id}: {reminder_msg}\n   🕐 {remind_at}\n")

    update.message.reply_text('\n'.join(lines))


def delete_reminder(update: Update, context: CallbackContext):
    """Delete a reminder"""
    chat_id = update.effective_chat.id

    if not context.args:
        update.message.reply_text("Usage: /delete <reminder_id>")
        return

    try:
        reminder_id = int(context.args[0])
        job_id = db.delete_reminder(reminder_id, chat_id)

        if job_id:
            existing = job_queue.get_job_by_name(job_id)
            if existing:
                existing.schedule_removal()
            update.message.reply_text(f"✅ Reminder {reminder_id} deleted.")
        else:
            update.message.reply_text(f"❌ Reminder {reminder_id} not found.")

    except ValueError:
        update.message.reply_text("❌ Invalid reminder ID.")


# ========== Inline Bot Handlers ==========

def inline_query(update: Update, context: CallbackContext):
    """Handle inline queries: @botname 30m Have tea"""
    query = update.inline_query.query.strip()
    user_id = update.inline_query.from_user.id
    user_timezone = db.get_user_timezone(user_id) or 'UTC'

    results = []

    if not query:
        # Show hint when nothing typed yet
        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="Set a reminder",
                description="Type: <time> <message>  e.g. 30m Drink water",
                input_message_content=InputTextMessageContent(
                    "💡 Usage: @<botname> <time> <message>\n"
                    "Examples:\n  30m Drink water\n  2h Team meeting\n  14:30 Lunch"
                )
            )
        )
    else:
        parts = query.split(maxsplit=1)
        time_str = parts[0]
        message = parts[1] if len(parts) > 1 else ""

        remind_dt = None
        tz = pytz.timezone(user_timezone)
        now = datetime.now(tz)

        # Try duration
        seconds = parse_time_string(time_str)
        if seconds:
            remind_dt = now + timedelta(seconds=seconds)

        # Try HH:MM
        if remind_dt is None:
            try:
                remind_time = datetime.strptime(time_str, "%H:%M").time()
                remind_dt = tz.localize(datetime.combine(now.date(), remind_time))
                if remind_dt <= now:
                    remind_dt += timedelta(days=1)
            except ValueError:
                pass

        if remind_dt and message:
            time_label = format_remind_dt(remind_dt, user_timezone)
            confirm_text = (
                f"✅ Reminder set!\n"
                f"📌 {message}\n"
                f"🕐 {time_label}"
            )
            results.append(
                InlineQueryResultArticle(
                    id=f"{time_str}|{message}",
                    title=f"⏰ Remind: {message}",
                    description=f"🕐 {time_label}",
                    input_message_content=InputTextMessageContent(confirm_text)
                )
            )
        elif time_str and not message:
            results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Add a message",
                    description=f"Time detected: {time_str} — now add your reminder message",
                    input_message_content=InputTextMessageContent(
                        f"⏳ Time: {time_str}\n📝 Please add a message after the time."
                    )
                )
            )
        else:
            results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="❌ Invalid format",
                    description="Try: 30m Drink water  or  14:30 Meeting",
                    input_message_content=InputTextMessageContent(
                        "❌ Invalid format.\nTry: 30m Drink water  or  14:30 Meeting"
                    )
                )
            )

    update.inline_query.answer(results, cache_time=0)


def chosen_inline_result(update: Update, context: CallbackContext):
    """When a user picks an inline result, actually schedule the reminder."""
    result = update.chosen_inline_result
    result_id = result.result_id
    user_id = result.from_user.id
    inline_message_id = result.inline_message_id  # ID of the message posted in the chat
    user_timezone = db.get_user_timezone(user_id) or 'UTC'

    # result_id is "time_str|message" for valid reminders
    if '|' not in result_id:
        return

    time_str, message = result_id.split('|', 1)
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)

    remind_dt = None
    seconds = parse_time_string(time_str)
    if seconds:
        remind_dt = now + timedelta(seconds=seconds)
    else:
        try:
            remind_time = datetime.strptime(time_str, "%H:%M").time()
            remind_dt = tz.localize(datetime.combine(now.date(), remind_time))
            if remind_dt <= now:
                remind_dt += timedelta(days=1)
        except ValueError:
            pass

    if remind_dt and message:
        db.save_user(user_id, user_timezone)
        delay = (remind_dt.astimezone(pytz.utc) - datetime.now(pytz.utc)).total_seconds()
        job = job_queue.run_once(
            send_reminder,
            delay,
            context={
                'chat_id': user_id,
                'message': message,
                'inline_message_id': inline_message_id
            }
        )
        db.save_reminder(user_id, message, remind_dt.isoformat(), job.id)
        logger.info(f"Inline reminder scheduled for user {user_id}: {message} at {remind_dt}")


# ========== Register Handlers ==========

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("set_timezone", set_timezone))
dispatcher.add_handler(CommandHandler("remind", remind))
dispatcher.add_handler(CommandHandler("list", list_reminders))
dispatcher.add_handler(CommandHandler("delete", delete_reminder))
dispatcher.add_handler(InlineQueryHandler(inline_query))
dispatcher.add_handler(ChosenInlineResultHandler(chosen_inline_result))

# ========== Flask Routes ==========

@app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook endpoint for Telegram updates"""
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'OK'

@app.route('/', methods=['GET'])
def index():
    return "Reminder Bot is running!"

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"}), 200

# ========== Main ==========

if __name__ == '__main__':
    updater.start_polling()
    app.run(host='0.0.0.0', port=5000, debug=False)
