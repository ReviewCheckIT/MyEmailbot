# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
import firebase_admin
from firebase_admin import credentials, db

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
# Render-এ এই ভেরিয়েবলগুলো ঠিকমতো সেট করবেন
TOKEN = os.environ.get('EMAIL_BOT_TOKEN')
OWNER_ID = os.environ.get('BOT_OWNER_ID')
FB_JSON = os.environ.get('FIREBASE_CREDENTIALS_JSON')
FB_URL = os.environ.get('FIREBASE_DATABASE_URL')
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')
PORT = int(os.environ.get('PORT', '10000'))

# Gmail Credentials
EMAIL_USER = os.environ.get('EMAIL_USER') 
EMAIL_PASS = os.environ.get('EMAIL_PASS')

# --- Global Variables for Control ---
IS_SENDING = False
TOTAL_SENT_SESSION = 0

# --- Firebase Initialization ---
try:
    if not firebase_admin._apps:
        cred_dict = json.loads(FB_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FB_URL})
    logger.info("🔥 Firebase Database Connected Successfully!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")

# --- Helper: Check Owner ---
def is_owner(uid):
    return str(uid) == str(OWNER_ID)

# --- Email Sending Function (SMTP) ---
def send_email_via_gmail(to_email, subject, body_html):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = to_email
        msg['Subject'] = subject

        # HTML ফরম্যাটে বডি অ্যাটাচ করা
        msg.attach(MIMEText(body_html, 'html'))

        # Gmail SMTP সার্ভারের সাথে কানেকশন (SSL)
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(EMAIL_USER, EMAIL_PASS)
        text = msg.as_string()
        server.sendmail(EMAIL_USER, to_email, text)
        server.quit()
        return True
    except Exception as e:
        logger.error(f"Email Send Failed to {to_email}: {e}")
        return False

