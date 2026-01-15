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
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
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
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')
PORT = int(os.environ.get('PORT', '10000'))

# --- Global Control ---
IS_SENDING = False

# --- Firebase Initialization ---
try:
    if not firebase_admin._apps:
        cred_dict = json.loads(FB_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FB_URL})
    logger.info("🔥 Firebase Connected!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")

# --- Health Check Server (Render Port Fix) ---
server = Flask(__name__)
@server.route('/')
def health(): return "Bot is Alive", 200

def run_health_server():
    server.run(host="0.0.0.0", port=PORT)

def is_owner(uid):
    return str(uid) == str(OWNER_ID)

def get_gas_url():
    stored_url = db.reference('config/gas_url').get()
    return stored_url if stored_url else os.environ.get('GAS_URL')

# --- Async Email Sender (Improved) ---
async def send_email_via_gas(to_email, subject, body_html):
    current_url = get_gas_url()
    if not current_url: return "URL_MISSING"
    
    async with httpx.AsyncClient() as client:
        try:
            payload = {"to": to_email, "subject": subject, "body": body_html}
            response = await client.post(current_url, json=payload, timeout=40)
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "success": return "SUCCESS"
                if "limit" in result.get("message", "").lower(): return "LIMIT_REACHED"
                return "GAS_ERROR"
            return "HTTP_ERROR"
        except Exception as e:
            return "CONNECTION_FAILED"

# --- Background Processor ---
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
    await context.bot.send_message(chat_id, "🚀 কিউ প্রসেস শুরু হয়েছে।")

    for key, data in all_leads.items():
        if not IS_SENDING: break
        if data.get('status') == 'sent': continue

        email = data.get('email')
        app_name = data.get('app_name', 'Developer')
        final_subject = config['subject'].replace('{app_name}', app_name)
        final_body = config['body'].replace('{app_name}', app_name)

        status = await send_email_via_gas(email, final_subject, final_body)

        if status == "SUCCESS":
            db.reference(f'scraped_emails/{key}').update({
                'status': 'sent', 'sent_at': datetime.now().isoformat()
            })
            count += 1
            if count % 10 == 0:
                await context.bot.send_message(chat_id, f"✅ সফলভাবে {count} টি পাঠানো হয়েছে।")
        elif status == "LIMIT_REACHED":
            await context.bot.send_message(chat_id, "🚨 লিমিট শেষ! /update_gas দিন।")
            break
        
        await asyncio.sleep(random.randint(40, 70))

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 কাজ শেষ! সেশনে পাঠানো হয়েছে: {count}")

# --- Commands ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_owner(u.effective_user.id):
        msg = ("🤖 **Email Bot Pro Online**\n\n/set_content | /update_gas\n/start_sending | /stop_sending\n/stats")
        await u.message.reply_text(msg, parse_mode="Markdown")

async def update_gas(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_owner(u.effective_user.id) and c.args:
        db.reference('config/gas_url').set(c.args[0])
        await u.message.reply_text("✅ GAS URL Updated!")

async def stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    leads = db.reference('scraped_emails').get() or {}
    sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
    await u.message.reply_text(f"📊 মোট লিড: {len(leads)}\n✅ পাঠানো: {sent}\n⏳ বাকি: {len(leads)-sent}")

async def start_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if is_owner(u.effective_user.id) and not IS_SENDING:
        IS_SENDING = True
        c.job_queue.run_once(process_email_queue, 1, chat_id=u.effective_chat.id)
        await u.message.reply_text("🚀 শুরু হয়েছে।")

async def stop_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if is_owner(u.effective_user.id):
        IS_SENDING = False
        await u.message.reply_text("🛑 থামানো হচ্ছে...")

# --- Conversation ---
SUBJECT, BODY = range(2)
async def set_c(u, c): 
    if not is_owner(u.effective_user.id): return
    await u.message.reply_text("Subject:"); return SUBJECT
async def set_s(u, c):
    c.user_data['ts'] = u.message.text
    await u.message.reply_text("Body (HTML):"); return BODY
async def set_b(u, c):
    db.reference('email_config').set({'subject': c.user_data['ts'], 'body': u.message.text})
    await u.message.reply_text("✅ Saved!"); return ConversationHandler.END

# --- Error Handler ---
async def error_handler(update, context):
    logger.error(msg="Exception:", exc_info=context.error)

def main():
    # Start Health Server for Render
    Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("update_gas", update_gas))
    app.add_handler(CommandHandler("start_sending", start_sending))
    app.add_handler(CommandHandler("stop_sending", stop_sending))
    app.add_error_handler(error_handler)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('set_content', set_c)],
        states={SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_s)],
                BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_b)]},
        fallbacks=[]
    ))
    
    if RENDER_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN[-10:], webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
