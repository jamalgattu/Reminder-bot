from flask import Flask, request, jsonify
from telegram import (
    Update, Bot,
    InlineQueryResultArticle, InputTextMessageContent,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Updater, CommandHandler, CallbackContext,
    InlineQueryHandler, CallbackQueryHandler
)
from telegram.error import TelegramError
import logging
from datetime import datetime, timedelta
import pytz
import os
import uuid
from dotenv import load_dotenv
import db
from parser import parse_time_string

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

# In-memory store for pending inline reminders (rid -> reminder data)
# Only lives until the user taps the button, so memory is fine
pending_inline = {}


# ========== Helpers ==========

def format_remind_dt(remind_dt, tz_str):
    """Format a timezone-aware datetime nicely."""
    tz = pytz.timezone(tz_str)
    local_dt = remind_dt.astimezone(tz)
    return local_dt.strftime("%-d %b %Y, %I:%M:%S %p") + f" ({tz_str})"


def parse_time_to_dt(time_str, user_timezone):
    """
    Parse a time string (duration or HH:MM) into a timezone-aware datetime.
    Returns remind_dt or None.
    """
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)

    seconds = parse_time_string(time_str)
    if seconds:
        return now + timedelta(seconds=seconds)

    try:
        remind_time = datetime.strptime(time_str, "%H:%M").time()
        remind_dt = tz.localize(datetime.combine(now.date(), remind_time))
        if remind_dt <= now:
            remind_dt += timedelta(days=1)
        return remind_dt
    except ValueError:
        pass

    return None


def parse_remind_args(args, user_timezone):
    """
    Parse /remind command args. Supports duration (30s/5m/2h30m) and clock (14:30).
    Returns (remind_dt, message, error_str).
    """
    if len(args) < 2:
        return None, None, (
            "Usage: /remind <time> <message>\nExamples:\n"
            "  /remind 30m Drink water\n"
            "  /remind 14:30 Team meeting"
        )
    time_str = args[0]
    message = ' '.join(args[1:])
    remind_dt = parse_time_to_dt(time_str, user_timezone)
    if not remind_dt:
        return None, None, (
            "❌ Invalid time format.\n"
            "Use a duration like `30s`, `5m`, `2h30m`\n"
            "or a clock time like `14:30`."
        )
    return remind_dt, message, None


def schedule_reminder(chat_id, message, remind_dt, extra=None):
    """Schedule a reminder job and save it to the DB."""
    now = datetime.now(pytz.utc)
    delay = (remind_dt.astimezone(pytz.utc) - now).total_seconds()
    ctx = {'chat_id': chat_id, 'message': message}
    if extra:
        ctx.update(extra)
    job = job_queue.run_once(send_reminder, delay, context=ctx)
    db.save_reminder(chat_id, message, remind_dt.isoformat(), job.id)
    return job


# ========== Command Handlers ==========

def start(update: Update, context: CallbackContext):
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
        "You can also use me inline: type @botname in any chat!"
    )


def set_timezone(update: Update, context: CallbackContext):
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
    chat_id = update.effective_chat.id
    user_timezone = db.get_user_timezone(chat_id) or 'UTC'

    remind_dt, message, error = parse_remind_args(context.args, user_timezone)
    if error:
        update.message.reply_text(error)
        return

    schedule_reminder(chat_id, message, remind_dt)
    time_label = format_remind_dt(remind_dt, user_timezone)
    update.message.reply_text(
        f"✅ Reminder set!\n"
        f"📌 {message}\n"
        f"🕐 {time_label}"
    )


def send_reminder(context: CallbackContext):
    """Fire a reminder — edits the inline message in the chat, or DMs the user."""
    chat_id = context.job.context['chat_id']
    message = context.job.context['message']
    inline_chat_id = context.job.context.get('inline_chat_id')
    inline_message_id = context.job.context.get('inline_message_id')

    reminder_text = f"⏰ Reminder: {message}"

    try:
        if inline_chat_id and inline_message_id:
            # Edit the original message right in the group/chat
            bot.edit_message_text(
                chat_id=inline_chat_id,
                message_id=inline_message_id,
                text=reminder_text
            )
        else:
            bot.send_message(chat_id=chat_id, text=reminder_text)
    except TelegramError as e:
        logger.error(f"Failed to send reminder: {e}")
        # Fallback: DM the user
        try:
            if inline_chat_id:
                bot.send_message(chat_id=chat_id, text=reminder_text)
        except TelegramError:
            pass


