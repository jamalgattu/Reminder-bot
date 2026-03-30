# pkg_resources compatibility shim for APScheduler 3.6.3.
# Wrapped in a function so closure variables survive without polluting module scope.
def _patch_pkg_resources():
    import sys
    import types
    import importlib.metadata as _imeta

    try:
        import pkg_resources as _pkgr
    except ImportError:
        _pkgr = types.ModuleType("pkg_resources")
        sys.modules["pkg_resources"] = _pkgr

    if not hasattr(_pkgr, "DistributionNotFound"):
        _pkgr.DistributionNotFound = _imeta.PackageNotFoundError

    if not hasattr(_pkgr, "get_distribution"):
        class _Dist:
            def __init__(self, d):
                self.version = d.metadata["Version"]
        def _get_dist(name):
            try:
                return _Dist(_imeta.distribution(name))
            except _imeta.PackageNotFoundError:
                raise _pkgr.DistributionNotFound(name)
        _pkgr.get_distribution = _get_dist

    if not hasattr(_pkgr, "iter_entry_points"):
        def _iter_eps(group, name=None):
            try:
                eps = _imeta.entry_points(group=group)
                return iter([ep for ep in eps if name is None or ep.name == name])
            except Exception:
                return iter([])
        _pkgr.iter_entry_points = _iter_eps

_patch_pkg_resources()
del _patch_pkg_resources

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

# In-memory store for pending inline reminders (rid -> reminder data).
# Cleared on restart — old inline buttons will show "expired".
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
    Always called fresh at button-press time so the delay is accurate.
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
    if delay < 0:
        delay = 0
    ctx = {'chat_id': chat_id, 'message': message}
    if extra:
        ctx.update(extra)
    job_name = str(uuid.uuid4())
    job = job_queue.run_once(send_reminder, delay, context=ctx, name=job_name)
    db.save_reminder(chat_id, message, remind_dt.isoformat(), job_name)
    logger.info(f"Reminder scheduled: chat_id={chat_id} delay={delay:.1f}s msg='{message}'")
    return job


# ========== Command Handlers ==========

