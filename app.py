import os
import re
import json
import time
import asyncio
import logging
import threading
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse

from flask import Flask
from telegram import Update, ChatPermissions
from telegram.error import RetryAfter, TimedOut, NetworkError, Forbidden
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ChatMemberHandler,
)

# =============================
# LOGGING
# =============================
# บันทึก error ลง console แทนที่จะ crash เงียบๆ

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
PORT  = int(os.environ.get("PORT", 10000))

# =============================
# CONCURRENCY CONTROL
# =============================
# [STABILITY #1] จำกัด coroutine พร้อมกันสูงสุด 30 ตัว
# กันไม่ให้ spam 1000 ข้อความ/วินาที สร้าง coroutine ไม่จำกัด → OOM crash

MAX_CONCURRENT = 30
_semaphore: asyncio.Semaphore | None = None  # สร้างใน event loop จริง (ดูด้านล่าง)

# [STABILITY #2] Per-user cooldown — ถ้า user ส่งถี่เกิน 0.3 วินาที → ข้ามทันที
# ลด API call ก่อนถึง semaphore เลย
USER_COOLDOWN       = 0.3   # วินาที
user_last_processed: dict[int, float] = {}

# ถ้าบอทเพิ่งตื่นหลังล่ม Render จะส่ง pending updates เข้ามารัวมาก
# ข้อความที่เก่ากว่านี้จะถือเป็น backlog และไม่โดน per-user cooldown ข้าม
BACKLOG_MESSAGE_AGE = 30    # วินาที

# [STABILITY #3] Alert dedup — ถ้า user ถูก alert ไปแล้วใน 60 วินาที → ไม่ alert ซ้ำ
# กัน bot ส่ง alert ท่วม group เวลาโดน spam หนัก
ALERT_COOLDOWN      = 60    # วินาที
user_last_alert: dict[int, float] = {}

# เมื่อตรวจเจอสแปมเมอร์ ให้ ban พร้อมสั่ง Telegram ลบประวัติข้อความของ user คนนั้น
# ถ้า Telegram ไม่อนุญาตหรือ bot สิทธิ์ไม่พอ จะ fallback เป็น mute ถาวรแบบเดิม
PURGE_SPAMMER_HISTORY_ON_DETECT = True
AUTO_ACTION_TEXT = (
    "🚫 DELETE + BAN + ลบประวัติ"
    if PURGE_SPAMMER_HISTORY_ON_DETECT
    else "🔇 DELETE + MUTE ถาวร"
)

# =============================
# WEB SERVER  (สำหรับ UptimeRobot)
# =============================

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Bot is running!", 200

@app_web.route("/health")
def health():
    return {"status": "ok", "muted_count": len(user_muted_permanent)}, 200

def run_web():
    app_web.run(host="0.0.0.0", port=PORT)

# =============================
# STORAGE
# =============================

join_times    : dict[int, float]       = {}
user_messages : dict[int, list[float]] = defaultdict(list)

# =============================
# ADMIN CACHE
# =============================

admin_cache     : dict[tuple, tuple] = {}
ADMIN_CACHE_TTL = 300  # 5 นาที

async def is_admin_cached(context, chat_id: int, user_id: int) -> bool:
    now = time.time()
    key = (chat_id, user_id)
    if key in admin_cache:
        result, expire = admin_cache[key]
        if now < expire:
            return result
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        result = member.status in ("administrator", "creator")
    except Exception:
        result = False
    admin_cache[key] = (result, now + ADMIN_CACHE_TTL)
    return result

# =============================
# PERSISTENT MUTE
# =============================

MUTED_FILE = "muted_users.json"

