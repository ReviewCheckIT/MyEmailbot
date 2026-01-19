# -*- coding: utf-8 -*-
import logging
import os
import json
import asyncio
import random
import string
import requests
import time
from datetime import datetime
from dotenv import load_dotenv  # ✅ নতুন যোগ করা হয়েছে (লোকাল রান এর জন্য)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    CallbackQueryHandler,
    filters
)
import firebase_admin
from firebase_admin import credentials, db
import google.generativeai as genai

# --- Load Environment Variables ---
load_dotenv()  # .env ফাইল থেকে ভেরিয়েবল লোড করবে

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
GAS_URL_ENV = os.environ.get('GAS_URL')

# Gemini API Keys
GEMINI_KEYS_STR = os.environ.get('GEMINI_API_KEYS', '') 
GEMINI_KEYS = [k.strip() for k in GEMINI_KEYS_STR.split(',') if k.strip()]

# --- Global Control ---
IS_SENDING = False
CURRENT_KEY_INDEX = 0

# --- Firebase Initialization ---
try:
    if not firebase_admin._apps:
        if FB_JSON:
            try:
                # যদি JSON স্ট্রিং হয়
                cred_dict = json.loads(FB_JSON)
                cred = credentials.Certificate(cred_dict)
            except json.JSONDecodeError:
                # যদি JSON ফাইলের পাথ (Path) দেওয়া থাকে
                if os.path.exists(FB_JSON):
                    cred = credentials.Certificate(FB_JSON)
                else:
                    raise Exception("Invalid Firebase JSON or Path")
            
            firebase_admin.initialize_app(cred, {'databaseURL': FB_URL})
            logger.info("🔥 Firebase Connected!")
        else:
            logger.warning("⚠️ FIREBASE_CREDENTIALS_JSON not found!")
except Exception as e:
    logger.error(f"❌ Firebase Error: {e}")

def is_owner(uid):
    return str(uid) == str(OWNER_ID)

# --- AI Helper Functions ---
def get_next_api_key():
    global CURRENT_KEY_INDEX
    if not GEMINI_KEYS: return None
    key = GEMINI_KEYS[CURRENT_KEY_INDEX % len(GEMINI_KEYS)]
    CURRENT_KEY_INDEX += 1
    return key

async def rewrite_email_with_ai(original_sub, original_body, app_name):
    if not GEMINI_KEYS:
        return original_sub, original_body

    for _ in range(3):
        api_key = get_next_api_key()
        if not api_key: break

        try:
            genai.configure(api_key=api_key)
            
            # gemini-pro বাদ দেওয়া হয়েছে কারণ এটি এখন আর কাজ করছে না
            models_to_try = ['gemini-1.5-flash']
            
            prompt = f"""
            Act as a professional app growth manager. Rewrite the email below for an Android App named "{app_name}".
            
            Constraints:
            1. Keep the meaning exactly the same.
            2. Change wording slightly to avoid spam detection.
            3. Tone: Professional and polite.
            4. Output format MUST be: Subject: [New Subject] ||| Body: [New Body]
            
            Original Subject: {original_sub}
            Original Body: {original_body}
            """
            
            for model_name in models_to_try:
                try:
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(prompt)
                    
                    text = response.text.strip()
                    if "|||" in text:
                        parts = text.split("|||")
                        new_sub = parts[0].replace("Subject:", "").strip()
                        new_body = parts[1].replace("Body:", "").strip()
                        new_body = new_body.replace('\n', '<br>')
                        return new_sub, new_body
                except Exception as inner_e:
                    logger.warning(f"Model {model_name} skipped: {inner_e}")
                    continue 

        except Exception as e:
            logger.error(f"AI Key Error: {e}")
            continue

        await asyncio.sleep(1)

    logger.error("❌ All AI keys failed. Using original text.")
    return original_sub, original_body

# --- Helper Functions ---
def get_gas_url():
    if not firebase_admin._apps: return GAS_URL_ENV
    try:
        bot_id = TOKEN.split(':')[0]
        stored_url = db.reference(f'bot_configs/{bot_id}/gas_url').get()
        return stored_url if stored_url else GAS_URL_ENV
    except:
        return GAS_URL_ENV

