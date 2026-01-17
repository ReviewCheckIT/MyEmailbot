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
from google.genai import Client  # গুগল জেমিনি লাইব্রেরি

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

# এখানে কমা দিয়ে আলাদা করা অনেকগুলো জেমিনি কী নেওয়া হবে
GEMINI_KEYS_STR = os.environ.get('GEMINI_API_KEYS', '') 
GEMINI_KEYS = [k.strip() for k in GEMINI_KEYS_STR.split(',') if k.strip()]

# --- Global Control ---
IS_SENDING = False
CURRENT_KEY_INDEX = 0

# --- Firebase Initialization ---
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

# --- AI Helper Functions (Magic Layer) ---
def get_next_gemini_client():
    """একের পর এক চাবি ব্যবহার করার ফাংশন"""
    global CURRENT_KEY_INDEX
    if not GEMINI_KEYS: return None
    
    # চাবি রোটেট করা হচ্ছে
    api_key = GEMINI_KEYS[CURRENT_KEY_INDEX % len(GEMINI_KEYS)]
    CURRENT_KEY_INDEX += 1
    try:
        return Client(api_key=api_key)
    except:
        return None

async def rewrite_email_with_ai(original_sub, original_body, app_name):
    """
    AI দিয়ে ইমেইল রি-রাইট করবে যাতে স্প্যাম বক্সে না যায়।
    """
    if not GEMINI_KEYS:
        return original_sub, original_body # চাবি না থাকলে অরিজিনালটাই ফেরত দেবে

    # ৩ বার চেষ্টা করবে ভিন্ন ভিন্ন চাবি দিয়ে
    for _ in range(3):
        client = get_next_gemini_client()
        if not client: break

        prompt = f"""
        Act as a professional business developer. Rewrite the following email subject and body for an Android App named "{app_name}".
        
        Rules:
        1. Keep the core meaning 100% same.
        2. Change words, sentence structure, and tone slightly to make it unique.
        3. Do NOT remove any links or placeholders like {{Link}} if present.
        4. Make it sound human and polite.
        5. Return the result strictly in this format: Subject: [New Subject] ||| Body: [New Body]
        
        Original Subject: {original_sub}
        Original Body: {original_body}
        """
        
        try:
            response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
            text = response.text.strip()
            
            if "|||" in text:
                parts = text.split("|||")
                new_sub = parts[0].replace("Subject:", "").strip()
                new_body = parts[1].replace("Body:", "").strip()
                
                # AI টেক্সট ফরম্যাট ঠিক করা (Markdown remove)
                new_body = new_body.replace('\n', '<br>')
                return new_sub, new_body
        except Exception as e:
            logger.error(f"AI Rewrite Error: {e}")
            continue # পরের চাবি দিয়ে চেষ্টা করবে

    return original_sub, original_body  # সব ফেইল করলে অরিজিনাল

# --- Helper Functions ---
def get_gas_url():
    bot_id = TOKEN.split(':')[0]
    stored_url = db.reference(f'bot_configs/{bot_id}/gas_url').get()
    return stored_url if stored_url else GAS_URL_ENV

def generate_random_id(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def call_gas_api(payload):
    url = get_gas_url()
    if not url: return {"status": "error", "message": "GAS URL missing"}
    try:
        response = requests.post(url, json=payload, timeout=60)
        return response.json() if response.status_code == 200 else {"status": "error"}
    except: return {"status": "error"}

def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚀 AI পাঠানো শুরু (Start)", callback_data='btn_start_send')],
        [InlineKeyboardButton("🛑 থামান (Stop)", callback_data='btn_stop_send')],
        [InlineKeyboardButton("📊 রিপোর্ট", callback_data='btn_stats'),
         InlineKeyboardButton("📝 ইমেইল সেটআপ", callback_data='btn_set_content')],
        [InlineKeyboardButton("🔄 রিসেট ডাটাবেজ", callback_data='btn_reset_all')]
    ]
    return InlineKeyboardMarkup(keyboard)

def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মেনুতে ফিরুন", callback_data='btn_main_menu')]])

