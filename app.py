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
    ContextTypes,
    MessageHandler,
    ChatMemberHandler,
    filters
)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

# --------------------------------
# WEB SERVER
# --------------------------------

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Guard Bot Running"

def run_web():
    app_web.run(host="0.0.0.0", port=PORT)

# --------------------------------
# STORAGE
# --------------------------------

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

# --------------------------------
# TEXT CLEAN
# --------------------------------

def normalize_text(text):

    text = unicodedata.normalize("NFKC", text)

    text = text.replace("\u200b", "")
    text = text.replace("\u200c", "")
    text = text.replace("\u200d", "")

    return text.lower()

# --------------------------------
# LINK DETECT
# --------------------------------

def contains_link(text):

    text = normalize_text(text)

    for pattern in LINK_PATTERNS:
        if re.search(pattern, text):
            return True

    return False


def extract_urls(text):

    return re.findall(r"(https?://[^\s]+|www\.[^\s]+)", text)


def is_allowed(url):

    if not url.startswith("http"):
        url = "http://" + url

    domain = urlparse(url).netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    return domain in ALLOWED_DOMAINS

# --------------------------------
# SPAM DETECT
# --------------------------------

def is_spam(user_id, text):

    now = time.time()

    user_messages[user_id] = [
        t for t in user_messages[user_id]
        if now - t[0] < 10
    ]

    user_messages[user_id].append((now, text))

    return len(user_messages[user_id]) > 6


def emoji_count(text):
    return len(re.findall(r"[^\w\s]", text))


def mention_count(text):
    return len(re.findall(r"@\w+", text))

# --------------------------------
# ADMIN CHECK
# --------------------------------

async def is_admin(update, context):

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    member = await context.bot.get_chat_member(chat_id, user_id)

    return member.status in ["administrator", "creator"]

# --------------------------------
# MUTE USER
# --------------------------------

async def mute_user(chat_id, user_id, context):

    permissions = ChatPermissions(
        can_send_messages=False,
        can_send_other_messages=False,
        can_send_media_messages=False,
        can_add_web_page_previews=False
    )

    await context.bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=permissions
    )

# --------------------------------
# PUBLIC LOG
# --------------------------------

async def public_log(context, chat_id, text):

    msg = await context.bot.send_message(chat_id, text)

    await threading.Timer(
        20,
        lambda: context.bot.delete_message(chat_id, msg.message_id)
    ).start()

# --------------------------------
# JOIN TRACK
# --------------------------------

async def track_join(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.chat_member.new_chat_member.status == "member":

        user_id = update.chat_member.new_chat_member.user.id

        join_times[user_id] = time.time()

# --------------------------------
# MAIN FILTER
# --------------------------------

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message

    if not message:
        return

    user = message.from_user
    chat_id = update.effective_chat.id

    # ------------------
    # ADMIN BYPASS
    # ------------------

    if await is_admin(update, context):
        return

    text = message.text or message.caption or ""

    # ------------------
    # LINK CHECK
    # ------------------

    if contains_link(text):

        urls = extract_urls(text)

        for url in urls:

            if not is_allowed(url):

                await message.delete()

                await mute_user(chat_id, user.id, context)

                await context.bot.send_message(
                    chat_id,
                    f"🚫 ระบบป้องกันสแปม\n\n"
                    f"ผู้ใช้: @{user.username or user.id}\n"
                    f"เหตุผล: ลิงก์ต้องห้าม\n\n"
                    f"การดำเนินการ:\n"
                    f"ลบข้อความ + MUTE ถาวร"
                )

                return

    # ------------------
    # EMOJI FLOOD
    # ------------------

    if emoji_count(text) > 20:

        await message.delete()

        await mute_user(chat_id, user.id, context)

        await context.bot.send_message(
            chat_id,
            f"⚠️ Emoji Spam\n"
            f"ผู้ใช้ @{user.username or user.id} ถูก MUTE"
        )

        return

    # ------------------
    # MENTION SPAM
    # ------------------

    if mention_count(text) > 6:

        await message.delete()

        await mute_user(chat_id, user.id, context)

        await context.bot.send_message(
            chat_id,
            f"⚠️ Mention Spam\n"
            f"ผู้ใช้ @{user.username or user.id} ถูก MUTE"
        )

        return

    # ------------------
    # MESSAGE FLOOD
    # ------------------

    if is_spam(user.id, text):

        await message.delete()

        await mute_user(chat_id, user.id, context)

        await context.bot.send_message(
            chat_id,
            f"⚠️ Spam Flood\n"
            f"ผู้ใช้ @{user.username or user.id} ถูก MUTE"
        )

# --------------------------------
# MAIN
# --------------------------------

if __name__ == "__main__":

    web_thread = threading.Thread(target=run_web)
    web_thread.start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER))

    app.add_handler(
        MessageHandler(
            filters.ALL & (~filters.COMMAND),
            check_message
        )
    )

    print("Guard Bot Started")

    app.run_polling()
