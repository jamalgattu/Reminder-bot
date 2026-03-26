# bot.py
from flask import Flask, request, jsonify
from telegram import (
    Update, Bot,
    InlineQueryResultArticle, InputTextMessageContent,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReactionTypeEmoji
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
from parser import parse_time_to_dt, parse_time_string

load_dotenv()

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
bot = Bot(token=BOT_TOKEN)
updater = Updater(token=BOT_TOKEN, use_context=True)
dispatcher = updater.dispatcher
job_queue = updater.job_queue

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

db.init_db()

# Stores pending inline reminders until user taps confirm button
pending_inline = {}


# ========== Helpers ==========

def format_remind_dt(remind_dt, tz_str):
    tz = pytz.timezone(tz_str)
    local_dt = remind_dt.astimezone(tz)
    return local_dt.strftime("%-d %b %Y, %I:%M:%S %p") + f" ({tz_str})"


def parse_remind_args(args, user_timezone):
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


def schedule_reminder(chat_id, user_id, message_id, message, remind_dt, extra=None):
    now = datetime.now(pytz.utc)
    delay = (remind_dt.astimezone(pytz.utc) - now).total_seconds()
    ctx = {
        'chat_id': chat_id,
        'user_id': user_id,
        'message_id': message_id,
        'message': message,
    }
    if extra:
        ctx.update(extra)
    job = job_queue.run_once(send_reminder, delay, context=ctx)
    db.save_reminder(chat_id, user_id, message_id, message, remind_dt.isoformat(), job.id)
    return job


# ========== Reminder Sender ==========

def send_reminder(context: CallbackContext):
    chat_id = context.job.context['chat_id']
    user_id = context.job.context['user_id']
    message_id = context.job.context.get('message_id')
    message = context.job.context['message']
    user_first_name = context.job.context.get('user_first_name', 'You')

    try:
        # Add ⏰ reaction to original message if message_id exists
        if message_id:
            try:
                bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=[ReactionTypeEmoji(emoji="⏰")],
                    is_big=True
                )
            except TelegramError as e:
                logger.warning(f"Could not set reaction: {e}")

        # Send tagged reminder message in chat
        mention = f'<a href="tg://user?id={user_id}">{user_first_name}</a>'
        bot.send_message(
            chat_id=chat_id,
            text=f"{mention} ⏰ Reminder: {message}",
            parse_mode='HTML'
        )

    except TelegramError as e:
        logger.error(f"Failed to send reminder: {e}")


# ========== Command Handlers ==========

def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    db.save_user(user_id, timezone=None)
    update.message.reply_text(
        "👋 Welcome to Reminder Bot!\n\n"
        "Commands:\n"
        "/set_timezone <timezone> — Set your timezone\n"
        "  e.g. /set_timezone Asia/Kolkata\n\n"
        "/remind <time> <message> — Set a reminder\n"
        "  Duration: /remind 30m Drink water\n"
        "  Clock:    /remind 14:30 Team meeting\n\n"
        "/list — Show reminders in this chat\n"
        "/delete <id> — Delete a reminder\n\n"
        "💬 Works in groups too!\n"
        "Use /remind in a group and I'll tag you when the time comes."
    )


def set_timezone(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not context.args:
        update.message.reply_text(
            "Usage: /set_timezone <timezone>\n"
            "Example: /set_timezone Asia/Kolkata\n\n"
            "Find your timezone at: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
        )
        return
    timezone = context.args[0]
    try:
        pytz.timezone(timezone)
        db.save_user(user_id, timezone)
        update.message.reply_text(f"✅ Timezone set to {timezone}")
    except pytz.exceptions.UnknownTimeZoneError:
        update.message.reply_text(f"❌ Unknown timezone: {timezone}")


def remind(update: Update, context: CallbackContext):
    user = update.effective_user
    chat_id = update.effective_chat.id
    message_id = update.message.message_id
    user_timezone = db.get_user_timezone(user.id) or 'UTC'

    remind_dt, message, error = parse_remind_args(context.args, user_timezone)
    if error:
        update.message.reply_text(error)
        return

    extra = {'user_first_name': user.first_name or 'You'}
    schedule_reminder(chat_id, user.id, message_id, message, remind_dt, extra=extra)

    time_label = format_remind_dt(remind_dt, user_timezone)
    update.message.reply_text(
        f"✅ Reminder set!\n"
        f"📌 {message}\n"
        f"🕐 {time_label}"
    )


def list_reminders(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    reminders = db.get_reminders(chat_id)
    if not reminders:
        update.message.reply_text("📭 No reminders in this chat.")
        return
    lines = ["📋 Reminders in this chat:\n"]
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


# ========== Inline Mode ==========

def inline_query(update: Update, context: CallbackContext):
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
            rid = str(uuid.uuid4())[:12]
            pending_inline[rid] = {
                'time_str': time_str,
                'message': message,
                'user_id': user_id,
                'user_first_name': update.inline_query.from_user.first_name or 'You',
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
                    description=f"🕐 {time_label} — tap to confirm",
                    input_message_content=InputTextMessageContent(preview_text),
                    reply_markup=keyboard
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
    user_first_name = pending['user_first_name']
    remind_dt = datetime.fromisoformat(pending['remind_dt'])

    # Get group chat_id and message_id from the callback
    if query.message:
        chat_id = query.message.chat_id
        message_id = query.message.message_id
    else:
        # Inline message in group — use inline_message_id context
        chat_id = user_id  # fallback to DM if no chat context
        message_id = None

    db.save_user(user_id, user_timezone)

    schedule_reminder(
        chat_id,
        user_id,
        message_id,
        message,
        remind_dt,
        extra={'user_first_name': user_first_name}
    )

    time_label = format_remind_dt(remind_dt, user_timezone)
    confirm_text = (
        f"✅ Reminder set!\n"
        f"📌 {message}\n"
        f"🕐 {time_label}"
    )

    if query.message:
        query.edit_message_text(confirm_text)
    else:
        try:
            context.bot.edit_message_text(
                inline_message_id=query.inline_message_id,
                text=confirm_text,
            )
        except Exception:
            pass

    query.answer("✅ Reminder confirmed!")


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
    return jsonify({"status": "healthy"}), 200


# ========== Error Handler ==========

def error_handler(update, context):
    from telegram.error import Conflict, NetworkError
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Conflict: another instance was running — now resolved.")
    elif isinstance(err, NetworkError):
        logger.warning(f"Network error (will retry): {err}")
    else:
        logger.error(f"Update {update} caused error: {err}", exc_info=err)

dispatcher.add_error_handler(error_handler)


# ========== Main ==========

if __name__ == '__main__':
    bot.delete_webhook(drop_pending_updates=True)
    updater.start_polling(drop_pending_updates=True, timeout=20, read_latency=5)
    app.run(host='0.0.0.0', port=5000, debug=False