# --- Background Task: Bulk Sender ---
async def process_email_queue(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING, TOTAL_SENT_SESSION
    chat_id = context.job.chat_id
    
    # ডাটাবেজ থেকে কনফিগারেশন (Subject & Body) নেওয়া
    config_ref = db.reference('email_config')
    config = config_ref.get()
    
    if not config or 'subject' not in config or 'body' not in config:
        await context.bot.send_message(chat_id, "⚠️ ইমেইল সাবজেক্ট এবং বডি সেট করা নেই! /set_content কমান্ড ব্যবহার করুন।")
        IS_SENDING = False
        return

    subject = config['subject']
    body_template = config['body']

    # ডাটাবেজ থেকে স্ক্র্যাপ করা ইমেইলগুলো আনা
    ref = db.reference('scraped_emails')
    all_leads = ref.get()

    if not all_leads:
        await context.bot.send_message(chat_id, "❌ ডাটাবেজে কোনো লিড পাওয়া যায়নি।")
        IS_SENDING = False
        return

    await context.bot.send_message(chat_id, "🚀 ইমেইল পাঠানো শুরু হচ্ছে... (Safe Mode On)")

    count = 0
    failed = 0
    
    # লুপ চালানো
    for key, data in all_leads.items():
        if not IS_SENDING:
            await context.bot.send_message(chat_id, f"zzZ প্রসেস থামানো হয়েছে। এই সেশনে পাঠানো হয়েছে: {count} টি।")
            break

        # চেক করা ইমেইলটি আগে পাঠানো হয়েছে কিনা
        if data.get('status') == 'sent':
            continue

        email = data.get('email')
        app_name = data.get('app_name', 'App Developer')

        # ইমেইল বডিতে অ্যাপের নাম ডাইনামিক্যালি বসানো (যদি {app_name} থাকে)
        final_body = body_template.replace('{app_name}', app_name)

        # ইমেইল পাঠানো
        success = send_email_via_gmail(email, subject, final_body)

        if success:
            # সফল হলে ডাটাবেজে স্ট্যাটাস আপডেট করা
            ref.child(key).update({
                'status': 'sent',
                'sent_at': datetime.now().isoformat()
            })
            count += 1
            TOTAL_SENT_SESSION += 1
            logger.info(f"✅ Sent to: {email}")
        else:
            failed += 1
            logger.error(f"❌ Failed: {email}")

        # --- SAFETY DELAY (Risk Free) ---
        # 10 থেকে 20 সেকেন্ডের র‍্যান্ডম বিরতি যাতে জিমেইল স্প্যাম না ভাবে
        # 1500 ইমেইল পাঠাতে প্রায় ৬-৮ ঘন্টা লাগবে, কিন্তু এটি ১০০% নিরাপদ।
        # দ্রুত পাঠাতে চাইলে delay কমানো যাবে, কিন্তু রিস্ক বাড়বে।
        delay = random.randint(10, 20) 
        await asyncio.sleep(delay)

        # প্রতি ২০টি ইমেইল পর পর আপডেট জানানো
        if count % 20 == 0:
            await context.bot.send_message(chat_id, f"⏳ আপডেট: {count} টি ইমেইল পাঠানো হয়েছে। চলছে...")

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 **মিশন কমপ্লিট!**\n✅ মোট পাঠানো হয়েছে: {count}\n❌ ব্যর্থ হয়েছে: {failed}")

# --- Handlers ---

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    msg = (
        "📨 **বাল্ক ইমেইল সেন্ডার বট (Firebase Connected)**\n\n"
        "এই বট আপনার `scraped_emails` ডাটাবেজ থেকে ইমেইল নিয়ে পাঠাবে।\n\n"
        "🔹 /set_content - ইমেইলের সাবজেক্ট এবং বডি সেট করুন।\n"
        "🔹 /check_content - বর্তমান ইমেইল টেমপ্লেট দেখুন।\n"
        "🔹 /start_sending - ইমেইল পাঠানো শুরু করুন।\n"
        "🔹 /stop_sending - মাঝপথে থামান।\n"
        "🔹 /stats - কতগুলো পাঠানো বাকি তা দেখুন।"
    )
    await u.message.reply_text(msg)

# --- Conversation Handler for Setting Content ---
SUBJECT, BODY = range(2)

async def set_content_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    await u.message.reply_text("📝 ইমেইলের **Subject** লিখুন:")
    return SUBJECT

async def set_subject(u: Update, c: ContextTypes.DEFAULT_TYPE):
    c.user_data['temp_subject'] = u.message.text
    await u.message.reply_text("📝 এবার ইমেইলের **Body** (HTML Supported) লিখুন:\n\n💡 টিপস: আপনি `{app_name}` লিখলে সেখানে অটোমেটিক অ্যাপের নাম বসে যাবে।")
    return BODY

async def set_body(u: Update, c: ContextTypes.DEFAULT_TYPE):
    subject = c.user_data['temp_subject']
    body = u.message.text # HTML or Plain Text

    # ফায়ারবেজে কনফিগারেশন সেভ করা
    db.reference('email_config').set({
        'subject': subject,
        'body': body,
        'updated_at': datetime.now().isoformat()
    })
    
    await u.message.reply_text(f"✅ **সেটআপ সম্পন্ন!**\n\nSubject: {subject}\n\nBody সেভ করা হয়েছে। /start_sending দিয়ে শুরু করতে পারেন।")
    return ConversationHandler.END

async def cancel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("❌ বাতিল করা হয়েছে।")
    return ConversationHandler.END

# --- Control Commands ---

async def check_content(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    config = db.reference('email_config').get()
    if config:
        await u.message.reply_text(f"📄 **বর্তমান টেমপ্লেট:**\n\n🔹 **Subject:** {config.get('subject')}\n\n🔹 **Body:**\n{config.get('body')}")
    else:
        await u.message.reply_text("⚠️ কোনো টেমপ্লেট সেট করা নেই।")

async def start_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if not is_owner(u.effective_user.id): return
    
    if IS_SENDING:
        await u.message.reply_text("⚠️ ইতিমধ্যে একটি প্রসেস চলছে!")
        return

    IS_SENDING = True
    # ব্যাকগ্রাউন্ড জব হিসেবে রান করা
    c.job_queue.run_once(process_email_queue, 1, chat_id=u.effective_chat.id)
    await u.message.reply_text("✅ রিকোয়েস্ট গ্রহণ করা হয়েছে। ইমেইল পাঠানো শুরু হচ্ছে...")

async def stop_sending(u: Update, c: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    if not is_owner(u.effective_user.id): return
    if IS_SENDING:
        IS_SENDING = False
        await u.message.reply_text("🛑 থামানো হচ্ছে... (পরের লুপে বন্ধ হবে)")
    else:
        await u.message.reply_text("😴 এখন কোনো প্রসেস চলছে না।")

async def stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    
    leads = db.reference('scraped_emails').get()
    if not leads:
        await u.message.reply_text("কোনো ডাটা নেই।")
        return

    total = len(leads)
    sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
    pending = total - sent
    
    await u.message.reply_text(
        f"📊 **লাইভ স্ট্যাটাস**\n\n"
        f"📂 মোট লিড: {total}\n"
        f"✅ পাঠানো হয়েছে: {sent}\n"
        f"⏳ বাকি আছে: {pending}\n"
        f"🚀 এই সেশনে পাঠানো: {TOTAL_SENT_SESSION}"
    )

def main():
    app = Application.builder().token(TOKEN).build()

    # Conversation Handler for Setup
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('set_content', set_content_start)],
        states={
            SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_subject)],
            BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_body)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check_content", check_content))
    app.add_handler(CommandHandler("start_sending", start_sending))
    app.add_handler(CommandHandler("stop_sending", stop_sending))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(conv_handler)

    # Webhook Setup for Render
    if RENDER_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"{RENDER_URL}/{TOKEN}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