def generate_random_id(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def call_gas_api(payload):
    url = get_gas_url()
    if not url: return {"status": "error", "message": "GAS URL missing"}
    try:
        response = requests.post(url, json=payload, timeout=60)
        return response.json() if response.status_code == 200 else {"status": "error", "message": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚀 Start Sending", callback_data='btn_start_send')],
        [InlineKeyboardButton("🛑 Stop", callback_data='btn_stop_send')],
        [InlineKeyboardButton("📊 Report", callback_data='btn_stats'),
         InlineKeyboardButton("📝 Set Email", callback_data='btn_set_content')],
        [InlineKeyboardButton("🔄 Reset DB", callback_data='btn_reset_all')]
    ]
    return InlineKeyboardMarkup(keyboard)

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='btn_main_menu')]])

# --- Background Worker ---
async def email_worker(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    bot_id = TOKEN.split(':')[0]
    
    try:
        config = db.reference('shared_config/email_template').get()
        if not config:
            await context.bot.send_message(chat_id, "⚠️ ইমেইল টেম্পলেট নেই! /set_email দিয়ে সেট করুন।")
            IS_SENDING = False
            return

        leads_ref = db.reference('scraped_emails')
        all_leads = leads_ref.get()
    except Exception as e:
        await context.bot.send_message(chat_id, f"🔥 Database Error: {e}")
        IS_SENDING = False
        return

    count = 0
    fail_count = 0

    await context.bot.send_message(chat_id, f"🤖 **AI Sending Started**\nLibrary: google-generativeai\nKeys Loaded: {len(GEMINI_KEYS)}")

    while IS_SENDING:
        # Refresh Data to avoid stale state
        all_leads = leads_ref.get()
        if not all_leads: 
            await context.bot.send_message(chat_id, "🏁 ডাটাবেজ খালি!")
            break
        
        target_key = None
        target_data = None
        
        for k, v in all_leads.items():
            if v.get('status') is None and v.get('processing_by') is None:
                target_key = k
                target_data = v
                break
        
        if not target_key:
            await context.bot.send_message(chat_id, "🏁 সব ইমেইল পাঠানো শেষ!")
            IS_SENDING = False
            break

        # Lock the lead
        leads_ref.child(target_key).update({'processing_by': bot_id})
        
        email = target_data.get('email')
        app_name = target_data.get('app_name', 'App Developer')
        
        # --- AI Processing ---
        orig_sub = config.get('subject', 'Hello').replace('{app_name}', app_name)
        orig_body = config.get('body', 'Hello').replace('{app_name}', app_name)
        
        final_subject, ai_body = await rewrite_email_with_ai(orig_sub, orig_body, app_name)
        
        unique_id = generate_random_id()
        final_body = f"{ai_body}<br><br><span style='display:none;color:transparent;'>RefID: {unique_id}</span>"

        # Sending via GAS
        res = call_gas_api({
            "action": "sendEmail", 
            "to": email, 
            "subject": final_subject, 
            "body": final_body
        })
        
        if res.get("status") == "success":
            leads_ref.child(target_key).update({
                'status': 'sent', 
                'sent_at': datetime.now().isoformat(),
                'sent_by': bot_id,
                'ai_generated': True,
                'processing_by': None
            })
            count += 1
            fail_count = 0 
            
            if count == 1:
                await context.bot.send_message(chat_id, f"✅ ১ম ইমেইল সফল! ({email})\nAI Subject: {final_subject}")
            elif count % 10 == 0:
                await context.bot.send_message(chat_id, f"📊 রিপোর্ট: {count}টি ইমেইল পাঠানো হয়েছে।")

            wait_time = random.randint(180, 300)
            await asyncio.sleep(wait_time)

        else:
            # Release lock on failure
            leads_ref.child(target_key).update({'processing_by': None})
            msg = str(res.get('message', '')).lower()
            fail_count += 1
            
            logger.error(f"Failed ({email}): {msg}")
            
            if "limit" in msg or "quota" in msg:
                await context.bot.send_message(chat_id, "🚨 জিমেইল লিমিট শেষ! GAS URL পরিবর্তন করুন।")
                IS_SENDING = False
                break
            
            if fail_count >= 5:
                await context.bot.send_message(chat_id, "⚠️ টানা ৫টি ইমেইল ফেইল। সিস্টেম চেক করুন।")
                IS_SENDING = False
                break
            
            await asyncio.sleep(60)

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"✅ প্রসেস স্টপ। মোট পাঠানো হয়েছে: {count}")

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text("🤖 **AI Email Sender**\nStatus: Ready", reply_markup=main_menu_keyboard())