def load_muted() -> set:
    try:
        with open(MUTED_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_muted() -> None:
    try:
        with open(MUTED_FILE, "w") as f:
            json.dump(list(user_muted_permanent), f)
    except Exception:
        pass

user_muted_permanent: set = load_muted()

# =============================
# ALERT SYSTEM
# =============================

async def alert_action(context, chat_id: int, user, reason: str, action: str):
    """
    ส่ง alert แต่ข้าม user ที่เพิ่งถูก alert ไปใน 60 วินาที
    กันบอทส่ง alert ท่วม group เวลาโดน spam หนัก
    """
    if user:
        now  = time.time()
        last = user_last_alert.get(user.id, 0)
        if now - last < ALERT_COOLDOWN:
            return  # ไม่ alert ซ้ำ
        user_last_alert[user.id] = now

    try:
        name = user.mention_html() if user else "Unknown"
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🚫 GUARD BOT\n\n"
                f"ตรวจพบการกระทำต้องสงสัย\n\n"
                f"ผู้ใช้: {name}\n"
                f"เหตุผล: {reason}\n\n"
                f"การดำเนินการ: {action}\n\n"
                f"ระบบป้องกันกลุ่มทำงานอัตโนมัติ"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

# =============================
# SETTINGS
# =============================

SUSPICIOUS_WORDS_TH = [
    "ดูฟรี", "กดดู", "ลิงก์นี้", "คลิกที่นี่", "เข้าดู", "ดูต่อ",
    "แจกฟรี", "เครดิตฟรี", "full clip", "full video", "ver video",
    "มปลาย", "มต้น", "เด็กนักเรียน", "เด็กมัธยม", "บริสุทธิ์", "ใสๆ",
    "สล็อต", "บาคาร่า", "แทงบอล", "ไม่ต้องฝาก",
    "ได้เงินจริง", "ถอนเงิน", "เครดิตฟรีไม่ต้องฝาก", "โปรแรง", "โปรแจก",
    "แอดไลน์", "แอดไลน์มา", "ขาย", "รับงาน", "รับทำ",
    "ไซด์ไลน์", "งานเอ็น", "เด็กเอ็น", "งานนอก",
    "t.me", "bit.ly", "tinyurl", "shorturl",
]

SUSPICIOUS_WORDS_EN = [
    "porn", "xxx", "nsfw", "18plus",
    "onlyfans", "fansly", "leak", "leaked",
    "watch", "free", "escort", "private",
    "bot", "auto", "gift",
    "line", "dm", "inbox",
    "http", "https", "www", "telegram",
]

LINK_PATTERNS = [
    r"http[s]?://",
    r"www\.",
    r"t\.me/",
    r"@\w+",
    r"\b[a-zA-Z0-9-]+\.(com|net|org|xyz|top|club|site|vip|online|live|shop|io)\b",
    r"bit\.ly/",
    r"tinyurl\.com/",
    r"shorturl\.",
]

SUSPICIOUS_EMOJIS = {
    "🔥", "💦", "😍", "🥵", "💋", "👉", "👌", "🍑", "🍆",
    "🔞", "📌", "🎯", "💯", "⭐", "❤️", "💖", "💥",
    "👅", "😈", "🤤", "🆓", "🎁", "📲", "📥", "📍",
    "❗", "‼️", "🔗", "⚡", "🚨", "📢",
}
EMOJI_THRESHOLD = 4

ALLOWED_DOMAINS = {
    "t-hoy.com", "mangath.live", "นางแบบ.live", "taluijapan.com",
    "youfilx.com", "cc-cos.com", "kamouth.com", "gamemonday.live",
    "catdumb.live", "gaythai.live", "figmodel.com", "hooligril.com",
    "tidroam.com", "zaranua.live", "kinnaii.com", "mmmoy.com",
    "ฟิวแฟน.live", "1000drink.com", "ppnewsth.com", "แจกวาร์ป.live",
    "longsanam.com", "toodtidgameth.com", "ttphoo.com", "larnom.com",
    "ockock.com", "kongcheer.com", "madamporns.com", "โอลี่แฟน.live",
    "โกดังญี่ปุ่น.com", "stmgamer.com", "doofarang.com", "fansav.com",
    "doophuchais.com", "tingkorea.com", "avidol.live", "onlyfanxxx.com",
    "zapgern.com", "gumpun.com", "madamboys.com", "peekjkt.com",
    "sudpung.com", "gxvdo.com", "24-jav.ch",
    "xn--72c9aea1jwd.live", "xn--q3cla5a5dzd.live",
    "xn--12cn2d5at0e3e4d.live", "xn--q3clr5a4b7dd5c.live",
    "xn--12cms0a1al5m8a2a6g6cc.com",
}

# =============================
# UTIL
# =============================

def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()

def extract_urls(text: str) -> list:
    text = normalize_text(text)
    return re.findall(
        r"(https?://[^\s]+|www\.[^\s]+|t\.me/[^\s]+|telegram\.me/[^\s]+)",
        text,
    )

def is_allowed(url: str) -> bool:
    if not url.startswith("http"):
        url = "http://" + url
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    return domain in ALLOWED_DOMAINS

def contains_link(text: str) -> bool:
    text = normalize_text(text)
    return any(re.search(p, text) for p in LINK_PATTERNS)

def contains_bad_word(text: str) -> bool:
    text = normalize_text(text)
    for word in SUSPICIOUS_WORDS_TH:
        if word in text:
            return True
    for word in SUSPICIOUS_WORDS_EN:
        if re.search(r"\b" + re.escape(word) + r"\b", text):
            return True
    return False

def contains_suspicious_emoji(text: str) -> bool:
    return sum(1 for ch in text if ch in SUSPICIOUS_EMOJIS) >= EMOJI_THRESHOLD

def is_spam(user_id: int) -> bool:
    now = time.time()
    user_messages[user_id] = [t for t in user_messages[user_id] if now - t < 10]
    user_messages[user_id].append(now)
    return len(user_messages[user_id]) >= 5

def is_backlog_message(message) -> bool:
    msg_date = getattr(message, "date", None)
    if not msg_date:
        return False
    return time.time() - msg_date.timestamp() > BACKLOG_MESSAGE_AGE

# =============================
# DETECT FORWARD / STORY / GIFT
# =============================

def is_any_forward(message) -> bool:
    if message.forward_from:                          return True
    if message.forward_from_chat:                     return True
    if message.forward_sender_name:                   return True
    if message.forward_date:                          return True
    if getattr(message, "forward_origin", None):      return True
    if getattr(message, "via_bot", None) and message.forward_date: return True
    return False

def is_story_share(message) -> bool:
    return bool(getattr(message, "story", None))

def is_gift_message(message) -> bool:
    if getattr(message, "gift", None):        return True
    if getattr(message, "unique_gift", None): return True
    text = message.text or message.caption or ""
    return bool(re.search(r"(unique collectible|gift from|pepe nft|sending as a gift)", text.lower()))

# =============================
# JOIN TRACK + CLEANUP
# =============================

JOIN_EXPIRE = 7200

def cleanup_join_times() -> None:
    now     = time.time()
    expired = [uid for uid, t in list(join_times.items()) if now - t > JOIN_EXPIRE]
    for uid in expired:
        join_times.pop(uid, None)

async def track_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.chat_member.new_chat_member.status == "member":
        user_id = update.chat_member.new_chat_member.user.id
        join_times[user_id] = time.time()
        cleanup_join_times()

# =============================
# MAIN FILTER
# =============================

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message
    if not message:
        return

    chat_id = update.effective_chat.id

    if message.sender_chat:
        return

    user = message.from_user
    if not user:
        return

    backlog = is_backlog_message(message)

    # [STABILITY #2] Per-user cooldown — fast path ก่อน semaphore
    # ถ้า user ส่งถี่เกิน 0.3 วินาที ตรวจสอบว่าอยู่ใน muted หรือเปล่า
    now  = time.time()
    last = user_last_processed.get(user.id, 0)
    if not backlog and now - last < USER_COOLDOWN:
        # ถ้าถูก mute ถาวรแล้ว → ลบทันทีโดยไม่รอ semaphore
        if user.id in user_muted_permanent:
            try:
                await message.delete()
            except Exception:
                pass
        return
    user_last_processed[user.id] = now

    # [STABILITY #1] Global semaphore — process ได้สูงสุด MAX_CONCURRENT พร้อมกัน
    async with _semaphore:
        await _process_message(message, user, chat_id, context)


async def _process_message(message, user, chat_id: int, context):
    """
    Logic จริงทั้งหมดอยู่ในฟังก์ชันนี้
    ห่อด้วย try/except เพื่อกันไม่ให้ exception ตัวเดียวล่มทั้ง bot
    """
    try:
        text = message.text or message.caption or ""

        # ——— Admin bypass ———
        if await is_admin_cached(context, chat_id, user.id):
            return

        # ————————————————————————
        # PERMANENT MUTE HELPER
        # ————————————————————————

        async def mute_permanent():
            user_muted_permanent.add(user.id)
            save_muted()
            if PURGE_SPAMMER_HISTORY_ON_DETECT:
                try:
                    await context.bot.ban_chat_member(
                        chat_id=chat_id,
                        user_id=user.id,
                        revoke_messages=True,
                    )
                    return
                except Exception as e:
                    logger.warning(f"ban/revoke failed for user {user.id} in chat {chat_id}: {e}")

            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user.id,
                    permissions=ChatPermissions(
                        can_send_messages         = False,
                        can_send_audios           = False,
                        can_send_documents        = False,
                        can_send_photos           = False,
                        can_send_videos           = False,
                        can_send_video_notes      = False,
                        can_send_voice_notes      = False,
                        can_send_polls            = False,
                        can_send_other_messages   = False,
                        can_add_web_page_previews = False,
                        can_change_info           = False,
                        can_invite_users          = False,
                        can_pin_messages          = False,
                    ),
                )
            except Exception:
                pass

        # ——— Already permanently muted ———
        if user.id in user_muted_permanent:
            try:
                await message.delete()
            except Exception:
                pass
            return

        # ——— Forward ———
        if is_any_forward(message):
            try: await message.delete()
            except Exception: pass
            await alert_action(context, chat_id, user, "Forward ข้อความ (ทุกรูปแบบ)", AUTO_ACTION_TEXT)
            await mute_permanent()
            return

        # ——— Story ———
        if is_story_share(message):
            try: await message.delete()
            except Exception: pass
            await alert_action(context, chat_id, user, "แชร์ Story", AUTO_ACTION_TEXT)
            await mute_permanent()
            return

        # ——— Gift / NFT ———
        if is_gift_message(message):
            try: await message.delete()
            except Exception: pass
            await alert_action(context, chat_id, user, "ส่ง Gift / NFT / Collectible", AUTO_ACTION_TEXT)
            await mute_permanent()
            return

        # ——— @username ———
        if re.search(r"@\w+", text):
            try: await message.delete()
            except Exception: pass
            await alert_action(context, chat_id, user, "ส่ง @username", AUTO_ACTION_TEXT)
            await mute_permanent()
            return

        # ——— New member link ———
        if user.id in join_times and time.time() - join_times[user.id] < 60:
            if contains_link(text):
                try: await message.delete()
                except Exception: pass
                await alert_action(context, chat_id, user, "สมาชิกใหม่ส่งลิงก์", AUTO_ACTION_TEXT)
                await mute_permanent()
                return

        # ——— Link filter ———
        for url in extract_urls(text):
            if not is_allowed(url):
                try: await message.delete()
                except Exception: pass
                await alert_action(context, chat_id, user, "ส่งลิงก์ที่ไม่อนุญาต", AUTO_ACTION_TEXT)
                await mute_permanent()
                return

        # ——— Bad word ———
        if contains_bad_word(text):
            try: await message.delete()
            except Exception: pass
            await alert_action(context, chat_id, user, "ใช้คำต้องห้าม", AUTO_ACTION_TEXT)
            await mute_permanent()
            return

        # ——— Suspicious emoji ———
        if contains_suspicious_emoji(text):
            try: await message.delete()
            except Exception: pass
            await alert_action(context, chat_id, user, f"ส่ง Emoji ต้องสงสัย ≥ {EMOJI_THRESHOLD} ตัว", AUTO_ACTION_TEXT)
            await mute_permanent()
            return

        # ——— Spam flood ———
        if is_spam(user.id):
            try: await message.delete()
            except Exception: pass
            await alert_action(context, chat_id, user, "Spam ข้อความ", AUTO_ACTION_TEXT)
            await mute_permanent()
            return

    except Exception as e:
        # [STABILITY] จับทุก exception ไม่ให้ตายเงียบๆ
        logger.error(f"check_message error for user {user.id} in chat {chat_id}: {e}")


