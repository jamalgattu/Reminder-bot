from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, JobQueue
from telegram.error import TelegramError
import logging
from datetime import datetime
import pytz
import os
from dotenv import load_dotenv
import db

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

# ========== Callback Functions ==========

def start(update: Update, context: CallbackContext):
    """Start command handler"""
    chat_id = update.effective_chat.id
    db.save_user(chat_id, timezone=None)
    update.message.reply_text(
        "Welcome to Reminder Bot! 🤖\n"
        "Commands:\n"
        "/set_timezone <timezone> - Set your timezone (e.g., /set_timezone Asia/Kolkata)\n"
        "/remind <time> <message> - Set a reminder\n"
        "/list - Show all reminders\n"
        "/delete <id> - Delete a reminder"
    )

def set_timezone(update: Update, context: CallbackContext):
    """Set user's timezone"""
    chat_id = update.effective_chat.id
    
    if not context.args:
        update.message.reply_text("Usage: /set_timezone <timezone>\nExample: /set_timezone Asia/Kolkata")
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
    
    if len(context.args) < 2:
        update.message.reply_text("Usage: /remind <HH:MM> <message>\nExample: /remind 14:30 Drink water")
        return
    
    time_str = context.args[0]
    message = ' '.join(context.args[1:])
    
    try:
        # Parse time
        remind_time = datetime.strptime(time_str, "%H:%M").time()
        
        # Create reminder datetime in user's timezone
        tz = pytz.timezone(user_timezone)
        now = datetime.now(tz)
        remind_dt = tz.localize(
            datetime.combine(now.date(), remind_time)
        )
        
        # If time has passed, schedule for tomorrow
        if remind_dt <= now:
            from datetime import timedelta
            remind_dt += timedelta(days=1)
        
        # Calculate delay in seconds
        delay = (remind_dt - now).total_seconds()
        
        # Schedule job
        job = job_queue.run_once(
            send_reminder,
            delay,
            context={'chat_id': chat_id, 'message': message}
        )
        
        # Save reminder to DB
        db.save_reminder(chat_id, message, remind_dt.isoformat(), job.id)
        
        update.message.reply_text(f"✅ Reminder set for {time_str}: {message}")
    
    except ValueError:
        update.message.reply_text("❌ Invalid time format. Use HH:MM (e.g., 14:30)")

def send_reminder(context: CallbackContext):
    """Send reminder notification"""
    chat_id = context.job.context['chat_id']
    message = context.job.context['message']
    
    try:
        bot.send_message(
            chat_id=chat_id,
            text=f"🔔 Reminder: {message}"
        )
    except TelegramError as e:
        logger.error(f"Failed to send reminder: {e}")

def list_reminders(update: Update, context: CallbackContext):
    """List all reminders for the user"""
    chat_id = update.effective_chat.id
    reminders = db.get_reminders(chat_id)
    
    if not reminders:
        update.message.reply_text("📭 You have no reminders")
        return
    
    message = "📋 Your Reminders:\n\n"
    for reminder_id, reminder_msg, remind_at in reminders:
        message += f"ID: {reminder_id}\n{reminder_msg}\nTime: {remind_at}\n\n"
    
    update.message.reply_text(message)

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
            job_queue.get_job_by_name(job_id)
            if job_queue.get_job_by_name(job_id):
                job_queue.get_job_by_name(job_id).schedule_removal()
            update.message.reply_text(f"✅ Reminder {reminder_id} deleted")
        else:
            update.message.reply_text(f"❌ Reminder {reminder_id} not found")
    
    except ValueError:
        update.message.reply_text("❌ Invalid reminder ID")

# ========== Register Handlers ==========

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("set_timezone", set_timezone))
dispatcher.add_handler(CommandHandler("remind", remind))
dispatcher.add_handler(CommandHandler("list", list_reminders))
dispatcher.add_handler(CommandHandler("delete", delete_reminder))

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
    # Start long polling
    updater.start_polling()
    
    # Start Flask app
    app.run(host='0.0.0.0', port=5000, debug=False)
