# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import smtplib
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

EMAIL_USER = os.environ.get('EMAIL_USER') 
EMAIL_PASS = os.environ.get('EMAIL_PASS')

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

def is_owner(uid):
    return str(uid) == str(OWNER_ID)

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if OWNER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ এরর ধরা পড়েছে: `{context.error}`")
        except: pass

# --- Safe Email Function (Human Style & Stable Connection) ---
def send_email_human_style(to_email, subject, body_html):
    try:
        msg = MIMEMultipart()
        msg['From'] = f"Support <{EMAIL_USER}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body_html, 'html'))

        # Port 587 and STARTTLS is often more stable on cloud environments
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
        server.starttls() 
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        logger.error(f"❌ Email Failed to {to_email}: {e}")
        return False

# --- Background Task: Human-like Processor ---
async def process_email_queue(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    
    config = db.reference('email_config').get()
    if not config or 'subject' not in config:
        await context.bot.send_message(chat_id, "⚠️ ইমেইল টেমপ্লেট সেট করা নেই! /set_content ব্যবহার করুন।")
        IS_SENDING = False
        return

    ref = db.reference('scraped_emails')
    all_leads = ref.get()

    if not all_leads:
        await context.bot.send_message(chat_id, "❌ ডাটাবেজে কোনো লিড নেই।")
        IS_SENDING = False
        return

    count = 0
    await context.bot.send_message(chat_id, "🚀 মানুষের মতো ইমেইল পাঠানো শুরু হয়েছে।")

    for key, data in all_leads.items():
        if not IS_SENDING: break
        if data.get('status') == 'sent': continue

        email = data.get('email')
        app_name = data.get('app_name', 'Developer')
        final_body = config['body'].replace('{app_name}', app_name)

        if send_email_human_style(email, config['subject'], final_body):
            ref.child(key).update({
                'status': 'sent', 
                'sent_at': datetime.now().isoformat(),
                'sender': EMAIL_USER
            })
            count += 1
            if count % 10 == 0:
                await context.bot.send_message(chat_id, f"✅ সফলভাবে {count} টি পাঠানো হয়েছে।")
                # প্রতি ১০ মেইল পর ১-৩ মিনিটের বড় গ্যাপ
                await asyncio.sleep(random.randint(60, 180))
        
        # প্রতিটি মেইলের মাঝে মানুষের মতো ৩০-৯০ সেকেন্ডের গ্যাপ
        await asyncio.sleep(random.randint(30, 90))

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 কাজ শেষ! সেশনে পাঠানো হয়েছে: {count}")

# --- Command Handlers ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    await u.message.reply_text("🤖 ইমেইল বট অনলাইন।\n\n/set_content - ইমেইল লিখুন\n/check_content - বর্তমান ইমেইল দেখুন\n/start_sending - পাঠানো শুরু\n/stop_sending - থামানো\n/stats - ডাটাবেজ অবস্থা")

async def stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    leads = db.reference('scraped_emails').get() or {}
    total = len(leads)
    sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
    await u.message.reply_text(f"📊 মোট লিড: {total}\n✅ পাঠানো হয়েছে: {sent}\n⏳ বাকি আছে: {total-sent}")

async def start_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if not is_owner(u.effective_user.id): return
    if IS_SENDING:
        await u.message.reply_text("⚠️ অলরেডি প্রসেস চলছে।")
        return
    IS_SENDING = True
    c.job_queue.run_once(process_email_queue, 1, chat_id=u.effective_chat.id)
    await u.message.reply_text("🚀 কিউতে যুক্ত করা হয়েছে। পাঠানো শুরু হচ্ছে।")

async def stop_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if not is_owner(u.effective_user.id): return
    IS_SENDING = False
    await u.message.reply_text("🛑 প্রসেস থামানো হচ্ছে...")

# --- Conversation Handler (Content Setup) ---
SUBJECT, BODY = range(2)
async def set_c(u, c): 
    if not is_owner(u.effective_user.id): return
    await u.message.reply_text("ইমেইলের সাবজেক্ট (Subject) দিন:")
    return SUBJECT
async def set_s(u, c):
    c.user_data['temp_s'] = u.message.text
    await u.message.reply_text("ইমেইলের বডি (HTML Body) দিন:")
    return BODY
async def set_b(u, c):
    db.reference('email_config').set({'subject': c.user_data['temp_s'], 'body': u.message.text})
    await u.message.reply_text("✅ টেমপ্লেট সেভ হয়েছে।")
    return ConversationHandler.END

async def check_content(u, c):
    if not is_owner(u.effective_user.id): return
    cfg = db.reference('email_config').get()
    if cfg:
        await u.message.reply_text(f"📝 সাবজেক্ট: {cfg['subject']}\n\n📄 বডি:\n{cfg['body']}")
    else:
        await u.message.reply_text("⚠️ কিছু সেট করা নেই।")

def main():
    # Build Application with JobQueue support
    app = Application.builder().token(TOKEN).build()
    
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("start_sending", start_sending))
    app.add_handler(CommandHandler("stop_sending", stop_sending))
    app.add_handler(CommandHandler("check_content", check_content))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('set_content', set_c)],
        states={SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_s)],
                BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_b)]},
        fallbacks=[CommandHandler('cancel', lambda u,c: ConversationHandler.END)]
    ))

    if RENDER_URL:
        # Webhook setup for Render
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN[-10:], 
                        webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
