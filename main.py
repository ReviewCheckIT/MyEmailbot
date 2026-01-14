# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import random
import requests
from datetime import datetime

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

# Mailjet Credentials from Render Env
MJ_API_KEY = os.environ.get('MJ_API_KEY')
MJ_API_SECRET = os.environ.get('MJ_API_SECRET')
SENDER_EMAIL = os.environ.get('EMAIL_USER') # আপনার ভেরিফাইড জিমেইল

# --- Global Control ---
IS_SENDING = False

# --- Firebase Initialization ---
try:
    if not firebase_admin._apps:
        cred_dict = json.loads(FB_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FB_URL})
    logger.info("🔥 Firebase Connected Successfully!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")

def is_owner(uid):
    return str(uid) == str(OWNER_ID)

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if OWNER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ এরর: `{context.error}`")
        except: pass

# --- Mailjet API Function (Stable HTTP Method) ---
def send_email_via_api(to_email, subject, body_html):
    try:
        url = "https://api.mailjet.com/v3.1/send"
        auth = (MJ_API_KEY, MJ_API_SECRET)
        data = {
            'Messages': [
                {
                    "From": {
                        "Email": SENDER_EMAIL,
                        "Name": "App Growth Specialist"
                    },
                    "To": [{"Email": to_email}],
                    "Subject": subject,
                    "HTMLPart": body_html
                }
            ]
        }
        response = requests.post(url, auth=auth, json=data, timeout=25)
        if response.status_code == 200:
            return True
        else:
            logger.error(f"❌ Mailjet API Error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ API Connection Error: {e}")
        return False

# --- Human-like Background Processor ---
async def process_email_queue(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    
    config = db.reference('email_config').get()
    if not config or 'subject' not in config:
        await context.bot.send_message(chat_id, "⚠️ ইমেইল কন্টেন্ট সেট করা নেই! /set_content ব্যবহার করুন।")
        IS_SENDING = False
        return

    ref = db.reference('scraped_emails')
    all_leads = ref.get()

    if not all_leads:
        await context.bot.send_message(chat_id, "❌ ডাটাবেজে কোনো ইমেল খুঁজে পাওয়া যায়নি।")
        IS_SENDING = False
        return

    count = 0
    await context.bot.send_message(chat_id, "🚀 API-র মাধ্যমে ইমেইল পাঠানো শুরু হয়েছে। এটি মানুষের মতো ধীরগতিতে কাজ করবে।")

    for key, data in all_leads.items():
        if not IS_SENDING: break
        if data.get('status') == 'sent': continue

        email = data.get('email')
        app_name = data.get('app_name', 'Developer')
        # সাবজেক্ট এবং বডিতে {app_name} রিপ্লেস করা
        final_subject = config['subject'].replace('{app_name}', app_name)
        final_body = config['body'].replace('{app_name}', app_name)

        if send_email_via_api(email, final_subject, final_body):
            ref.child(key).update({
                'status': 'sent', 
                'sent_at': datetime.now().isoformat(),
                'sender_used': SENDER_EMAIL
            })
            count += 1
            if count % 10 == 0:
                await context.bot.send_message(chat_id, f"✅ সফলভাবে {count} টি পাঠানো হয়েছে।")
                # প্রতি ১০টি মেইল পর মানুষের মতো ২ মিনিটের লম্বা বিরতি
                await asyncio.sleep(random.randint(120, 180))
        
        # প্রতি মেইলের মাঝে ৩০ থেকে ৯০ সেকেন্ডের র‍্যান্ডম গ্যাপ
        await asyncio.sleep(random.randint(30, 90))

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 কাজ শেষ! এই সেশনে মোট পাঠানো হয়েছে: {count}")

# --- Handlers ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    await u.message.reply_text("🤖 API ইমেইল বট অনলাইন।\n\n/set_content - কন্টেন্ট সেট করুন\n/start_sending - পাঠানো শুরু\n/stop_sending - থামানো\n/stats - বর্তমান অবস্থা")

async def stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    leads = db.reference('scraped_emails').get() or {}
    total = len(leads)
    sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
    await u.message.reply_text(f"📊 মোট ইমেইল: {total}\n✅ পাঠানো হয়েছে: {sent}\n⏳ বাকি আছে: {total-sent}")

async def start_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if not is_owner(u.effective_user.id): return
    if IS_SENDING:
        await u.message.reply_text("⚠️ অলরেডি ইমেইল পাঠানো হচ্ছে।")
        return
    IS_SENDING = True
    c.job_queue.run_once(process_email_queue, 1, chat_id=u.effective_chat.id)
    await u.message.reply_text("🚀 কিউতে যুক্ত করা হয়েছে। কাজ শুরু হচ্ছে...")

async def stop_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if not is_owner(u.effective_user.id): return
    IS_SENDING = False
    await u.message.reply_text("🛑 পাঠানো বন্ধ করা হচ্ছে (বর্তমান মেইলটি পাঠানোর পর থেমে যাবে)।")

# --- Conversation for Content ---
SUBJECT, BODY = range(2)
async def set_c(u, c): 
    if not is_owner(u.effective_user.id): return
    await u.message.reply_text("ইমেইল সাবজেক্ট (Subject) দিন:")
    return SUBJECT
async def set_s(u, c):
    c.user_data['temp_s'] = u.message.text
    await u.message.reply_text("ইমেইল বডি (HTML Body) দিন:")
    return BODY
async def set_b(u, c):
    db.reference('email_config').set({'subject': c.user_data['temp_s'], 'body': u.message.text})
    await u.message.reply_text("✅ টেমপ্লেট সফলভাবে সেভ হয়েছে।")
    return ConversationHandler.END

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("start_sending", start_sending))
    app.add_handler(CommandHandler("stop_sending", stop_sending))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('set_content', set_c)],
        states={SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_s)],
                BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_b)]},
        fallbacks=[CommandHandler('cancel', lambda u,c: ConversationHandler.END)]
    ))

    if RENDER_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN[-10:], 
                        webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
