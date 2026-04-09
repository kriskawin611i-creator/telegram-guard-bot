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
# 🔞 โป๊ / ลามก
"เย็ด","ควย","หี","แตกใน","น้ำแตก","เงี่ยน","เสียว","เอากัน",
"porn","xxx","nsfw","18+","18plus","หนังโป๊",

# 📸 หลุด / onlyfans
"หลุด","คลิปหลุด","ของหลุด","onlyfans","fansly","leak","leaked",

# 👙 ล่อแหลม
"นม","หุ่นดี","สาวเด็ด","เซ็กซี่","ยั่ว",

# 🔗 หลอกคลิก / CTA
"ดูฟรี","ดูเลย","คลิก","กดดู","ลิงก์นี้","linkนี้","คลิกที่นี่",
"รับชม","เข้าดู","ดูต่อ","ฟรี","แจกฟรี","เครดิตฟรี",
"watch","watch video","ver video","free","full clip","full video",

# ⚠️ เด็ก (เสี่ยงสูง)
"มัธยม","มปลาย","มต้น","นักเรียน","เด็ก","เด็กนักเรียน",
"เด็กมัธยม","บริสุทธิ์","ใสๆ",

# 🎰 พนัน / เงิน
"สล็อต","บาคาร่า","แทงบอล","ไม่ต้องฝาก",
"ลงทุน","กำไร","ได้เงินจริง","รวย","ทำเงิน","ถอนเงิน",
"เครดิตฟรีไม่ต้องฝาก","โบนัส","โปรแรง","โปรแจก",

# 💬 ชวนคุย / ปิดการขาย
"แอดไลน์","แอดไลน์มา","line","dm","inbox","สนใจ",
"ขาย","ขายจริง","รับงาน","รับทำ","ติดต่อ",

# 🎭 แฝงขายบริการ
"ไซด์ไลน์","งานเอ็น","escort","เด็กเอ็น","งานนอก","private",

# 🔗 ลิงก์ / โดเมน
"http","https","www",".com",".xyz",".vip",".top",".site",
"t.me","telegram","bit.ly","tinyurl","shorturl",

# 🤖 อื่น ๆ / หลบฟิลเตอร์
"ดูด","คลังเก็บ","ai","bot","auto","gift"
]

LINK_PATTERNS = [
    r"http[s]?://",                  # http / https
    r"www\.",                        # www.
    r"t\.me/",                       # telegram link
    r"@\w+",                         # @username
    r"\b[a-zA-Z0-9-]+\.(com|net|org|xyz|top|club|site|vip|online|live|shop|io)\b",  # domain
    r"bit\.ly/",                     # short link
    r"tinyurl\.com/",
    r"shorturl\.",
]

SUSPICIOUS_EMOJIS = [
    "🔥","💦","😍","🥵","💋","👉","👌","🍑","🍆",
    "🔞","📌","🎯","💯","⭐","❤️","💖","💥",
    "👅","😈","🤤","🆓","🎁","📲","📥","📍",
    "❗","‼️","🔗","⚡","🚨","📢"
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

    if not message:
        return

    chat_id = update.effective_chat.id

    # ==================================================
    # BYPASS POST AS GROUP / CHANNEL / ANONYMOUS ADMIN
    # ==================================================

    if message.sender_chat:
        return

    user = message.from_user

    if not user:
        return

    # ==================================================
    # HARD ADMIN BYPASS
    # ==================================================

    try:

        member = await context.bot.get_chat_member(chat_id, user.id)

        if member.status in ["administrator", "creator"]:

            try:
                await context.bot.restrict_chat_member(
                    chat_id,
                    user.id,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_audios=True,
                        can_send_documents=True,
                        can_send_photos=True,
                        can_send_videos=True,
                        can_send_video_notes=True,
                        can_send_voice_notes=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                        can_change_info=True,
                        can_invite_users=True,
                        can_pin_messages=True,
                    ),
                )
            except:
                pass

            return

    except:
        pass

    text = message.text or message.caption or ""

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

        await alert_action(context, chat_id, user, "Forward ข้อความ", "DELETE + MUTE")

        await mute_user()
        return

    # =============================
    # BLOCK @
    # =============================

    if re.search(r"@\w+", text):

        await message.delete()

        await alert_action(context, chat_id, user, "ส่ง @username", "DELETE + MUTE")

        await mute_user()
        return

    # =============================
    # NEW MEMBER LINK
    # =============================

    if user.id in join_times:

        if time.time() - join_times[user.id] < 60:

            if contains_link(text):

                await message.delete()

                await alert_action(context, chat_id, user, "สมาชิกใหม่ส่งลิงก์", "DELETE + MUTE")

                await mute_user()
                return

    # =============================
    # LINK FILTER
    # =============================

    urls = extract_urls(text)

    for url in urls:

        if not is_allowed(url):

            await message.delete()

            await alert_action(context, chat_id, user, "ส่งลิงก์ที่ไม่อนุญาต", "DELETE + MUTE")

            await mute_user()
            return

    # =============================
    # BAD WORD
    # =============================

    if contains_bad_word(text):

        await message.delete()

        await alert_action(context, chat_id, user, "ใช้คำต้องห้าม", "DELETE + MUTE")

        await mute_user()
        return

    # =============================
    # SPAM FLOOD
    # =============================

    if is_spam(user.id):

        await message.delete()

        await alert_action(context, chat_id, user, "Spam ข้อความ", "DELETE + MUTE")

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
        MessageHandler(filters.ALL, check_message)
    )

    print("Bot started...")

    application.run_polling()

# =============================
# NEW
# =============================

async def advanced_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message
    if not message:
        return

    user = message.from_user
    chat_id = update.effective_chat.id
    text = message.text or message.caption or ""

    # 🔒 ยังไม่ verify
    if user.id in pending_verify:
        await message.delete()
        return

    # 🔒 ยังไม่ครบ 5 นาที
    if is_locked(user.id):
        await message.delete()
        return

    # 🔒 ไม่มีโปรไฟล์
    if no_profile(user):
        await message.delete()
        return

    # 🚫 Forward ALL TYPES
    if (
        message.forward_from
        or message.forward_from_chat
        or message.forward_sender_name
    ):
        await message.delete()
        user_muted_permanent.add(user.id)
        await alert_action(context, chat_id, user, "Forward ทุกประเภท", "MUTE PERMANENT")
        return

    # 🔁 duplicate
    if is_duplicate(user.id, text):
        await message.delete()
        return

    # 🧠 similarity
    if is_similar(user.id, text):
        await message.delete()
        return

    # 🧬 pattern
    if detect_pattern(text):
        await message.delete()
        return

    # ⚡ spam เร็ว
    if is_fast_spam(user.id):
        await message.delete()
        user_muted_permanent.add(user.id)
        await alert_action(context, chat_id, user, "Spam รัว", "MUTE PERMANENT")
        return

    # 🔗 link ขั้นสูง
    urls = extract_urls(text)
    for url in urls:
        if not is_allowed_strict(url):
            await message.delete()
            user_muted_permanent.add(user.id)
            return
