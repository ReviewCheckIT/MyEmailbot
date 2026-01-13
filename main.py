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

# --- Env Variables ---
TOKEN = os.environ.get('EMAIL_BOT_TOKEN') 
OWNER_ID = os.environ.get('BOT_OWNER_ID')
FB_JSON = os.environ.get('FIREBASE_CREDENTIALS_JSON')
FB_URL = os.environ.get('FIREBASE_DATABASE_URL')
EMAIL_USER = os.environ.get('EMAIL_USER') 
EMAIL_PASS = os.environ.get('EMAIL_PASS') 

PORT = int(os.environ.get('PORT', '10000'))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')

# --- Firebase Init ---
try:
    if not firebase_admin._apps:
        cred_dict = json.loads(FB_JSON)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {'databaseURL': FB_URL})
    logger.info("🔥 Firebase Connected Successfully!")
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
    WA_LINK = "https://wa.me/message/4TY7FZQW5ADQF1" 
    TG_LINK = "https://t.me/Rifat8289"   
    WEB_LINK = "https://brotheritltd.com"    
    MY_NAME = "MD SHAKIL"        

    subject_template = "Boost Your App Ranking & Reviews: {app_name}"
    
    html_template = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #1a73e8;">Hello {{dev_name}},</h2>
        <p>I found your app <b>"{{app_name}}"</b> on the Play Store. It has great potential!</p>
        <p>I specialize in <b>App Store Optimization (ASO)</b> and can help you get authentic reviews and higher rankings.</p>
        <div style="background: #f1f3f4; padding: 15px; border-radius: 10px; border: 1px solid #ddd;">
          <p>✅ <b>WhatsApp:</b> <a href="{WA_LINK}">Chat Now</a></p>
          <p>✅ <b>Telegram:</b> <a href="{TG_LINK}">Contact Now</a></p>
          <p>✅ <b>Website:</b> <a href="{WEB_LINK}">Our Portfolio</a></p>
        </div>
        <br><p>Best Regards,<br><b>{MY_NAME}</b></p>
      </body>
    </html>
    """

    await update.message.reply_text("🚀 ইমেইল পাঠানো শুরু হচ্ছে...")
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
            # SMTP_SSL (465) এর বদলে SMTP (587) ব্যবহার করছি আরও ভালো কানেকশনের জন্য
            with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
                smtp.starttls() # কানেকশন সিকিউর করা
                smtp.login(EMAIL_USER, EMAIL_PASS)
                smtp.send_message(msg)
            
            # ডাটাবেজে আপডেট
            ref.child(key).update({'sent': True, 'sent_at': datetime.now().isoformat()})
            sent_count += 1
            
            logger.info(f"✅ Sent to: {recipient_email}")
            await asyncio.sleep(5) 

            # আপনার ৫টি ইমেইল যেহেতু, তাই প্রতি ইমেইলেই একটা মেসেজ পাক যাতে আপনি বুঝতে পারেন
            await update.message.reply_text(f"📈 পাঠানো হয়েছে: {sent_count}টি ({recipient_email})")

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
    await update.message.reply_document(document=output, caption="📊 সফল ইমেইল লিস্ট।")

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_owner(u.effective_user.id):
        await u.message.reply_text("বট অনলাইন! \n/send_emails - ইমেইল পাঠাতে \n/export_sent - লিস্ট পেতে")

def main():
    if not TOKEN:
        logger.error("No Bot Token found!")
        sys.exit(1)

    # v21.x এর জন্য ApplicationBuilder ব্যবহার
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("send_emails", send_mail_task))
    application.add_handler(CommandHandler("export_sent", export_sent))

    if RENDER_URL:
        # Render এর জন্য Webhook
        logger.info(f"Starting Webhook on port {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{RENDER_URL}/{TOKEN}",
            drop_pending_updates=True
        )
    else:
        # লোকাল পিসির জন্য Polling
        application.run_polling()

if __name__ == "__main__":
    main()
