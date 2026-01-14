# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import smtplib
import random
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# --- Firebase Init ---
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

# --- Error Handler Function ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    # মালিককে এরর মেসেজ পাঠানো (অপশনাল)
    if OWNER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ **Error Occurred:**\n`{context.error}`")
        except: pass

# --- Safe Email Sending ---
def send_email_via_gmail(to_email, subject, body_html):
    try:
        msg = MIMEMultipart()
        msg['From'] = f"Support <{EMAIL_USER}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body_html, 'html'))

        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15)
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        logger.error(f"❌ Email Error ({to_email}): {e}")
        return False

# --- Bulk Task ---
async def process_email_queue(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    
    config = db.reference('email_config').get()
    if not config or 'subject' not in config:
        await context.bot.send_message(chat_id, "⚠️ Content not set! Use /set_content")
        IS_SENDING = False
        return

    ref = db.reference('scraped_emails')
    all_leads = ref.get()

    if not all_leads:
        await context.bot.send_message(chat_id, "❌ No leads found in DB.")
        IS_SENDING = False
        return

    count = 0
    await context.bot.send_message(chat_id, "🚀 Sending started...")

    for key, data in all_leads.items():
        if not IS_SENDING: break
        if data.get('status') == 'sent': continue

        email = data.get('email')
        app_name = data.get('app_name', 'Developer')
        final_body = config['body'].replace('{app_name}', app_name)

        if send_email_via_gmail(email, config['subject'], final_body):
            ref.child(key).update({'status': 'sent', 'sent_at': datetime.now().isoformat()})
            count += 1
            if count % 20 == 0:
                await context.bot.send_message(chat_id, f"⏳ Sent: {count}")
        
        await asyncio.sleep(random.randint(10, 20))

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 Done! Total sent: {count}")

# --- Commands ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_owner(u.effective_user.id):
        await u.message.reply_text("✅ Bot Online!\n/set_content\n/start_sending\n/stats")

async def stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    leads = db.reference('scraped_emails').get() or {}
    total = len(leads)
    sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
    await u.message.reply_text(f"📊 Total: {total}\n✅ Sent: {sent}\n⏳ Pending: {total-sent}")

async def start_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if is_owner(u.effective_user.id) and not IS_SENDING:
        IS_SENDING = True
        c.job_queue.run_once(process_email_queue, 1, chat_id=u.effective_chat.id)
        await u.message.reply_text("🚀 Start command received.")

async def stop_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if is_owner(u.effective_user.id):
        IS_SENDING = False
        await u.message.reply_text("🛑 Stopping...")

# --- Conv Handlers ---
SUBJECT, BODY = range(2)
async def set_c(u, c): 
    if is_owner(u.effective_user.id): 
        await u.message.reply_text("Subject:")
        return SUBJECT
async def set_s(u, c):
    c.user_data['s'] = u.message.text
    await u.message.reply_text("Body (HTML):")
    return BODY
async def set_b(u, c):
    db.reference('email_config').set({'subject': c.user_data['s'], 'body': u.message.text})
    await u.message.reply_text("✅ Saved!")
    return ConversationHandler.END

def main():
    app = Application.builder().token(TOKEN).build()
    
    # Error Handler রেজিস্টার করা হলো
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
