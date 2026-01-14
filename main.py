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

# --- Global Control ---
IS_SENDING = False

# --- Firebase Initialization ---
try:
    if not firebase_admin._apps:
        cred_dict = json.loads(FB_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FB_URL})
    logger.info("🔥 Firebase Connect সম্পন্ন হয়েছে!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")

def is_owner(uid):
    return str(uid) == str(OWNER_ID)

def get_gas_url():
    """ডাটাবেজ থেকে বর্তমান লিঙ্কটি নিয়ে আসে"""
    return db.reference('config/gas_url').get() or os.environ.get('GAS_URL')

# --- GAS API Caller ---
def call_gas(payload):
    url = get_gas_url()
    if not url: return {"status": "error", "message": "GAS URL সেট করা নেই।"}
    try:
        res = requests.post(url, json=payload, timeout=35)
        return res.json() if res.status_code == 200 else {"status": "error", "message": f"HTTP {res.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- Background Processor ---
async def email_task(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    
    config = db.reference('email_config').get()
    leads = db.reference('scraped_emails').get()
    
    if not config or not leads:
        await context.bot.send_message(chat_id, "⚠️ ডাটা বা কন্টেন্ট খুঁজে পাওয়া যায়নি।")
        IS_SENDING = False
        return

    count = 0
    await context.bot.send_message(chat_id, "🚀 ইমেইল পাঠানোর কাজ শুরু হলো।")

    for key, data in leads.items():
        if not IS_SENDING: break
        if data.get('status') == 'sent': continue

        # কন্টেন্ট তৈরি
        sub = config['subject'].replace('{app_name}', data.get('app_name', 'Developer'))
        body = config['body'].replace('{app_name}', data.get('app_name', 'Developer'))

        res = call_gas({"action": "sendEmail", "to": data.get('email'), "subject": sub, "body": body})
        
        if res.get("status") == "success":
            db.reference(f'scraped_emails/{key}').update({'status': 'sent', 'sent_at': datetime.now().isoformat()})
            count += 1
            if count % 10 == 0:
                await context.bot.send_message(chat_id, f"✅ {count}টি মেইল পাঠানো সম্পন্ন।")
                await asyncio.sleep(random.randint(100, 200)) # বড় বিরতি
        else:
            msg = res.get('message', '').lower()
            if "limit" in msg or "quota" in msg:
                await context.bot.send_message(chat_id, "🚨 লিমিট শেষ! /update_gas দিয়ে নতুন লিঙ্ক দিন।")
                IS_SENDING = False
                break
        
        await asyncio.sleep(random.randint(35, 70)) # সাধারণ বিরতি

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"🏁 কাজ শেষ। সেশনে পাঠানো হয়েছে: {count}")

# --- Handlers ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    msg = (
        "📊 **Email Bot Dashboard**\n\n"
        "🔗 /update_gas - নতুন GAS URL সেট করুন (জিমেইল পরিবর্তনের বিকল্প)\n"
        "🔋 /limit - বর্তমান লিমিট চেক করুন\n"
        "📝 /set_content - ইমেইল বডি/সাবজেক্ট সেট করুন\n"
        "🚀 /start_sending - কাজ শুরু করুন\n"
        "🛑 /stop_sending - কাজ বন্ধ করুন\n"
        "📈 /stats - বর্তমান রিপোর্ট দেখুন"
    )
    await u.message.reply_text(msg, parse_mode="Markdown")

async def update_gas_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    if not c.args:
        await u.message.reply_text("⚠️ কমান্ডের সাথে লিঙ্কটি দিন। যেমন:\n`/update_gas https://script.google.com/...`", parse_mode="Markdown")
        return
    url = c.args[0]
    db.reference('config/gas_url').set(url)
    await u.message.reply_text("✅ GAS URL আপডেট হয়েছে। এখন থেকে এই নতুন লিঙ্কটি ব্যবহার হবে।")

async def get_limit(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    res = call_gas({"action": "getLimit"})
    rem = res.get("remaining", "Unknown")
    await u.message.reply_text(f"📉 আজকের অবশিষ্ট লিমিট: {rem}")

async def stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    leads = db.reference('scraped_emails').get() or {}
    sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
    await u.message.reply_text(f"📊 মোট লিড: {len(leads)}\n✅ পাঠানো হয়েছে: {sent}")

async def set_content(u: Update, c: ContextTypes.DEFAULT_TYPE):
    # সিম্পল প্রম্পট ইমেইল সেট করার জন্য
    await u.message.reply_text("কন্টেন্ট সেট করতে /set_msg ব্যবহার করুন।")

# --- Conversation for Messages ---
async def start_send(u, c):
    global IS_SENDING
    if IS_SENDING: return await u.message.reply_text("ইতিমধ্যেই চলছে।")
    IS_SENDING = True
    c.job_queue.run_once(email_task, 1, chat_id=u.effective_chat.id)
    await u.message.reply_text("🚀 কাজ শুরু হচ্ছে...")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update_gas", update_gas_cmd))
    app.add_handler(CommandHandler("limit", get_limit))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("start_sending", start_send))
    app.add_handler(CommandHandler("stop_sending", lambda u,c: globals().update(IS_SENDING=False)))
    
    # Message Content Handler (Simple Version)
    async def save_msg(u, c):
        try:
            parts = u.message.text.split("|")
            db.reference('email_config').set({'subject': parts[0].strip(), 'body': parts[1].strip()})
            await u.message.reply_text("✅ কন্টেন্ট সেভ হয়েছে।")
        except: await u.message.reply_text("⚠️ ফরম্যাট: `Subject | Body` এভাবে দিন।")
    
    app.add_handler(CommandHandler("set_msg", save_msg))

    if RENDER_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN[-10:], webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}")
    else: app.run_polling()

if __name__ == "__main__": main()