# =============================
# ADMIN COMMANDS
# =============================

async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    admin = update.effective_user
    chat = update.effective_chat

    if not message or not admin or not chat:
        return

    if not await is_admin_cached(context, chat.id, admin.id):
        return

    target_message = message.reply_to_message
    if not target_message or not target_message.from_user:
        await message.reply_text("ใช้ /purge โดย reply ข้อความสแปมที่ต้องการล้าง")
        return

    target = target_message.from_user
    if await is_admin_cached(context, chat.id, target.id):
        await message.reply_text("ไม่ล้างข้อความของ admin")
        return

    user_muted_permanent.add(target.id)
    save_muted()

    try:
        await context.bot.ban_chat_member(
            chat_id=chat.id,
            user_id=target.id,
            revoke_messages=True,
        )
        try:
            await message.delete()
        except Exception:
            pass
        await alert_action(
            context,
            chat.id,
            target,
            "Admin ใช้ /purge เพื่อล้างสแปมย้อนหลัง",
            "🚫 BAN + ลบข้อความทั้งหมดของ user",
        )
        return
    except Exception as e:
        logger.warning(f"/purge ban/revoke failed for user {target.id} in chat {chat.id}: {e}")

    try:
        await target_message.delete()
    except Exception:
        pass

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=target.id,
            permissions=ChatPermissions(can_send_messages=False),
        )
    except Exception as e:
        logger.warning(f"/purge fallback restrict failed for user {target.id} in chat {chat.id}: {e}")

    try:
        await message.delete()
    except Exception:
        pass

    await alert_action(
        context,
        chat.id,
        target,
        "Admin ใช้ /purge แต่ล้างประวัติไม่สำเร็จ จึงลบข้อความที่ reply และ mute แทน",
        "🔇 DELETE + MUTE ถาวร",
    )