def list_reminders(update: Update, context: CallbackContext):
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
        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="Set a reminder",
                description="Type: <time> <message>  e.g. 30m Drink water",
                input_message_content=InputTextMessageContent(
                    "💡 Type @botname <time> <message>\n"
                    "Examples:\n  30m Drink water\n  2h Team meeting\n  14:30 Lunch"
                )
            )
        )
    else:
        parts = query.split(maxsplit=1)
        time_str = parts[0]
        message = parts[1] if len(parts) > 1 else ""

        remind_dt = parse_time_to_dt(time_str, user_timezone) if message else None

        if remind_dt and message:
            time_label = format_remind_dt(remind_dt, user_timezone)

            # Store pending reminder data keyed by a short ID
            rid = str(uuid.uuid4())[:12]
            pending_inline[rid] = {
                'time_str': time_str,
                'message': message,
                'user_id': user_id,
                'user_timezone': user_timezone,
                'remind_dt': remind_dt.isoformat(),
            }

            preview_text = (
                f"⏰ Reminder pending...\n"
                f"📌 {message}\n"
                f"🕐 {time_label}\n\n"
                f"Tap the button to confirm!"
            )
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Set Reminder", callback_data=f"r:{rid}")
            ]])

            results.append(
                InlineQueryResultArticle(
                    id=rid,
                    title=f"⏰ Remind: {message}",
                    description=f"🕐 {time_label}  — tap to confirm",
                    input_message_content=InputTextMessageContent(preview_text),
                    reply_markup=keyboard
                )
            )
        elif time_str and not message:
            results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Add a message",
                    description=f"Time: {time_str} — now type your reminder message",
                    input_message_content=InputTextMessageContent(
                        f"⏳ Time: {time_str}\n📝 Add your reminder message after the time."
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


def inline_confirm(update: Update, context: CallbackContext):
    """
    Called when the user taps the 'Set Reminder' button on an inline message.
    At this point we have the real chat_id and message_id.
    """
    query = update.callback_query
    data = query.data

    if not data.startswith("r:"):
        query.answer("Unknown action.")
        return

    rid = data[2:]
    pending = pending_inline.pop(rid, None)

    if not pending:
        query.answer("⚠️ This reminder has already been set or expired.")
        return

    user_id = pending['user_id']
    message = pending['message']
    user_timezone = pending['user_timezone']
    remind_dt = datetime.fromisoformat(pending['remind_dt'])

    # Re-parse in case of clock-time that needs recalculation (edge case)
    # Use stored remind_dt directly — it was calculated at inline_query time
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    db.save_user(user_id, user_timezone)
    schedule_reminder(
        user_id,
        message,
        remind_dt,
        extra={'inline_chat_id': chat_id, 'inline_message_id': message_id}
    )

    time_label = format_remind_dt(remind_dt, user_timezone)
    query.edit_message_text(
        f"✅ Reminder set!\n"
        f"📌 {message}\n"
        f"🕐 {time_label}"
    )
    query.answer("Reminder set!")


# ========== Register Handlers ==========

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("set_timezone", set_timezone))
dispatcher.add_handler(CommandHandler("remind", remind))
dispatcher.add_handler(CommandHandler("list", list_reminders))
dispatcher.add_handler(CommandHandler("delete", delete_reminder))
dispatcher.add_handler(InlineQueryHandler(inline_query))
dispatcher.add_handler(CallbackQueryHandler(inline_confirm, pattern=r"^r:"))

# ========== Flask Routes ==========

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'OK'

@app.route('/', methods=['GET'])
def index():
    return "Reminder Bot is running!"

@app.route('/health', methods=['GET'])
def health():
    from flask import jsonify
    return jsonify({"status": "healthy"}), 200

# ========== Main ==========

if __name__ == '__main__':
    updater.start_polling()
    app.run(host='0.0.0.0', port=5000, debug=False)