# --- Background Worker (Updated with AI) ---
async def email_worker(context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    chat_id = context.job.chat_id
    bot_id = TOKEN.split(':')[0]
    
    config = db.reference('shared_config/email_template').get()
    if not config:
        await context.bot.send_message(chat_id, "⚠️ ইমেইল টেম্পলেট সেট করা নেই! /set_email কমান্ড দিন।")
        IS_SENDING = False
        return

    leads_ref = db.reference('scraped_emails')
    count = 0
    fail_count = 0

    await context.bot.send_message(chat_id, f"🤖 **AI ইঞ্জিন চালু হয়েছে!**\n🔑 লোড করা কী: {len(GEMINI_KEYS)}টি\nএখন প্রতিটি ইমেইল ইউনিক করে পাঠানো হবে...")

    while IS_SENDING:
        # ডাটাবেজ থেকে পেন্ডিং লিড খোঁজা
        all_leads = leads_ref.get()
        if not all_leads: 
            await context.bot.send_message(chat_id, "🏁 ডাটাবেজ খালি!")
            break
        
        target_key = None
        target_data = None
        
        # স্ট্যাটাস নেই এমন লিড খোঁজা
        for k, v in all_leads.items():
            if v.get('status') is None and v.get('processing_by') is None:
                target_key = k
                target_data = v
                break
        
        if not target_key:
            await context.bot.send_message(chat_id, "🏁 সব ইমেইল পাঠানো শেষ!")
            IS_SENDING = False
            break

        # লক করা হচ্ছে যাতে অন্য বট না নেয়
        leads_ref.child(target_key).update({'processing_by': bot_id})
        
        email = target_data.get('email')
        app_name = target_data.get('app_name', 'App Developer')
        
        # --- AI Rewriting Section ---
        orig_sub = config['subject'].replace('{app_name}', app_name)
        orig_body = config['body'].replace('{app_name}', app_name)
        
        # AI কে কল করা হচ্ছে (এটি একটু সময় নেবে)
        final_subject, ai_body = await rewrite_email_with_ai(orig_sub, orig_body, app_name)
        
        # Hidden Tracker (Anti-Spam)
        unique_id = generate_random_id()
        final_body = f"{ai_body}<br><br><span style='display:none;font-size:0px;color:transparent;'>Ref: {unique_id}</span>"

        # GAS এ পাঠানো
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
            fail_count = 0 # রিসেট ফেইল কাউন্টার
            
            if count == 1:
                await context.bot.send_message(chat_id, f"✅ প্রথম AI ইমেইল সফল! ({email})\nসাবজেক্ট ছিল: {final_subject}")
            elif count % 5 == 0:
                await context.bot.send_message(chat_id, f"📊 আপডেট: {count}টি ইমেইল পাঠানো হয়েছে।")

            # --- Smart Delay (Anti-Spam) ---
            # ২ থেকে ৪ মিনিটের র‍্যান্ডম বিরতি
            wait_time = random.randint(120, 240)
            await asyncio.sleep(wait_time)

        else:
            # ফেইল হলে লক ছেড়ে দেওয়া
            leads_ref.child(target_key).update({'processing_by': None})
            msg = res.get('message', '').lower()
            fail_count += 1
            
            logger.error(f"Failed to send to {email}: {msg}")
            
            if "limit" in msg or "quota" in msg:
                await context.bot.send_message(chat_id, "🚨 জিমেইল লিমিট শেষ! /update_gas দিয়ে নতুন লিঙ্ক দিন।")
                IS_SENDING = False
                break
            
            if fail_count >= 5:
                await context.bot.send_message(chat_id, "⚠️ টানা ৫টি ইমেইল ফেইল হয়েছে। নেটওয়ার্ক বা GAS স্ক্রিপ্ট চেক করুন।")
                IS_SENDING = False
                break
            
            await asyncio.sleep(60) # ফেইল হলে ১ মিনিট অপেক্ষা

    IS_SENDING = False
    await context.bot.send_message(chat_id, f"✅ সেশন শেষ! মোট পাঠানো হয়েছে: {count}")

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    await update.message.reply_text("🤖 **AI ইমেইল সেন্ডার (Pro)**\nএখন প্রতিটি ইমেইল হবে ইউনিক!", 
                                   reply_markup=main_menu_keyboard(), parse_mode="Markdown")

async def button_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_SENDING
    query = update.callback_query
    await query.answer()
    
    if query.data == 'btn_main_menu':
        await query.edit_message_text("🤖 **মেনু**", reply_markup=main_menu_keyboard())
    
    elif query.data == 'btn_start_send':
        if IS_SENDING:
            await query.edit_message_text("⚠️ অলরেডি চলছে!", reply_markup=back_button())
        else:
            if not GEMINI_KEYS:
                await context.bot.send_message(query.message.chat_id, "⚠️ সতর্কতা: কোনো Gemini API Key পাওয়া যায়নি! সাধারণ মোডে চলবে।")
            IS_SENDING = True
            context.job_queue.run_once(email_worker, 1, chat_id=query.message.chat_id)
            await query.edit_message_text("🚀 AI সেন্ডিং শুরু হচ্ছে...", reply_markup=back_button())
            
    elif query.data == 'btn_stop_send':
        IS_SENDING = False
        await query.edit_message_text("🛑 থামানো হচ্ছে... বর্তমান কাজটি শেষ করে থামবে।", reply_markup=back_button())
        
    elif query.data == 'btn_stats':
        leads = db.reference('scraped_emails').get() or {}
        total = len(leads)
        sent = sum(1 for v in leads.values() if v.get('status') == 'sent')
        await query.edit_message_text(f"📊 **লাইভ রিপোর্ট:**\n\n🎯 টার্গেট: {total}\n✅ সম্পন্ন: {sent}\n⏳ বাকি: {total-sent}", 
                                     reply_markup=back_button())

    elif query.data == 'btn_set_content':
        await query.edit_message_text("📝 ইমেইল সেট করতে:\n`/set_email Subject | Body`\n\nউদাহরণ:\n`/set_email Partnership for {app_name} | Hi team, saw your app {app_name}...`", 
                                     reply_markup=back_button(), parse_mode="Markdown")
    
    elif query.data == 'btn_reset_all':
        await query.edit_message_text("⚠️ ডাটাবেজ ক্লিয়ার করতে `/confirm_reset` লিখুন।", reply_markup=back_button())

async def update_gas_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    if not c.args:
        await u.message.reply_text("⚠️ ব্যবহার: `/update_gas https://...`")
        return
    bot_id = TOKEN.split(':')[0]
    db.reference(f'bot_configs/{bot_id}/gas_url').set(c.args[0])
    await u.message.reply_text("✅ GAS URL আপডেট হয়েছে।")

async def set_email_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    try:
        content = u.message.text.split('/set_email ', 1)[1]
        if '|' in content:
            sub, body = content.split('|', 1)
            db.reference('shared_config/email_template').set({'subject': sub.strip(), 'body': body.strip()})
            await u.message.reply_text("✅ টেম্পলেট সেভ হয়েছে। AI এখন এটি ব্যবহার করে নতুন ভেরিয়েশন তৈরি করবে।")
        else:
             await u.message.reply_text("❌ `|` চিহ্ন খুঁজে পাওয়া যায়নি। সাবজেক্ট এবং বডির মাঝে `|` দিন।")
    except:
        await u.message.reply_text("❌ ভুল ফরম্যাট!")

async def confirm_reset_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    leads = db.reference('scraped_emails').get() or {}
    count = 0
    for k in leads:
        db.reference(f'scraped_emails/{k}').update({'status': None, 'processing_by': None, 'sent_by': None})
        count += 1
    await u.message.reply_text(f"🔄 {count}টি লিড রিসেট করা হয়েছে। আবার পাঠানো শুরু করতে পারেন।")

def main():
    if not TOKEN: return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update_gas", update_gas_cmd))
    app.add_handler(CommandHandler("set_email", set_email_cmd))
    app.add_handler(CommandHandler("confirm_reset", confirm_reset_cmd))
    app.add_handler(CallbackQueryHandler(button_tap))

    if RENDER_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN[-10:], 
                        webhook_url=f"{RENDER_URL}/{TOKEN[-10:]}")
    else: app.run_polling()

if __name__ == "__main__":
    main()
