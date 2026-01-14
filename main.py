# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import requests # API কল করার জন্য
import csv
import io
import sys
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, db
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Env Variables (Render-এ সেট করবেন) ---
TOKEN = os.environ.get('EMAIL_BOT_TOKEN') 
OWNER_ID = os.environ.get('BOT_OWNER_ID')
FB_JSON = os.environ.get('FIREBASE_CREDENTIALS_JSON')
FB_URL = os.environ.get('FIREBASE_DATABASE_URL')
MAILTRAP_API_TOKEN = os.environ.get('MAILTRAP_API_TOKEN') # 23b3c7053f8b34fbff24b54fdf04315f

PORT = int(os.environ.get('PORT', '10000'))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')

# --- Firebase Init ---
try:
    if not firebase_admin._apps:
        cred_dict = json.loads(FB_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FB_URL})
    logger.info("🔥 Firebase Connected!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")
    sys.exit(1)

def is_owner(uid):
    return str(uid) == str(OWNER_ID)

# --- Mailtrap API Sending Logic ---
def send_via_mailtrap_api(to_email, dev_name, app_name):
    url = "https://send.api.mailtrap.io/api/send"
    
    # আপনার সার্ভিস সম্পর্কে প্রফেশনাল টেক্সট
    wa_link = "https://wa.me/8801647323233" # এখানে আপনার নাম্বার দিন
    tg_link = "https://t.me/t.me/AfMdshakil"   # আপনার টেলিগ্রাম দিন

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>Hello {dev_name},</h2>
        <p>I found your app <b>"{app_name}"</b> on the Play Store. It has great potential!</p>
        <p>I provide professional <b>App Review & Ranking Services</b>. I can help your app get high-quality ratings and authentic reviews to boost user trust and search ranking.</p>
        <p><b>Contact Me:</b><br>
        WhatsApp: <a href="{wa_link}">Chat Now</a><br>
        Telegram: <a href="{tg_link}">Send Message</a></p>
        <p>Best Regards,<br>App Marketing Specialist</p>
      </body>
    </html>
    """

    payload = {
        "from": {"email": "hello@demomailtrap.co", "name": "App Services"},
        "to": [{"email": to_email}],
        "subject": f"Boost Your App Ranking: {app_name}",
        "html": html_content,
        "category": "App Marketing"
    }

    headers = {
        "Authorization": f"Bearer {MAILTRAP_API_TOKEN}",
        "Content-Type": application/json
    }

    response = requests.post(url, json=payload, headers=headers)
    return response

# --- Email Send Task ---
async def send_mail_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    
    ref = db.reference('scraped_emails')
    data = ref.get()
    if not data:
        await update.message.reply_text("❌ ডাটাবেজে কোনো ইমেইল নেই।")
        return

    await update.message.reply_text("🚀 Mailtrap API এর মাধ্যমে ইমেইল পাঠানো শুরু হচ্ছে...")
    sent_count = 0

    for key, info in data.items():
        if info.get('sent') == True: continue

        recipient_email = info.get('email')
        app_name = info.get('app_name', 'Your App')
        dev_name = info.get('dev', 'Developer')

        try:
            res = send_via_mailtrap_api(recipient_email, dev_name, app_name)
            
            if res.status_code == 200:
                ref.child(key).update({'sent': True, 'sent_at': datetime.now().isoformat()})
                sent_count += 1
                await update.message.reply_text(f"✅ পাঠানো হয়েছে: {recipient_email}")
            else:
                logger.error(f"API Error: {res.text}")

            await asyncio.sleep(2) # API ব্যবহারে সময় কম লাগে, তাই ২ সেকেন্ড গ্যাপই যথেষ্ট

        except Exception as e:
            logger.error(f"Error: {e}")
            continue

    await update.message.reply_text(f"🏁 সম্পন্ন! মোট {sent_count}টি ইমেইল পাঠানো হয়েছে।")

# --- Commands ---
async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_owner(u.effective_user.id):
        await u.message.reply_text("বট অনলাইন! ইমেইল পাঠাতে /send_emails কমান্ড দিন।")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("send_emails", send_mail_task))

    if RENDER_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"{RENDER_URL}/{TOKEN}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
