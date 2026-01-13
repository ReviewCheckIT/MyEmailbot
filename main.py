# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import smtplib
import csv
import io
import sys
from email.message import EmailMessage
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
EMAIL_USER = os.environ.get('EMAIL_USER') 
EMAIL_PASS = os.environ.get('EMAIL_PASS') 

# Render-এর জন্য পোর্ট এবং ইউআরএল সেটআপ
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

# --- Email Logic ---
async def send_mail_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    
    ref = db.reference('scraped_emails')
    data = ref.get()
    if not data:
        await update.message.reply_text("❌ ডাটাবেজে কোনো ইমেইল নেই।")
        return

    # --- আপনার তথ্য এখানে দিন (পরবর্তীতে এডিট করতে পারবেন) ---
    WA_LINK = "https://wa.me/8801700000000" 
    TG_LINK = "https://t.me/your_username"   
    WEB_LINK = "https://yourwebsite.com"    
    MY_NAME = "Your Name/Agency"        

    subject_template = "Boost Your App Ranking & Reviews: {app_name}"
    
    html_template = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #1a73e8;">Hello {{dev_name}},</h2>
        <p>I hope you're doing well. I recently found your app <b>"{{app_name}}"</b> on the Play Store.</p>
        <p>I specialize in <b>App Store Optimization (ASO)</b> and <b>Review Services</b>. I can help your app get high-quality ratings and authentic reviews, which will significantly improve your ranking and user trust.</p>
        
        <div style="background: #f1f3f4; padding: 15px; border-radius: 10px; border: 1px solid #ddd;">
          <p><b>Interested? Let's discuss further:</b></p>
          <p>✅ <b>WhatsApp:</b> <a href="{WA_LINK}">Chat Now</a></p>
          <p>✅ <b>Telegram:</b> <a href="{TG_LINK}">Contact via Telegram</a></p>
          <p>✅ <b>Website:</b> <a href="{WEB_LINK}">Visit Our Portfolio</a></p>
        </div>
        <br>
        <p>Looking forward to working with you!</p>
        <p>Best Regards,<br><b>{MY_NAME}</b></p>
      </body>
    </html>
    """

    await update.message.reply_text("🚀 ইমেইল ক্যাম্পেইন শুরু হয়েছে...")
    sent_count = 0

    for key, info in data.items():
        if info.get('sent') == True: continue

        recipient_email = info.get('email')
        app_name = info.get('app_name', 'Your App')
        dev_name = info.get('dev', 'Developer')

        msg = EmailMessage()
        msg['Subject'] = subject_template.format(app_name=app_name)
        msg['From'] = EMAIL_USER
        msg['To'] = recipient_email
        msg.add_alternative(html_template.format(app_name=app_name, dev_name=dev_name), subtype='html')

        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
                smtp.login(EMAIL_USER, EMAIL_PASS)
                smtp.send_message(msg)
            
            ref.child(key).update({'sent': True, 'sent_at': datetime.now().isoformat()})
            sent_count += 1
            await asyncio.sleep(5) # নিরাপত্তা বিরতি

            if sent_count % 10 == 0:
                await update.message.reply_text(f"📈 স্ট্যাটাস: {sent_count}টি ইমেইল পাঠানো সম্পন্ন।")
        except Exception as e:
            logger.error(f"Error for {recipient_email}: {e}")
            continue

    await update.message.reply_text(f"✅ সম্পন্ন! মোট {sent_count}টি ইমেইল পাঠানো হয়েছে।")

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
    await update.message.reply_document(document=output, caption="📊 সফলভাবে পাঠানো ইমেইলের লিস্ট।")

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_owner(u.effective_user.id):
        await u.message.reply_text("বট অনলাইন! \n/send_emails দিয়ে কাজ শুরু করুন।")

def main():
    if not TOKEN:
        logger.error("No Bot Token found!")
        return

    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("send_emails", send_mail_task))
    app.add_handler(CommandHandler("export_sent", export_sent))

    # Render Deployment (Port 10000 binding)
    if RENDER_URL:
        logger.info(f"Starting Webhook on port {PORT}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{RENDER_URL}/{TOKEN}"
        )
    else:
        logger.info("Starting Polling (Local Mode)")
        app.run_polling()

if __name__ == "__main__":
    main()