# =============================
# GLOBAL ERROR HANDLER
# =============================
# [STABILITY #4] จับ error ทุกประเภทจาก PTB
# ป้องกัน unhandled exception ฆ่า bot process

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error

    if isinstance(error, RetryAfter):
        # [STABILITY] Telegram flood control — PTB จะ retry อัตโนมัติ รอแค่ log
        logger.warning(f"Telegram flood control: retry after {error.retry_after}s")
        await asyncio.sleep(error.retry_after)
        return

    if isinstance(error, (TimedOut, NetworkError)):
        # เน็ตหลุดชั่วคราว → PTB จะ reconnect เอง
        logger.warning(f"Network issue (will auto-recover): {error}")
        return

    if isinstance(error, Forbidden):
        # บอทถูกเตะออกจากกลุ่ม หรือไม่มีสิทธิ์
        logger.info(f"Forbidden (bot removed from group or no permission): {error}")
        return

    # error อื่นๆ → log ไว้ดูแต่ไม่ crash
    logger.error(f"Unhandled PTB error: {error}", exc_info=error)


# =============================
# MAIN
# =============================

if __name__ == "__main__":

    # [STABILITY #1] สร้าง semaphore ใน main thread ก่อน event loop เริ่ม
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    # Flask สำหรับ UptimeRobot ping (daemon thread)
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    logger.info(f"Health check server started on port {PORT}")

    # [STABILITY] ApplicationBuilder พร้อม timeout และ connection pool
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .connection_pool_size(16)       # รองรับหลายกลุ่มพร้อมกัน
        .read_timeout(15)               # รอ Telegram ตอบกลับ 15 วินาที
        .write_timeout(15)
        .connect_timeout(15)
        .pool_timeout(10)
        .build()
    )

    application.add_handler(ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(CommandHandler("purge", purge_command))
    application.add_handler(MessageHandler(filters.ALL, check_message))

    # [STABILITY #4] Global error handler
    application.add_error_handler(error_handler)

    logger.info("Bot started...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,  # ประมวลผล update ที่ค้างอยู่ตอน bot restart เพื่อไล่ลบสแปมย้อนหลัง
    )