async def button_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    query = update.callback_query
    await query.answer()
    
    if query.data == 'btn_main_menu':
        await query.edit_message_text("🤖 **Main Menu**", reply_markup=main_menu_keyboard())
    
    elif query.data == 'btn_start_send':
        if IS_SENDING:
            await query.edit_message_text("⚠️ Already Running!", reply_markup=back_button())
        else:
            if not GEMINI_KEYS:
                await context.bot.send_message(query.message.chat_id, "⚠️ No AI Keys found! Running in basic mode.")
            IS_SENDING = True
            context.job_queue.run_once(email_worker, 1, chat_id=query.message.chat_id)
            await query.edit_message_text("🚀 Starting Sender...", reply_markup=back_button())
            
    elif query.data == 'btn_stop_send':
        IS_SENDING = False
        await query.edit_message_text("🛑 Stopping...", reply_markup=back_button())
        
    elif query.data == 'btn_stats':
        try:
            leads = db.reference('scraped_emails').get() or {}
            total = len(leads)
            sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
            await query.edit_message_text(f"📊 **Stats:**\nTotal: {total}\nSent: {sent}\nPending: {total-sent}", 
                                         reply_markup=back_button())
        except:
             await query.edit_message_text("📊 Stats unavailable (DB Error)", reply_markup=back_button())

    elif query.data == 'btn_set_content':
        await query.edit_message_text("Usage:\n`/set_email Subject | Body`", reply_markup=back_button(), parse_mode="Markdown")
    
    elif query.data == 'btn_reset_all':
        await query.edit_message_text("Type `/confirm_reset` to clear DB.", reply_markup=back_button())

async def update_gas_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    if not c.args:
        await u.message.reply_text("Usage: `/update_gas URL`")
        return
    try:
        bot_id = TOKEN.split(':')[0]
        db.reference(f'bot_configs/{bot_id}/gas_url').set(c.args[0])
        await u.message.reply_text("✅ GAS URL Updated.")
    except Exception as e:
        await u.message.reply_text(f"❌ DB Error: {e}")

async def set_email_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    try:
        content = u.message.text.split('/set_email ', 1)[1]
        if '|' in content:
            sub, body = content.split('|', 1)
            db.reference('shared_config/email_template').set({'subject': sub.strip(), 'body': body.strip()})
            await u.message.reply_text("✅ Email Template Saved.")
        else:
             await u.message.reply_text("❌ Missing `|` separator.")
    except:
        await u.message.reply_text("❌ Invalid format.")

async def confirm_reset_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    try:
        leads = db.reference('scraped_emails').get() or {}
        for k in leads:
            db.reference(f'scraped_emails/{k}').update({'status': None, 'processing_by': None, 'sent_by': None})
        await u.message.reply_text("🔄 Database Reset Done.")
    except Exception as e:
        await u.message.reply_text(f"❌ Error: {e}")

def main():
    if not TOKEN:
        print("❌ Error: EMAIL_BOT_TOKEN not found in environment variables.")
        return
        
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update_gas", update_gas_cmd))
    app.add_handler(CommandHandler("set_email", set_email_cmd))
    app.add_handler(CommandHandler("confirm_reset", confirm_reset_cmd))
    app.add_handler(CallbackQueryHandler(button_tap))

    logger.info("🤖 Bot is starting...")
    
    if RENDER_URL:
        # Webhook Mode (For Render/Server)
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN[-10:], 
                        webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}")
    else:
        # Polling Mode (For Local PC)
        app.run_polling()

if __name__ == "__main__":
    main()
