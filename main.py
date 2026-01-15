# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import random
import httpx
from datetime import datetime

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

# --- Environment Variables (From Render Env) ---
TOKEN = os.environ.get('EMAIL_BOT_TOKEN')
OWNER_ID = os.environ.get('BOT_OWNER_ID')
FB_JSON = os.environ.get('FIREBASE_CREDENTIALS_JSON')
FB_URL = os.environ.get('FIREBASE_DATABASE_URL')
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')
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
    logger.info("🔥 Firebase Connected Successfully!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")

# --- Helper Functions ---
def is_owner(uid):
    return str(uid) == str(OWNER_ID)

async def get_active_gas_url():
    """ডাটাবেজ থেকে প্রথম সচল GAS URL টি নেয়"""
    urls = db.reference('config/gas_urls').get()
    if isinstance(urls, list) and len(urls) > 0:
        return urls[0] # বর্তমানে প্রথমটি ব্যবহার করছে, লিমিট শেষ হলে এটি অটো সরাতে হবে
    return os.environ.get('GAS_URL')

# --- Async Email Sender ---
async def send_email_async(to_email, subject, body_html):
    url = await get_active_gas_url()
    if not url: return "URL_MISSING"
    
    async with httpx.AsyncClient() as client:
        try:
            payload = {"to": to_email, "subject": subject, "body": body_html}
            response = await client.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                res_data = response.json()
                if res_data.get("status") == "success": return "SUCCESS"
                if "limit" in res_data.get("message", "").lower(): return "LIMIT_REACHED"
            return "ERROR"
        except Exception as e:
            logger.error(f"HTTP Error: {e}")
            return "CONNECTION_FAILED"

# --- Keyboard Menus ---
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🚀 Start Sending", callback_query_id="start_send"),
         InlineKeyboardButton("🛑 Stop", callback_query_id="stop_send")],
        [InlineKeyboardButton("📊 Statistics", callback_query_id="show_stats"),
         InlineKeyboardButton("⚙️ Set Content", callback_query_id="set_content")],
        [InlineKeyboardButton("🔗 Update GAS URL", callback_query_id="up_gas")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Background Queue Processor ---
async def process_email_queue(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    
    config = db.reference('email_config').get()
    if not config:
        await context.bot.send_message(chat_id, "⚠️ আগে ইমেইল কন্টেন্ট সেট করুন!")
        IS_SENDING = False
        return

    # Scraper বট থেকে আসা ইমেইলগুলো রিড করা
    all_leads = db.reference('scraped_emails').get()
    if not all_leads:
        await context.bot.send_message(chat_id, "❌ কোনো লিড পাওয়া যায়নি।")
        IS_SENDING = False
        return

    count = 0
    await context.bot.send_message(chat_id, "⚡ কিউ প্রসেসিং শুরু হয়েছে...")

    for key, data in all_leads.items():
        if not IS_SENDING: break
        if data.get('status') == 'sent': continue

        email = data.get('email')
        app_name = data.get('app_name', 'User')
        
        # প্লেস হোল্ডার রিপ্লেস {app_name}
        final_subject = config['subject'].replace('{app_name}', app_name)
        final_body = config['body'].replace('{app_name}', app_name)

        status = await send_email_async(email, final_subject, final_body)

        if status == "SUCCESS":
            db.reference(f'scraped_emails/{key}').update({
                'status': 'sent',
                'sent_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            count += 1
            if count % 5 == 0:
                await context.bot.send_message(chat_id, f"✅ {count}টি ইমেইল পাঠানো হয়েছে...")
        
        elif status == "LIMIT_REACHED":
            await context.bot.send_message(chat_id, "🚨 গুগল লিমিট শেষ! অন্য GAS URL যোগ করুন।")
            break

        await asyncio.sleep(random.randint(10, 25)) # স্প্যামিং এড়াতে ডিলে

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 কাজ সম্পন্ন! মোট পাঠানো হয়েছে: {count}")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text(
        "👋 **Email Bot Pro** তে স্বাগতম!\nনিচের মেনু থেকে আপনার অ্যাকশন সিলেক্ট করুন।",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    query = update.callback_query
    await query.answer()

    if query.data == "start_send":
        if IS_SENDING:
            await query.edit_message_text("⚠️ অলরেডি একটি কিউ চলছে।")
        else:
            IS_SENDING = True
            context.job_queue.run_once(process_email_queue, 1, chat_id=query.message.chat_id)
            await query.edit_message_text("🚀 কিউ স্টার্ট করা হয়েছে।", reply_markup=main_menu())

    elif query.data == "stop_send":
        IS_SENDING = False
        await query.edit_message_text("🛑 কিউ থামানো হচ্ছে...", reply_markup=main_menu())

    elif query.data == "show_stats":
        leads = db.reference('scraped_emails').get() or {}
        total = len(leads)
        sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
        await query.edit_message_text(
            f"📊 **পরিসংখ্যান**\n\n✅ পাঠানো হয়েছে: {sent}\n⏳ বাকি আছে: {total-sent}\n📂 মোট লিড: {total}",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
    
    elif query.data == "set_content":
        await query.message.reply_text("📝 ইমেইল **Subject** লিখুন:")
        return SUBJECT

# --- Conversation Flow ---
async def set_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['sub'] = update.message.text
    await update.message.reply_text("🔗 এবার ইমেইল **Body (HTML support)** লিখুন:\n(টিপস: `{app_name}` কিওয়ার্ড ব্যবহার করতে পারেন)")
    return BODY

async def set_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sub = context.user_data.get('sub')
    body = update.message.text
    db.reference('email_config').set({'subject': sub, 'body': body})
    await update.message.reply_text("✅ ইমেইল কন্টেন্ট সফলভাবে সেভ হয়েছে!", reply_markup=main_menu())
    return ConversationHandler.END

# --- Main App ---
def main():
    app = Application.builder().token(TOKEN).build()

    # Conversation for setting email content
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^set_content$")],
        states={
            SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_subject)],
            BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_body)],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_handler))

    if RENDER_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN[-10:], webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
