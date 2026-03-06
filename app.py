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
    return "Bot running"

def run_web():
    app_web.run(host="0.0.0.0", port=PORT)

# =============================
# STORAGE
# =============================

join_times = {}
spam_tracker = defaultdict(list)

# =============================
# SETTINGS
# =============================

BAD_WORDS = [
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

def normalize(text):
    return unicodedata.normalize("NFKC", text).lower()

def extract_urls(text):
    return re.findall(r"(https?://[^\s]+|www\.[^\s]+)", text)

def allowed(url):

    if not url.startswith("http"):
        url = "http://" + url

    domain = urlparse(url).netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    return domain in ALLOWED_DOMAINS

def bad_word(text):

    text = normalize(text)

    for word in BAD_WORDS:
        if word in text:
            return True

    return False

def spam(user_id):

    now = time.time()

    spam_tracker[user_id] = [
        t for t in spam_tracker[user_id]
        if now - t < 10
    ]

    spam_tracker[user_id].append(now)

    return len(spam_tracker[user_id]) >= 3

# =============================
# TRACK JOIN
# =============================

async def track_join(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.chat_member.new_chat_member.status == "member":
        join_times[update.chat_member.new_chat_member.user.id] = time.time()

# =============================
# MAIN FILTER
# =============================

async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message
    if not msg:
        return

    user = msg.from_user
    chat_id = update.effective_chat.id
    text = msg.text or ""

    # ===== ADMIN BYPASS =====
    member = await context.bot.get_chat_member(chat_id, user.id)

    if member.status in ("administrator", "creator"):
        return

    # ===== MUTE FUNCTION =====
    async def mute():

        await context.bot.restrict_chat_member(
            chat_id,
            user.id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
            ),
        )

    # ===== BLOCK FORWARD =====
    if msg.forward_from or msg.forward_from_chat:
        await msg.delete()
        await mute()
        return

    # ===== BLOCK MENTION =====
    if re.search(r"@\w+", text):
        await msg.delete()
        await mute()
        return

    # ===== NEW MEMBER LINK =====
    if user.id in join_times:
        if time.time() - join_times[user.id] < 60:
            if "http" in text:
                await msg.delete()
                await mute()
                return

    # ===== LINK FILTER =====
    urls = extract_urls(text)

    for u in urls:
        if not allowed(u):
            await msg.delete()
            await mute()
            return

    # ===== BAD WORD =====
    if bad_word(text):
        await msg.delete()
        await mute()
        return

    # ===== SPAM =====
    if spam(user.id):
        await msg.delete()
        await mute()
        return


# =============================
# MAIN
# =============================

if __name__ == "__main__":

    web_thread = threading.Thread(target=run_web)
    web_thread.start()

    bot = ApplicationBuilder().token(TOKEN).build()

    bot.add_handler(
        ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER)
    )

    bot.add_handler(
        MessageHandler(filters.ALL, guard)
    )

    print("Bot started")

    bot.run_polling()
