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
    r"\b[a-zA-Z0-9-]+\.(com|net|org|xyz|top|club|site|vip|online)\b"
]

ALLOWED_DOMAINS = [
    "gxvdo.com",
    "t-hoy.com",
    "mangath.live",
    "nangbab.live",
]

# =============================
# UTIL
# =============================

def normalize_text(text):
    text = unicodedata.normalize("NFKC", text)
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
        if re.search(pattern, text):
            return True

    return False


def contains_bad_word(text):

    text = normalize_text(text)

    for word in SUSPICIOUS_WORDS:
        if word in text:
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
# MAIN FILTER
# =============================

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message

    if not message or not message.text:
        return

    user = message.from_user
    chat_id = update.effective_chat.id
    text = message.text

    # =============================
    # ADMIN BYPASS
    # =============================

    member = await context.bot.get_chat_member(chat_id, user.id)

    if member.status in ["administrator", "creator"]:
        return

    # =============================
    # MUTE FUNCTION
    # =============================

    async def mute_user():

        await context.bot.restrict_chat_member(

            chat_id=chat_id,
            user_id=user.id,

            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
            ),
        )

    # =============================
    # BLOCK FORWARD
    # =============================

    if message.forward_from or message.forward_from_chat:

        await message.delete()
        await mute_user()
        return

    # =============================
    # BLOCK @
    # =============================

    if re.search(r"@\w+", text):

        await message.delete()
        await mute_user()
        return

    # =============================
    # NEW MEMBER LINK
    # =============================

    if user.id in join_times:

        if time.time() - join_times[user.id] < 60:

            if contains_link(text):

                await message.delete()
                await mute_user()
                return

    # =============================
    # LINK FILTER
    # =============================

    urls = extract_urls(text)

    for url in urls:

        if not is_allowed(url):

            await message.delete()
            await mute_user()
            return

    # =============================
    # BAD WORD
    # =============================

    if contains_bad_word(text):

        await message.delete()
        await mute_user()
        return

    # =============================
    # SPAM FLOOD
    # =============================

    if is_spam(user.id):

        await message.delete()
        await mute_user()
        return


# =============================
# MAIN
# =============================

if __name__ == "__main__":

    web_thread = threading.Thread(target=run_web)
    web_thread.start()

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER))

    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), check_message)
    )

    print("Bot started...")

    application.run_polling()
