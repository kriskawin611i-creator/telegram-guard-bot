import os
import re
import time
import threading
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse

from flask import Flask
from telegram import Update, ChatPermissions
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
# WEB SERVER
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
# ALERT SYSTEM
# =============================

async def alert_action(context, chat_id, user, reason, action):

    try:

        name = "Unknown"

        if user:
            name = user.mention_html()

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"""
🚫 GUARD BOT

ตรวจพบการกระทำต้องสงสัย

ผู้ใช้: {name}
เหตุผล: {reason}

การดำเนินการ: {action}

ระบบป้องกันกลุ่มทำงานอัตโนมัติ
""",
            parse_mode="HTML"
        )

    except:
        pass

# =============================
# SETTINGS
# =============================

SUSPICIOUS_WORDS = [

# 🔞 18+
"เย็ด","ควย","หี","แตกใน","น้ำแตก","เงี่ยน","เสียว","เอากัน",
"porn","xxx","nsfw","18plus","หนังโป๊",

# 📸 leak
"หลุด","คลิปหลุด","onlyfans","fansly","leak","leaked",

# 👙 ล่อแหลม
"นม","หุ่นดี","สาวเด็ด","เซ็กซี่","ยั่ว",

# 🔗 CTA / หลอกคลิก
"ดูฟรี","ดูเลย","กดดู","คลิกที่นี่","ลิงก์นี้",
"รับชม","เข้าดู","ดูต่อ",
"watch video","full clip","full video",

# ⚠️ เด็ก
"มัธยม","นักเรียน","เด็กนักเรียน","เด็กมัธยม","ใสๆ","เดก",

# 🎰 พนัน
"สล็อต","บาคาร่า","แทงบอล","ไม่ต้องฝาก",
"ลงทุน","กำไร","ได้เงินจริง","ทำเงิน","ถอนเงิน",
"โบนัส","โปรแรง","โปรแจก",

# 💬 ขาย / ปิดการขาย
"แอดไลน์","line","dm","inbox","สนใจ",
"ขายจริง","รับงาน","รับทำ","ติดต่อ","หาร",

# 🎭 escort
"ไซด์ไลน์","งานเอ็น","escort","เด็กเอ็น","งานนอก","private",

# 🔗 link / domain
"http","https","www",".com",".xyz",".vip",".top",".site",
"t.me","telegram","bit.ly","tinyurl","shorturl",

# 🤖 spam trick
"คลังเก็บ","auto","gift"
]

LINK_PATTERNS = [
    r"http[s]?://",
    r"www\.",
    r"t\.me/",
    r"@\w+",
    r"\b[a-zA-Z0-9-]+\.(com|net|org|xyz|top|club|site|vip|online)\b"
]

# =============================
# ADD SMART NORMALIZE
# =============================
def smart_normalize(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r'[\u200b-\u200f\uFEFF]', '', text)
    text = re.sub(r'[^\w\s]', '', text)
    return text.lower()

# =============================
# FIX contains_bad_word
# =============================
def contains_bad_word(text):

    text = smart_normalize(text)

    patterns = [
        r"ค[\W_]*ว[\W_]*ย",
        r"เ[\W_]*ย[\W_]*็[\W_]*ด",
        r"p[\W_]*o[\W_]*r[\W_]*n",
    ]

    for p in patterns:
        if re.search(p, text):
            return True

    for word in SUSPICIOUS_WORDS:
        if word in text:
            return True

    return False

SUSPICIOUS_EMOJIS = [
"🔥","💦","😍","🥵","💋","👉","👌","🍑","🍆",
"🔞","📌","🎯","💯","⭐","❤️","💖","💥",
"👅","😈","🤤","🆓","🎁","📲","📥","📍",
"❗","‼️","🔗"
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
# UTIL
# =============================

def normalize_text(text):
    text = unicodedata.normalize("NFKC", text)
    return text.lower()

def extract_urls(text):
    return re.findall(r"(https?://[^\s]+|www\.[^\s]+)", text)

# =============================
# FIX DOMAIN CHECK
# =============================
def is_allowed(url):

    if not url.startswith("http"):
        url = "http://" + url

    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    for d in ALLOWED_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return True

    return False

def contains_link(text):

    text = normalize_text(text)

    for pattern in LINK_PATTERNS:
        if re.search(pattern, text):
            return True

    return False

def is_spam(user_id):

    now = time.time()

    user_messages[user_id] = [
        t for t in user_messages[user_id]
        if now - t < 10
    ]

    user_messages[user_id].append(now)

    return len(user_messages[user_id]) >= 3


# =============================
# JOIN TRACK
# =============================

async def track_join(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.chat_member.new_chat_member.status == "member":

        user_id = update.chat_member.new_chat_member.user.id
        join_times[user_id] = time.time()


# =============================
# MAIN FILTER (ปิดระบบเก่า)
# =============================

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return


# =============================
# GOD MODE ADD
# =============================

user_verified = {}
pending_verify = set()
user_score = defaultdict(int)

async def enhanced_join(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.chat_member.new_chat_member.status == "member":

        user = update.chat_member.new_chat_member.user
        uid = user.id
        chat_id = update.chat_member.chat.id

        pending_verify.add(uid)

        await context.bot.restrict_chat_member(
            chat_id,
            uid,
            ChatPermissions(can_send_messages=False)
        )

        await context.bot.send_message(chat_id,
            "👋 พิมพ์ 'ยืนยัน' และรอ 5 นาที ก่อนพิมพ์")

async def verify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message
    if not msg:
        return

    user = msg.from_user

    if user.id in pending_verify and msg.text and "ยืนยัน" in msg.text:

        pending_verify.remove(user.id)
        user_verified[user.id] = time.time()

        await msg.reply_text("✅ ยืนยันแล้ว")

def is_locked(uid):
    return uid not in user_verified or time.time() - user_verified[uid] < 300

async def god_mode_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message
    if not msg:
        return

    user = msg.from_user
    if not user:
        return

    chat_id = update.effective_chat.id

    # ADMIN BYPASS
    try:
        member = await context.bot.get_chat_member(chat_id, user.id)
        if member.status in ["administrator", "creator"]:
            return
    except:
        pass

    uid = user.id
    text = msg.text or msg.caption or ""

    if uid in pending_verify or is_locked(uid):
        await msg.delete()
        return

    if msg.forward_from or msg.forward_from_chat or msg.forward_sender_name:
        await msg.delete()
        await context.bot.restrict_chat_member(chat_id, uid, ChatPermissions(can_send_messages=False))
        return

    score = 0

    if contains_bad_word(text):
        score += 3

    urls = extract_urls(text)
    for url in urls:
        if not is_allowed(url):
            score += 5

    user_score[uid] += score

    if user_score[uid] >= 5:
        await msg.delete()

    if user_score[uid] >= 8:
        await context.bot.restrict_chat_member(chat_id, uid, ChatPermissions(can_send_messages=False))

    if user_score[uid] >= 12:
        await context.bot.ban_chat_member(chat_id, uid)


# =============================
# MAIN
# =============================

if __name__ == "__main__":

    web_thread = threading.Thread(target=run_web)
    web_thread.start()

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.ALL, check_message))

    application.add_handler(ChatMemberHandler(enhanced_join, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.TEXT, verify_user))
    application.add_handler(MessageHandler(filters.ALL, god_mode_filter))

    print("BOT RUNNING (FINAL FIXED)")

    application.run_polling()
    
