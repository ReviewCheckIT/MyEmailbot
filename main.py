# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import random
import httpx
from datetime import datetime
from threading import Thread

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    ConversationHandler, 
    MessageHandler, 
    CallbackQueryHandler,
    filters
)
import firebase_admin
from firebase_admin import credentials, db

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TOKEN = os.environ.get('EMAIL_BOT_TOKEN')
OWNER_ID = os.environ.get('BOT_OWNER_ID')
FB_JSON = os.environ.get('FIREBASE_CREDENTIALS_JSON')
FB_URL = os.environ.get('FIREBASE_DATABASE_URL')
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL') # https://your-app.onrender.com
PORT = int(os.environ.get('PORT', '10000'))

# --- Global Logic Control ---
IS_SENDING = False
SUBJECT, BODY = range(2)

# --- Firebase Initialization ---
try:
    if not firebase_admin._apps:
        cred_dict = json.loads(FB_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FB_URL})
    logger.info("🔥 Firebase Connected!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")

# --- Flask Server for Render Port Health Check ---
server = Flask(__name__)

@server.route('/')
def index():
    return "Bot is running and port is open!", 200

def run_flask():
    server.run(host="0.0.0.0", port=PORT)

# --- Helper Functions ---
def is_owner(uid):
    return str(uid) == str(OWNER_ID)

async def get_active_gas_url():
    urls = db.reference('config/gas_url').get()
    if urls: return urls
    return os.environ.get('GAS_URL')

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --- Async Email Sender ---
async def send_email_async(to_email, subject, body_html):
    url = await get_active_gas_url()
    if not url: return "URL_MISSING"
    
    async with httpx.AsyncClient() as client:
        try:
            payload = {"to": to_email, "subject": subject, "body": body_html}
            response = await client.post(url, json=payload, timeout=40)
            if response.status_code == 200:
                res_data = response.json()
                if res_data.get("status") == "success": return "SUCCESS"
                if "limit" in res_data.get("message", "").lower(): return "LIMIT_REACHED"
            return "GAS_ERROR"
        except Exception as e:
            return "CONNECTION_FAILED"

# --- Menus & Handlers ---
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🚀 Start Sending", callback_data="start_send"),
         InlineKeyboardButton("🛑 Stop", callback_data="stop_send")],
        [InlineKeyboardButton("📊 Statistics", callback_data="show_stats"),
         InlineKeyboardButton("⚙️ Set Content", callback_data="set_content")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def process_email_queue(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    config = db.reference('email_config').get()
    all_leads = db.reference('scraped_emails').get()

    if not config or not all_leads:
        await context.bot.send_message(chat_id, "⚠️ ডাটা বা কন্টেন্ট নেই!")
        IS_SENDING = False
        return

    count = 0
    for key, data in all_leads.items():
        if not IS_SENDING: break
        if data.get('status') == 'sent': continue

        email = data.get('email')
        app_name = data.get('app_name', 'Developer')
        final_subject = config['subject'].replace('{app_name}', app_name)
        final_body = config['body'].replace('{app_name}', app_name)

        status = await send_email_async(email, final_subject, final_body)
        if status == "SUCCESS":
            db.reference(f'scraped_emails/{key}').update({'status': 'sent', 'sent_at': datetime.now().isoformat()})
            count += 1
        elif status == "LIMIT_REACHED":
            await context.bot.send_message(chat_id, "🚨 লিমিট শেষ!")
            break
        await asyncio.sleep(random.randint(40, 70))

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 শেষ হয়েছে! পাঠানো হয়েছে: {count}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update.effective_user.id):
        await update.message.reply_text("🤖 **Email Bot Pro Online**", reply_markup=main_menu(), parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    query = update.callback_query
    await query.answer()
    if query.data == "start_send":
        if not IS_SENDING:
            IS_SENDING = True
            context.job_queue.run_once(process_email_queue, 1, chat_id=query.message.chat_id)
            await query.edit_message_text("🚀 পাঠানো শুরু হয়েছে।")
    elif query.data == "stop_send":
        IS_SENDING = False
        await query.edit_message_text("🛑 থামানো হয়েছে।")
    elif query.data == "show_stats":
        leads = db.reference('scraped_emails').get() or {}
        sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
        await query.message.reply_text(f"📊 মোট লিড: {len(leads)}\n✅ পাঠানো: {sent}")

async def set_content_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("📝 Subject লিখুন:")
    return SUBJECT

async def set_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_sub'] = update.message.text
    await update.message.reply_text("🔗 Body (HTML) লিখুন:")
    return BODY

async def set_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.reference('email_config').set({'subject': context.user_data['temp_sub'], 'body': update.message.text})
    await update.message.reply_text("✅ সেভ হয়েছে!", reply_markup=main_menu())
    return ConversationHandler.END

def main():
    # Start Flask in a separate thread
    Thread(target=run_flask).start()

    app = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_content_start, pattern="^set_content$")],
        states={
            SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_subject)],
            BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_body)],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    # Use Polling if testing locally, Webhook for Render
    if RENDER_URL:
        # রেন্ডার পোর্টে বট কানেক্ট করার জন্য
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get('PORT', 8443)), # আলাদা ইন্টারনাল পোর্ট
            url_path=TOKEN,
            webhook_url=f"{RENDER_URL}/{TOKEN}"
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