def start(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    db.register_user(chat_id)
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
        "💬 In a group chat?\n"
        "Use /remind directly in the group — when the time comes, "
        "I'll send a new message in the group tagging you.\n\n"
        "⚡ Inline mode (@botname in any chat) is also supported, "
        "but reminders set that way are delivered to you here privately.\n"
        "Make sure you've started this bot in DM before using inline mode!"
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
    user = update.effective_user
    user_timezone = db.get_user_timezone(user.id) or 'UTC'

    remind_dt, message, error = parse_remind_args(context.args, user_timezone)
    if error:
        update.message.reply_text(error)
        return

    is_group = update.effective_chat.type in ('group', 'supergroup')
    extra = None
    if is_group:
        extra = {
            'user_id': user.id,
            'user_first_name': user.first_name or 'You',
            'inline_chat_id': chat_id,
        }

    schedule_reminder(chat_id, message, remind_dt, extra=extra)
    time_label = format_remind_dt(remind_dt, user_timezone)
    update.message.reply_text(
        f"✅ Reminder set!\n"
        f"📌 {message}\n"
        f"🕐 {time_label}"
    )


def send_reminder(context: CallbackContext):
    """Fire a reminder."""
    chat_id = context.job.context['chat_id']
    message = context.job.context['message']
    reminder_text = f"⏰ Reminder: {message}"

    inline_chat_id = context.job.context.get('inline_chat_id')
    user_id = context.job.context.get('user_id')
    user_first_name = context.job.context.get('user_first_name', 'You')
    inline_message_id_str = context.job.context.get('inline_message_id_str')

    if inline_chat_id and user_id:
        # /remind in a group — send a tagged message in the group chat
        mention = f'<a href="tg://user?id={user_id}">{user_first_name}</a>'
        try:
            bot.send_message(
                chat_id=inline_chat_id,
                text=f"{mention} {reminder_text}",
                parse_mode='HTML'
            )
            return
        except TelegramError as e:
            logger.error(f"Failed to send group reminder, falling back to DM: {e}")

    if inline_message_id_str:
        # Inline bot result — edit the original inline message, then DM the user
        try:
            bot.edit_message_text(
                inline_message_id=inline_message_id_str,
                text=reminder_text
            )
        except TelegramError as e:
            logger.warning(f"Could not edit inline message: {e}")
        try:
            bot.send_message(chat_id=chat_id, text=reminder_text)
        except TelegramError as e:
            logger.error(f"Failed to DM reminder: {e}")
        return

    # Plain /remind in DM
    try:
        bot.send_message(chat_id=chat_id, text=reminder_text)
    except TelegramError as e:
        logger.error(f"Failed to send reminder: {e}")


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
            matching = job_queue.get_jobs_by_name(job_id)
            if matching:
                matching[0].schedule_removal()
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

            rid = str(uuid.uuid4())[:12]
            pending_inline[rid] = {
                'time_str': time_str,
                'message': message,
                'user_id': user_id,
                'user_timezone': user_timezone,
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

    Key facts about inline callback queries (Telegram Bot API):
      - query.message is None  — inline results don't expose the chat message object
      - query.inline_message_id is set — use this to edit the sent message

    The remind_dt is re-calculated fresh at button-press time (not the stale value
    stored during inline_query), so the countdown starts from when the user confirms.
    """
    query = update.callback_query
    data = query.data

    if not data.startswith("r:"):
        query.answer("Unknown action.")
        return

    rid = data[2:]
    pending = pending_inline.pop(rid, None)

    if not pending:
        query.answer(
            "⚠️ Reminder expired — please create a new one.",
            show_alert=True
        )
        return

    user_id = pending['user_id']
    message = pending['message']
    time_str = pending['time_str']
    user_timezone = pending['user_timezone']
    user_first_name = query.from_user.first_name or "You"

    # Re-calculate remind_dt NOW (at button-press time), not at inline-query time.
    # This ensures "1h" means 1 hour from when the user taps Confirm, not from
    # when they started typing.
    remind_dt = parse_time_to_dt(time_str, user_timezone)
    if not remind_dt:
        query.answer("❌ Could not schedule reminder.", show_alert=True)
        return

    db.save_user(user_id, user_timezone)
    time_label = format_remind_dt(remind_dt, user_timezone)
    confirm_text = f"✅ Reminder set!\n📌 {message}\n🕐 {time_label}"

    if query.message is not None:
        # Button on a regular bot message (not an inline result) — we have chat_id
        chat_id = query.message.chat_id
        schedule_reminder(
            user_id, message, remind_dt,
            extra={
                'inline_chat_id': chat_id,
                'user_id': user_id,
                'user_first_name': user_first_name,
            }
        )
        query.edit_message_text(confirm_text)
    else:
        # Inline result button — query.message is None, use inline_message_id
        inline_message_id_str = query.inline_message_id
        schedule_reminder(
            user_id, message, remind_dt,
            extra={
                'inline_message_id_str': inline_message_id_str,
                'user_id': user_id,
                'user_first_name': user_first_name,
            }
        )
        try:
            context.bot.edit_message_text(
                inline_message_id=inline_message_id_str,
                text=confirm_text,
            )
        except TelegramError as e:
            logger.warning(f"Could not edit inline confirmation message: {e}")

    query.answer("✅ Reminder set!")


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
    """Suppress Conflict errors (stale poll from previous run); log everything else."""
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
    import threading
    port = int(os.environ.get('PORT', 5000))

    # Start Flask first so Railway healthcheck responds immediately
    flask_thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    )
    flask_thread.daemon = True
    flask_thread.start()

    # Then initialise the Telegram bot
    bot.delete_webhook(drop_pending_updates=True)
    updater.start_polling(drop_pending_updates=True, timeout=20, read_latency=5)
    updater.idle()
