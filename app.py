import os
import re
import time
import threading
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse

from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
    ChatMemberHandler,
)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

# =============================
# WEB SERVER (กัน Render sleep)
# =============================

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Bot is running!"

def run_web():
    app_web.run(host="0.0.0.0", port=PORT)

# =============================
# STORAGE
# =============================

join_times = {}
user_messages = defaultdict(list)

# =============================
# SETTINGS
# =============================

SUSPICIOUS_WORDS = [
    "ver video",
    "watch",
    "watch video",
    "ดูฟรี",
    "free",
    "เด็กนักเรียน",
    "เด็ก",
    "นักเรียน",
    "ฟรี",
    "หีเด็ก",
    "คลิกปุ่ม",
]

LINK_PATTERNS = [
    r"http[s]?://",
    r"www\.",
    r"t\.me/",
    r"@\w+",
    r"\b[a-zA-Z0-9-]+\.(com|net|org|xyz|top|club|site|vip|online)\b"
]

ALLOWED_DOMAINS = [
    "t-hoy.com",
    "mangath.live",
    "นางแบบ.live",
    "taluijapan.com",
    "youfilx.com",
    "cc-cos.com",
    "kamouth.com",
    "gamemonday.live",
    "catdumb.live",
    "gaythai.live",
    "figmodel.com",
    "hooligril.com",
    "tidroam.com",
    "zaranua.live",
    "kinnaii.com",
    "mmmoy.com",
    "ฟิวแฟน.live",
    "1000drink.com",
    "ppnewsth.com",
    "แจกวาร์ป.live",
    "longsanam.com",
    "toodtidgameth.com",
    "ttphoo.com",
    "larnom.com",
    "ockock.com",
    "kongcheer.com",
    "madamporns.com",
    "โอลี่แฟน.live",
    "โกดังญี่ปุ่น.com",
    "stmgamer.com",
    "doofarang.com",
    "fansav.com",
    "doophuchais.com",
    "tingkorea.com",
    "avidol.live",
    "onlyfanxxx.com",
    "zapgern.com",
    "gumpun.com",
    "madamboys.com",
    "peekjkt.com",
    "sudpung.com",
    "gxvdo.com",
    "24-jav.ch",
    "xn--72c9aea1jwd.live",
    "xn--q3cla5a5dzd.live",
    "xn--12cn2d5at0e3e4d.live",
    "xn--q3clr5a4b7dd5c.live",
    "xn--12cms0a1al5m8a2a6g6cc.com",
]

# =============================
# UTIL FUNCTIONS
# =============================

def normalize_text(text):
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.lower()

def extract_urls(text):
    return re.findall(r"(https?://[^\s]+|www\.[^\s]+)", text)

def is_allowed(url):
    if not url.startswith("http"):
        url = "http://" + url

    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    return domain in ALLOWED_DOMAINS

def contains_link(text):
    text = normalize_text(text)
    for pattern in LINK_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

def contains_suspicious_words(text):
    text = normalize_text(text)
    for word in SUSPICIOUS_WORDS:
        if word in text:
            return True
    return False

def is_spam(user_id, text):
    now = time.time()
    user_messages[user_id] = [
        t for t in user_messages[user_id]
        if now - t[0] < 10
    ]
    user_messages[user_id].append((now, text))
    return len(user_messages[user_id]) > 3

# =============================
# TRACK JOIN TIME
# =============================

async def track_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.chat_member.new_chat_member.status == "member":
        user_id = update.chat_member.new_chat_member.user.id
        join_times[user_id] = time.time()

# =============================
# MAIN FILTER
# =============================

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    user = message.from_user
    chat_id = update.effective_chat.id
    text = message.text

    # 🔴 บล็อก Forward
    if message.forward_from or message.forward_from_chat:
        await message.delete()
        await context.bot.ban_chat_member(chat_id, user.id)
        return

    # 🔴 บล็อกปุ่ม
    if message.reply_markup:
        await message.delete()
        await context.bot.ban_chat_member(chat_id, user.id)
        return

    # 🔴 คนเพิ่งเข้า < 60 วิ แล้วส่งลิงก์
    if user.id in join_times:
        if time.time() - join_times[user.id] < 60:
            if contains_link(text):
                await message.delete()
                await context.bot.ban_chat_member(chat_id, user.id)
                return

    # 🔴 ลิงก์ทั่วไป (ไม่อยู่ allow list)
    urls = extract_urls(text)
    for url in urls:
        if not is_allowed(url):
            await message.delete()
            await context.bot.ban_chat_member(chat_id, user.id)
            return

    # 🔴 คำต้องสงสัย
    if contains_suspicious_words(text):
        await message.delete()
        await context.bot.ban_chat_member(chat_id, user.id)
        return

    # 🔴 spam flood
    if is_spam(user.id, text):
        await message.delete()
        await context.bot.ban_chat_member(chat_id, user.id)
        return

# =============================
# MAIN
# =============================

if __name__ == "__main__":

    web_thread = threading.Thread(target=run_web)
    web_thread.start()

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_message))

    print("Bot started...")
    application.run_polling()
