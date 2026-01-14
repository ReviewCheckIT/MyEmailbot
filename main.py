# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import requests # এটি নতুন যুক্ত হয়েছে API এর জন্য
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

# --- Env Variables ---
TOKEN = os.environ.get('EMAIL_BOT_TOKEN') 
OWNER_ID = os.environ.get('BOT_OWNER_ID')
FB_JSON = os.environ.get('FIREBASE_CREDENTIALS_JSON')
FB_URL = os.environ.get('FIREBASE_DATABASE_URL')
# জিমেইল পাসওয়ার্ডের বদলে এখন Mailtrap API Token ব্যবহার হবে
MAILTRAP_API_TOKEN = os.environ.get('MAILTRAP_API_TOKEN') 

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

# --- Email Send Task ---
async def send_mail_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    
    ref = db.reference('scraped_emails')
    data = ref.get()
    if not data:
        await update.message.reply_text("❌ ডাটাবেজে কোনো ইমেইল নেই।")
        return

    # --- আপনার তথ্যসমূহ ---
    WA_LINK = "https://wa.me/88016323233" 
    TG_LINK = "https://t.me/t.me/AfMdshakil"   
    MY_NAME = "Your Name/MD.SHAKIL"        

    await update.message.reply_text("🚀 ইমেইল ক্যাম্পেইন শুরু হচ্ছে (Mailtrap API)...")
    sent_count = 0

    for key, info in data.items():
        if info.get('sent') == True: continue

        recipient_email = info.get('email')
        app_name = info.get('app_name', 'Your App')
        dev_name = info.get('dev', 'Developer')

        # API Payload তৈরি
        url = "https://send.api.mailtrap.io/api/send"
        
        payload = {
            "from": {"email": "hello@demomailtrap.co", "name": "App Services"},
            "to": [{"email": recipient_email}],
            "subject": f"Boost Your App Ranking & Reviews: {app_name}",
            "html": f"""
            <html>
              <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <h2 style="color: #1a73e8;">Hello {dev_name},</h2>
                <p>I found your app <b>"{app_name}"</b> on the Play Store. It has great potential!</p>
                <p>I specialize in <b>App Store Optimization (ASO)</b> and can help you get authentic reviews and higher rankings.</p>
                <div style="background: #f1f3f4; padding: 15px; border-radius: 10px; border: 1px solid #ddd;">
                  <p>✅ <b>WhatsApp:</b> <a href="{WA_LINK}">Chat Now</a></p>
                  <p>✅ <b>Telegram:</b> <a href="{TG_LINK}">Contact Now</a></p>
                </div>
                <br><p>Best Regards,<br><b>{MY_NAME}</b></p>
              </body>
            </html>
            """,
            "category": "App Marketing"
        }

        headers = {
            "Authorization": f"Bearer {MAILTRAP_API_TOKEN}",
            "Content-Type": "application/json"
        }

        try:
            # API এর মাধ্যমে মেইল পাঠানো
            response = requests.post(url, json=payload, headers=headers)
            
            if response.status_code == 200 or response.status_code == 202:
                ref.child(key).update({'sent': True, 'sent_at': datetime.now().isoformat()})
                sent_count += 1
                await update.message.reply_text(f"✅ পাঠানো হয়েছে: {recipient_email}")
            else:
                logger.error(f"Mailtrap Error: {response.text}")
                await update.message.reply_text(f"⚠️ ব্যর্থ: {recipient_email} (Status: {response.status_code})")
            
            await asyncio.sleep(2) # API এর জন্য ২ সেকেন্ড বিরতিই যথেষ্ট

        except Exception as e:
            logger.error(f"Error for {recipient_email}: {e}")
            continue

    await update.message.reply_text(f"🏁 কাজ শেষ! মোট {sent_count}টি ইমেইল পাঠানো হয়েছে।")

# --- Export & Stats ---
async def export_sent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    data = db.reference('scraped_emails').get()
    if not data: return

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['App Name', 'Email', 'Sent At'])
    for v in data.values():
        if v.get('sent') == True:
            cw.writerow([v.get('app_name'), v.get('email'), v.get('sent_at')])

    output = io.BytesIO(si.getvalue().encode())
    output.name = f"Sent_List_{datetime.now().strftime('%d_%m')}.csv"
    await update.message.reply_document(document=output, caption="📊 সফল ইমেইল লিস্ট।")

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_owner(u.effective_user.id):
        await u.message.reply_text("বট অনলাইন! \n/send_emails - ইমেইল পাঠাতে \n/export_sent - লিস্ট পেতে")

def main():
    if not TOKEN:
        sys.exit(1)

    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("send_emails", send_mail_task))
    application.add_handler(CommandHandler("export_sent", export_sent))

    if RENDER_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{RENDER_URL}/{TOKEN}",
            drop_pending_updates=True
        )
    else:
        application.run_polling()

if __name__ == "__main__":
    main()
