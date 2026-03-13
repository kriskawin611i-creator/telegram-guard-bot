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

# =====================================
# TOKEN
# =====================================

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

# =====================================
# WEB SERVER (KEEP BOT ALIVE)
# =====================================

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Guard Bot Running"

def run_web():
    app_web.run(host="0.0.0.0", port=PORT)

# =====================================
# STORAGE
# =====================================

join_times = {}
user_messages = defaultdict(list)
last_messages = {}

# =====================================
# SETTINGS
# =====================================

SUSPICIOUS_WORDS = [

    "เด็กน้อย",
    "เดกน้อย",
    "เปิดคลิป",
    "เปิดคลิปเลย",
    "คลิกดู",
    "ดูคลิป",
    "คลิปหลุด",
    "ดูฟรี",
    "ฟรี",
    "18+",
    "xxx",
    "porn",
    "sex",
    "watch",
    "watch video",
    "free video",
]

LINK_PATTERNS = [

    r"http[s]?://",
    r"www\\.",
    r"t\\.me/",
    r"@\\w+",
    r"bit\\.ly",
    r"tinyurl",
    r"t\\.co",
    r"goo\\.gl",
    r"shorturl",
    r"\\b[a-zA-Z0-9-]+\\.(com|net|org|xyz|top|club|site|vip|online)\\b",
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
]

# =====================================
# UTIL FUNCTIONS
# =====================================

def normalize_text(text):

    text = unicodedata.normalize("NFKC", text)
    return text.lower()


def extract_urls(text):

    return re.findall(r"(https?://[^\\s]+|www\\.[^\\s]+)", text)


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

# =====================================
# ALERT
# =====================================

async def alert_action(context, chat_id, user, reason, action):

    try:

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"""
🚫 GUARD BOT

ตรวจพบการกระทำต้องสงสัย

ผู้ใช้: {user.mention_html()}
เหตุผล: {reason}

การดำเนินการ: {action}

ระบบป้องกันกลุ่มทำงานอัตโนมัติ
""",
            parse_mode="HTML"
        )

    except:
        pass

# =====================================
# JOIN TRACK
# =====================================

async def track_join(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.chat_member.new_chat_member.status == "member":

        user_id = update.chat_member.new_chat_member.user.id

        join_times[user_id] = time.time()

# =====================================
# MAIN FILTER
# =====================================

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message

    if not message:
        return

    user = message.from_user
    chat_id = update.effective_chat.id

    text = message.text or ""

    # =====================================
    # ADMIN BYPASS (ADMIN + OWNER POST ANYTHING)
    # =====================================

    try:

        member = await context.bot.get_chat_member(chat_id, user.id)

        if member.status in ["administrator", "creator"]:
            return

    except:
        return

    # =====================================
    # MUTE FUNCTION
    # =====================================

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

    # =====================================
    # BLOCK FORWARD
    # =====================================

    if message.forward_from or message.forward_from_chat:

        await message.delete()

        await alert_action(context, chat_id, user, "Forward Spam", "DELETE + MUTE")

        await mute_user()

        return

    # =====================================
    # INLINE BUTTON SPAM
    # =====================================

    if message.reply_markup:

        try:

            buttons = message.reply_markup.inline_keyboard

            for row in buttons:
                for button in row:

                    if button.url:

                        await message.delete()

                        await alert_action(context, chat_id, user, "Inline Button Spam", "DELETE + MUTE")

                        await mute_user()

                        return

        except:
            pass

    # =====================================
    # USERNAME SPAM
    # =====================================

    if re.search(r"@\\w+", text):

        await message.delete()

        await alert_action(context, chat_id, user, "Username Spam", "DELETE + MUTE")

        await mute_user()

        return

    # =====================================
    # NEW MEMBER LINK
    # =====================================

    if user.id in join_times:

        if time.time() - join_times[user.id] < 60:

            if contains_link(text):

                await message.delete()

                await alert_action(context, chat_id, user, "New Member Link", "DELETE + MUTE")

                await mute_user()

                return

    # =====================================
    # LINK FILTER
    # =====================================

    urls = extract_urls(text)

    for url in urls:

        if not is_allowed(url):

            await message.delete()

            await alert_action(context, chat_id, user, "Unauthorized Link", "DELETE + MUTE")

            await mute_user()

            return

    # =====================================
    # BAD WORD FILTER
    # =====================================

    if contains_bad_word(text):

        await message.delete()

        await alert_action(context, chat_id, user, "Suspicious Word", "DELETE + MUTE")

        await mute_user()

        return

    # =====================================
    # EMOJI PORN SPAM
    # =====================================

    emoji_pattern = r"[👅💦🔞🍑🍒🔥]"

    if len(re.findall(emoji_pattern, text)) >= 3:

        await message.delete()

        await alert_action(context, chat_id, user, "Emoji Spam", "DELETE + MUTE")

        await mute_user()

        return

    # =====================================
    # DUPLICATE MESSAGE
    # =====================================

    key = (user.id, text)

    if key in last_messages:

        await message.delete()

        await alert_action(context, chat_id, user, "Duplicate Spam", "DELETE + MUTE")

        await mute_user()

        return

    last_messages[key] = time.time()

    # =====================================
    # FLOOD SPAM
    # =====================================

    if is_spam(user.id):

        await message.delete()

        await alert_action(context, chat_id, user, "Message Flood", "DELETE + MUTE")

        await mute_user()

        return

# =====================================
# MAIN
# =====================================

if __name__ == "__main__":

    web_thread = threading.Thread(target=run_web)

    web_thread.start()

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER))

    application.add_handler(
        MessageHandler(filters.ALL & (~filters.COMMAND), check_message)
    )

    print("Guard Bot Started")

    application.run_polling()
