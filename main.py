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
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
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
    logger.info("🔥 Firebase Connected!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")

# --- Helper Functions ---
def is_owner(uid):
    return str(uid) == str(OWNER_ID)

async def get_active_gas_url():
    """ডাটাবেজ বা এনভায়রনমেন্ট থেকে সচল GAS URL নেয়"""
    # প্রথমে ডাটাবেজ চেক করে (যদি আপনি একাধিক লিঙ্ক রাখেন)
    urls = db.reference('config/gas_url').get()
    if urls:
        return urls
    return os.environ.get('GAS_URL')

# --- Error Handler (আপনার সমস্যার সমাধান) ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """বটের যেকোনো এরর লগ করা এবং হ্যান্ডেল করা"""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    # মালিককে এরর মেসেজ পাঠানো (অপশনাল)
    if update and isinstance(update, Update) and update.effective_user:
        await context.bot.send_message(
            chat_id=OWNER_ID, 
            text=f"⚠️ একটি এরর হয়েছে: `{str(context.error)}`",
            parse_mode="Markdown"
        )

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
            logger.error(f"Connection Error: {e}")
            return "CONNECTION_FAILED"

# --- Keyboard Menus ---
def main_menu():
    keyboard = [
        [InlineKeyboardButton("🚀 Start Sending", callback_data="start_send"),
         InlineKeyboardButton("🛑 Stop", callback_data="stop_send")],
        [InlineKeyboardButton("📊 Statistics", callback_data="show_stats"),
         InlineKeyboardButton("⚙️ Set Content", callback_data="set_content")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Background Queue Processor ---
async def process_email_queue(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    
    config = db.reference('email_config').get()
    if not config:
        await context.bot.send_message(chat_id, "⚠️ Content সেট করা নেই! /start এ গিয়ে সেট করুন।")
        IS_SENDING = False
        return

    # অন্য বট (Scraper) থেকে আসা লিডগুলো নেওয়া
    all_leads = db.reference('scraped_emails').get()
    if not all_leads:
        await context.bot.send_message(chat_id, "❌ ডাটাবেজে কোনো লিড নেই।")
        IS_SENDING = False
        return

    count = 0
    await context.bot.send_message(chat_id, "🚀 কিউ প্রসেস শুরু হয়েছে...")

    for key, data in all_leads.items():
        if not IS_SENDING: break
        if data.get('status') == 'sent': continue

        email = data.get('email')
        app_name = data.get('app_name', 'Developer')
        
        # প্লেসহোল্ডার রিপ্লেস
        final_subject = config['subject'].replace('{app_name}', app_name)
        final_body = config['body'].replace('{app_name}', app_name)

        status = await send_email_async(email, final_subject, final_body)

        if status == "SUCCESS":
            db.reference(f'scraped_emails/{key}').update({
                'status': 'sent',
                'sent_at': datetime.now().isoformat()
            })
            count += 1
            if count % 10 == 0:
                await context.bot.send_message(chat_id, f"✅ {count}টি ইমেইল পাঠানো হয়েছে।")
        
        elif status == "LIMIT_REACHED":
            await context.bot.send_message(chat_id, "🚨 গুগল কোটা শেষ! নতুন GAS লিঙ্ক আপডেট করুন।")
            break
        
        # স্প্যাম প্রটেকশন ডিলে
        await asyncio.sleep(random.randint(45, 90))

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 কাজ শেষ! সেশনে পাঠানো হয়েছে: {count}")

# --- Command & Callback Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text(
        "🤖 **Email Bot Pro Online**\nআপনার সব লিড এবং সেটিংস Firebase থেকে লোড করা হয়েছে।",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    query = update.callback_query
    await query.answer()

    if query.data == "start_send":
        if IS_SENDING:
            await query.edit_message_text("⚠️ অলরেডি পাঠানো হচ্ছে।")
        else:
            IS_SENDING = True
            context.job_queue.run_once(process_email_queue, 1, chat_id=query.message.chat_id)
            await query.edit_message_text("🚀 ইমেইল পাঠানোর কিউ শুরু হয়েছে।", reply_markup=main_menu())

    elif query.data == "stop_send":
        IS_SENDING = False
        await query.edit_message_text("🛑 কিউ বন্ধ করা হচ্ছে...", reply_markup=main_menu())

    elif query.data == "show_stats":
        leads = db.reference('scraped_emails').get() or {}
        total = len(leads)
        sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
        await query.edit_message_text(
            f"📊 **রিপোর্ট**\n\n✅ পাঠানো হয়েছে: {sent}\n⏳ বাকি: {total-sent}\n📂 মোট লিড: {total}",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

# --- Conversation Flow ---
async def set_content_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📝 ইমেইল **Subject** দিন:")
    return SUBJECT

async def set_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_sub'] = update.message.text
    await update.message.reply_text("🔗 এবার ইমেইল **Body (HTML)** দিন:\n(টিপস: `{app_name}` ব্যবহার করতে পারেন)")
    return BODY

async def set_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sub = context.user_data.get('temp_sub')
    body = update.message.text
    db.reference('email_config').set({'subject': sub, 'body': body})
    await update.message.reply_text("✅ ইমেইল কন্টেন্ট সফলভাবে সেভ হয়েছে!", reply_markup=main_menu())
    return ConversationHandler.END

# --- Main Runtime ---
def main():
    app = Application.builder().token(TOKEN).build()

    # Content Setup Conversation
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_content_start, pattern="^set_content$")],
        states={
            SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_subject)],
            BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_body)],
        },
        fallbacks=[],
    )

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Error Handler Registration
    app.add_error_handler(error_handler)

    # Deployment Logic
    if RENDER_URL:
        app.run_webhook(
            listen="0.0.0.0", 
            port=PORT, 
            url_path=TOKEN[-10:], 
            webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}"
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
