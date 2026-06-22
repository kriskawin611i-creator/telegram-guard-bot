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

def parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

def parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"Invalid {name}={raw!r}; using {default}")
        return default

def parse_chat_ids(raw: str | None) -> set[int]:
    chat_ids: set[int] = set()
    if not raw:
        return chat_ids
    for item in re.split(r"[,\s]+", raw.strip()):
        if not item:
            continue
        try:
            chat_ids.add(int(item))
        except ValueError:
            logger.warning(f"Ignoring invalid chat id in ALLOWED_CHAT_IDS: {item!r}")
    return chat_ids

def parse_usernames(raw: str | None) -> set[str]:
    usernames: set[str] = set()
    if not raw:
        return usernames
    for item in re.split(r"[,\s]+", raw.strip()):
        username = item.strip().lstrip("@").lower()
        if username:
            usernames.add(username)
    return usernames

TOKEN = os.getenv("BOT_TOKEN")
PORT  = parse_int_env("PORT", 10000)

# Optional production guards:
# ALLOWED_CHAT_IDS="-100111,-100222"  -> bot only works in these groups
# LEAVE_UNAUTHORIZED_CHATS=true       -> bot leaves groups not in the allowlist
# DROP_PENDING_UPDATES=false          -> process Telegram backlog on restart
ALLOWED_CHAT_IDS          = parse_chat_ids(os.getenv("ALLOWED_CHAT_IDS"))
OWNER_USER_IDS           = parse_chat_ids(os.getenv("OWNER_USER_IDS"))
ALLOWED_BOT_USERNAMES    = parse_usernames(os.getenv("ALLOWED_BOT_USERNAMES"))
LEAVE_UNAUTHORIZED_CHATS = parse_bool_env("LEAVE_UNAUTHORIZED_CHATS", False)
DROP_PENDING_UPDATES     = parse_bool_env("DROP_PENDING_UPDATES", True)
ALLOWED_UPDATE_TYPES     = ["message", "chat_member"]

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
user_last_processed: dict[tuple[int, int], float] = {}

# ถ้าบอทเพิ่งตื่นหลังล่ม Render จะส่ง pending updates เข้ามารัวมาก
# ข้อความที่เก่ากว่านี้จะถือเป็น backlog และไม่โดน per-user cooldown ข้าม
BACKLOG_MESSAGE_AGE = 30    # วินาที

# [STABILITY #3] Alert dedup — ถ้า user ถูก alert ไปแล้วใน 60 วินาที → ไม่ alert ซ้ำ
# กัน bot ส่ง alert ท่วม group เวลาโดน spam หนัก
ALERT_COOLDOWN      = 60    # วินาที
user_last_alert: dict[tuple[int, int], float] = {}

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
    return {
        "status": "ok",
        "muted_count": len(user_muted_permanent),
        "allowed_chat_ids": sorted(ALLOWED_CHAT_IDS),
        "allowed_bot_usernames": sorted(ALLOWED_BOT_USERNAMES),
        "drop_pending_updates": DROP_PENDING_UPDATES,
        "processed_updates_count": processed_updates_count,
        "last_update_at": last_update_at,
        "last_error_at": last_error_at,
        "last_error_message": last_error_message,
        "cache_sizes": {
            "join_times": len(join_times),
            "user_messages": len(user_messages),
            "user_last_processed": len(user_last_processed),
            "user_last_alert": len(user_last_alert),
            "admin_cache": len(admin_cache),
            "message_log_users": len(user_message_log),
        },
    }, 200

def run_web():
    app_web.run(host="0.0.0.0", port=PORT)

# =============================
# STORAGE
# =============================

UserKey = tuple[int, int]  # (chat_id, user_id)

join_times    : dict[UserKey, float]       = {}
user_messages : dict[UserKey, list[float]] = defaultdict(list)

last_update_at: float | None = None
last_error_at: float | None = None
last_error_message: str | None = None
processed_updates_count = 0
_runtime_cleanup_counter = 0
RUNTIME_CLEANUP_EVERY = 500
USER_STATE_TTL = 3600
ALERT_STATE_TTL = max(ALERT_COOLDOWN * 10, 600)
SPAM_WINDOW = 10

# [PURGE FIX] เก็บ message_id รายคน เพื่อให้ /purge ลบโพสต์ทั้งหมดของ user ย้อนหลังได้
# key = (chat_id, user_id) -> list[(message_id, timestamp)]
# revoke_messages ของ Telegram ลบได้แค่ 48 ชม.ล่าสุด และไม่การันตีว่าลบจริง
# จึงต้องเก็บ id เองแล้วสั่ง delete ตรงๆ
user_message_log: dict[tuple[int, int], list[tuple[int, float]]] = defaultdict(list)

MESSAGE_DELETABLE_AGE    = 48 * 3600   # บอทลบข้อความเก่ากว่า 48 ชม.ไม่ได้ (ลิมิต Telegram)
MESSAGE_LOG_MAX_PER_USER = 300         # เก็บ id ล่าสุดสูงสุด 300 ตัว/คน กัน memory leak
_record_counter          = 0          # ตัวนับสำหรับ trigger cleanup เป็นระยะ


def user_key(chat_id: int, user_id: int) -> UserKey:
    return (int(chat_id), int(user_id))

def muted_key_to_str(key: UserKey) -> str:
    return f"{key[0]}:{key[1]}"

def parse_muted_key(value) -> UserKey | None:
    if isinstance(value, dict):
        try:
            return user_key(value["chat_id"], value["user_id"])
        except Exception:
            return None

    if isinstance(value, str):
        raw = value.strip()
        if ":" not in raw:
            return None
        chat_id, user_id = raw.split(":", 1)
        try:
            return user_key(int(chat_id), int(user_id))
        except ValueError:
            return None

    # Legacy files stored only user_id. That is unsafe for multi-group bots,
    # so old entries are ignored instead of muting the user in every group.
    return None

def is_allowed_chat_id(chat_id: int) -> bool:
    return not ALLOWED_CHAT_IDS or chat_id in ALLOWED_CHAT_IDS

async def guard_allowed_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if not chat:
        return False

    if is_allowed_chat_id(chat.id):
        return True

    logger.warning(f"Ignoring unauthorized chat {chat.id} ({getattr(chat, 'title', None)!r})")
    if LEAVE_UNAUTHORIZED_CHATS:
        try:
            await context.bot.leave_chat(chat.id)
            logger.info(f"Left unauthorized chat {chat.id}")
        except Exception as e:
            logger.warning(f"Failed to leave unauthorized chat {chat.id}: {e}")
    return False

def mark_update_seen() -> None:
    global last_update_at, processed_updates_count, _runtime_cleanup_counter
    last_update_at = time.time()
    processed_updates_count += 1
    _runtime_cleanup_counter += 1
    if _runtime_cleanup_counter >= RUNTIME_CLEANUP_EVERY:
        _runtime_cleanup_counter = 0
        cleanup_runtime_state()

def cleanup_runtime_state() -> None:
    now = time.time()

    for key, t in list(join_times.items()):
        if now - t > JOIN_EXPIRE:
            join_times.pop(key, None)

    for key, times in list(user_messages.items()):
        user_messages[key] = [t for t in times if now - t < SPAM_WINDOW]
        if not user_messages[key]:
            user_messages.pop(key, None)

    for key, t in list(user_last_processed.items()):
        if now - t > USER_STATE_TTL:
            user_last_processed.pop(key, None)

    for key, t in list(user_last_alert.items()):
        if now - t > ALERT_STATE_TTL:
            user_last_alert.pop(key, None)

    for key, (_, expire) in list(admin_cache.items()):
        if now >= expire:
            admin_cache.pop(key, None)

    cleanup_message_log()


def record_message(chat_id: int, user_id: int, message_id: int) -> None:
    """บันทึก message_id ของ user ไว้สำหรับ /purge — พร้อม prune ของเก่าและ cap จำนวน"""
    global _record_counter
    now = time.time()
    log = user_message_log[(chat_id, user_id)]
    log.append((message_id, now))

    # prune ข้อความที่เกิน 48 ชม. (ลบไม่ได้อยู่แล้ว) + จำกัดจำนวน
    cutoff = now - MESSAGE_DELETABLE_AGE
    log[:] = [e for e in log if e[1] > cutoff][-MESSAGE_LOG_MAX_PER_USER:]

    _record_counter += 1
    if _record_counter >= 500:
        _record_counter = 0
        cleanup_message_log()


def cleanup_message_log() -> None:
    """ลบ key ที่ข้อความหมดอายุทั้งหมดแล้ว กัน dict โตไม่จำกัด"""
    now    = time.time()
    cutoff = now - MESSAGE_DELETABLE_AGE
    dead   = []
    for key, log in list(user_message_log.items()):
        log[:] = [e for e in log if e[1] > cutoff]
        if not log:
            dead.append(key)
    for key in dead:
        user_message_log.pop(key, None)


async def purge_user_messages(context, chat_id: int, user_id: int,
                              extra_ids: list[int] | None = None) -> int:
    """
    ลบโพสต์ทั้งหมดของ user คนนี้ในกลุ่ม (ที่ยังอยู่ในกรอบ 48 ชม.)
    คืนค่าจำนวนข้อความที่ลบสำเร็จ
    """
    now = time.time()
    log = user_message_log.get((chat_id, user_id), [])

    ids: set[int] = set(extra_ids or [])     # ข้อความที่ admin reply → รวมไว้เสมอ
    for mid, t in log:
        if now - t < MESSAGE_DELETABLE_AGE:  # เกิน 48 ชม. ลบไม่ได้ ข้ามไป
            ids.add(mid)

    # เคลียร์ log ของ user คนนี้ทิ้ง (โดนแบนแล้ว ไม่ต้องเก็บต่อ)
    user_message_log.pop((chat_id, user_id), None)

    if not ids:
        return 0

    ids_list = sorted(ids)
    deleted  = 0

    # ลบทีละชุด 100 ตัว (delete_messages = bulk API ของ Telegram)
    for i in range(0, len(ids_list), 100):
        chunk = ids_list[i:i + 100]
        try:
            await context.bot.delete_messages(chat_id=chat_id, message_ids=chunk)
            deleted += len(chunk)
        except Exception as e:
            # fallback: ลบทีละข้อความ (เผื่อ PTB เก่าไม่มี delete_messages
            # หรือบางข้อความเกิน 48 ชม. / ลบไปแล้ว)
            logger.warning(f"bulk delete failed in chat {chat_id}: {e} — fallback per-message")
            for mid in chunk:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                    deleted += 1
                except Exception:
                    pass

    logger.info(f"purge_user_messages: deleted {deleted}/{len(ids_list)} msgs "
                f"for user {user_id} in chat {chat_id}")
    return deleted

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
_muted_lock = threading.Lock()

def load_muted() -> set:
    try:
        with open(MUTED_FILE, "r", encoding="utf-8") as f:
            raw_items = json.load(f)
    except FileNotFoundError:
        return set()
    except Exception as e:
        logger.warning(f"Failed to load {MUTED_FILE}: {e}")
        return set()

    muted: set[UserKey] = set()
    legacy_count = 0
    for item in raw_items:
        key = parse_muted_key(item)
        if key:
            muted.add(key)
        else:
            legacy_count += 1

    if legacy_count:
        logger.warning(
            f"Ignored {legacy_count} legacy muted entries without chat_id. "
            "Use /purge again in the target group if those users should stay muted."
        )

    return muted

def save_muted() -> None:
    tmp_file = f"{MUTED_FILE}.tmp"
    try:
        data = sorted(muted_key_to_str(key) for key in user_muted_permanent)
        with _muted_lock:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_file, MUTED_FILE)
    except Exception as e:
        logger.warning(f"Failed to save {MUTED_FILE}: {e}")

user_muted_permanent: set[UserKey] = load_muted()

def is_permanently_muted(chat_id: int, user_id: int) -> bool:
    return user_key(chat_id, user_id) in user_muted_permanent

def add_permanent_mute(chat_id: int, user_id: int) -> None:
    user_muted_permanent.add(user_key(chat_id, user_id))
    save_muted()

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
        key  = user_key(chat_id, user.id)
        last = user_last_alert.get(key, 0)
        if now - last < ALERT_COOLDOWN:
            return  # ไม่ alert ซ้ำ
        user_last_alert[key] = now

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
    r"hxxp[s]?://",
    r"www\.",
    r"t\.me/",
    r"telegram\.me/",
    r"telegram\.dog/",
    r"@\w+",
    r"\b[a-zA-Z0-9-]+\.(com|net|org|xyz|top|club|site|vip|online|live|shop|io|me|link|click|app|pro|cc|ch|tv|to|co|info|biz|dev|fun|store|website|space|cam|lol|icu|one|bot)\b",
    r"bit\.ly/",
    r"tinyurl\.com/",
    r"shorturl\.",
    r"cutt\.ly/",
    r"is\.gd/",
    r"buff\.ly/",
    r"rebrand\.ly/",
    r"lnkd\.in/",
]

LINK_ENTITY_TYPES = {
    "url",
    "text_link",
    "mention",
    "text_mention",
    "email",
}

INVISIBLE_CHARS_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
LINK_NOISE_RE = re.compile(r"[\s\u00a0\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]+")

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
    text = unicodedata.normalize("NFKC", text)
    text = INVISIBLE_CHARS_RE.sub("", text)
    return text.lower()

def compact_link_text(text: str) -> str:
    return LINK_NOISE_RE.sub("", normalize_text(text))

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
    compact = compact_link_text(text)
    return any(re.search(p, text) for p in LINK_PATTERNS) or any(
        re.search(p, compact) for p in LINK_PATTERNS
    )

def entity_type_name(entity) -> str:
    return str(getattr(entity, "type", "")).lower().rsplit(".", 1)[-1]

def message_has_link(message) -> bool:
    text = message.text or message.caption or ""
    if contains_link(text):
        return True

    entities = list(getattr(message, "entities", None) or [])
    entities += list(getattr(message, "caption_entities", None) or [])

    for entity in entities:
        entity_type = entity_type_name(entity)
        if entity_type in LINK_ENTITY_TYPES:
            return True
        if getattr(entity, "url", None):
            return True

    return False

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

def is_spam(chat_id: int, user_id: int) -> bool:
    now = time.time()
    key = user_key(chat_id, user_id)
    user_messages[key] = [t for t in user_messages[key] if now - t < SPAM_WINDOW]
    user_messages[key].append(now)
    return len(user_messages[key]) >= 5

def is_backlog_message(message) -> bool:
    msg_date = getattr(message, "date", None)
    if not msg_date:
        return False
    return time.time() - msg_date.timestamp() > BACKLOG_MESSAGE_AGE

# =============================
# DETECT FORWARD / STORY / GIFT
# =============================

def is_any_forward(message) -> bool:
    if getattr(message, "forward_from", None):        return True
    if getattr(message, "forward_from_chat", None):   return True
    if getattr(message, "forward_sender_name", None): return True
    if getattr(message, "forward_date", None):        return True
    if getattr(message, "forward_origin", None):      return True
    if getattr(message, "via_bot", None) and getattr(message, "forward_date", None): return True
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
    if not await guard_allowed_chat(update, context):
        return
    mark_update_seen()

    if update.chat_member.new_chat_member.status == "member":
        chat_id = update.effective_chat.id
        user_id = update.chat_member.new_chat_member.user.id
        join_times[user_key(chat_id, user_id)] = time.time()
        cleanup_join_times()


async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat = update.effective_chat
    inviter = update.effective_user

    if not message or not chat:
        return

    if not await guard_allowed_chat(update, context):
        return
    mark_update_seen()

    new_members = message.new_chat_members or []
    if not new_members:
        return

    for member in new_members:
        join_times[user_key(chat.id, member.id)] = time.time()

        if not getattr(member, "is_bot", False):
            continue

        if member.id == context.bot.id:
            continue

        username = (member.username or "").lstrip("@").lower()
        if username and username in ALLOWED_BOT_USERNAMES:
            logger.info(f"Allowed bot @{username} joined chat {chat.id}")
            continue

        if await is_admin_cached(context, chat.id, member.id):
            logger.info(f"Admin bot @{username or member.id} is allowed in chat {chat.id}")
            continue

        inviter_is_admin = bool(inviter and await is_admin_cached(context, chat.id, inviter.id))
        if inviter_is_admin:
            logger.info(
                f"Admin user {inviter.id} added bot @{username or member.id} "
                f"to chat {chat.id}; allowed"
            )
            continue

        bot_banned = False
        inviter_banned = False

        try:
            await context.bot.ban_chat_member(
                chat_id=chat.id,
                user_id=member.id,
                revoke_messages=True,
            )
            bot_banned = True
        except Exception as e:
            logger.warning(f"Failed to ban added bot {member.id} in chat {chat.id}: {e}")

        if inviter and inviter.id not in {member.id, context.bot.id}:
            try:
                if not await is_admin_cached(context, chat.id, inviter.id):
                    await context.bot.ban_chat_member(
                        chat_id=chat.id,
                        user_id=inviter.id,
                        revoke_messages=True,
                    )
                    inviter_banned = True
            except Exception as e:
                logger.warning(f"Failed to ban bot inviter {inviter.id} in chat {chat.id}: {e}")

        try:
            await message.delete()
        except Exception:
            pass

        action_parts = []
        if bot_banned:
            action_parts.append("แบนบอทแปลกหน้า")
        if inviter_banned:
            action_parts.append("แบนคนที่เพิ่มบอท")
        action_text = " + ".join(action_parts) if action_parts else "พยายามแบนแล้ว แต่สิทธิ์บอทอาจไม่พอ"
        bot_label = f"@{username}" if username else f"ID: {member.id}"

        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    "🚫 GUARD BOT\n\n"
                    "พบการเพิ่มบอทโดยผู้ใช้ที่ไม่ใช่แอดมิน\n"
                    f"บอท: {bot_label}\n"
                    f"การดำเนินการ: {action_text}"
                )
            )
        except Exception:
            pass

    cleanup_join_times()

# =============================
# MAIN FILTER
# =============================

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.message
    if not message:
        return

    if not await guard_allowed_chat(update, context):
        return
    mark_update_seen()

    chat_id = update.effective_chat.id

    command_text = message.text or ""
    if re.match(r"^/purge(?:@\w+)?(?:\s|$)", command_text, re.IGNORECASE):
        await purge_command(update, context)
        return

    if message.sender_chat:
        return

    user = message.from_user
    if not user:
        return

    # [PURGE FIX] บันทึก message_id ของ user ไว้ก่อน เผื่อ admin สั่ง /purge ภายหลัง
    # ทำก่อน cooldown เพื่อให้เก็บ id ของพวก flood ได้ครบ แม้ข้อความนั้นจะถูกข้ามการตรวจ
    record_message(chat_id, user.id, message.message_id)

    backlog = is_backlog_message(message)

    # [STABILITY #2] Per-user cooldown — fast path ก่อน semaphore
    # ถ้า user ส่งถี่เกิน 0.3 วินาที ตรวจสอบว่าอยู่ใน muted หรือเปล่า
    now  = time.time()
    key  = user_key(chat_id, user.id)
    last = user_last_processed.get(key, 0)
    if not backlog and now - last < USER_COOLDOWN:
        # ถ้าถูก mute ถาวรแล้ว → ลบทันทีโดยไม่รอ semaphore
        if is_permanently_muted(chat_id, user.id):
            try:
                await message.delete()
            except Exception:
                pass
        return
    user_last_processed[key] = now

    # [STABILITY #1] Global semaphore — process ได้สูงสุด MAX_CONCURRENT พร้อมกัน
    if _semaphore is None:
        await _process_message(message, user, chat_id, context)
        return

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
            add_permanent_mute(chat_id, user.id)
            if PURGE_SPAMMER_HISTORY_ON_DETECT:
                try:
                    await context.bot.ban_chat_member(
                        chat_id=chat_id,
                        user_id=user.id,
                        revoke_messages=True,
                    )
                    # [PURGE FIX] ลบโพสต์ทั้งหมดของสแปมเมอร์ที่ตรวจเจอ
                    # (revoke_messages ตอน ban ไม่การันตีว่าลบจริง จึงลบเองด้วย)
                    await purge_user_messages(context, chat_id, user.id)
                    return
                except Exception as e:
                    logger.warning(f"ban failed for user {user.id} in chat {chat_id}: {e}")

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
        if is_permanently_muted(chat_id, user.id):
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
        join_key = user_key(chat_id, user.id)
        if join_key in join_times and time.time() - join_times[join_key] < 60:
            if message_has_link(message):
                try: await message.delete()
                except Exception: pass
                await alert_action(context, chat_id, user, "สมาชิกใหม่ส่งลิงก์", AUTO_ACTION_TEXT)
                await mute_permanent()
                return

        # ——— Strict link filter ———
        if message_has_link(message):
            try: await message.delete()
            except Exception: pass
            await alert_action(context, chat_id, user, "ส่งลิงก์ทุกชนิด", AUTO_ACTION_TEXT)
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
        if is_spam(chat_id, user.id):
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

async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat:
        return

    title = getattr(chat, "title", None) or getattr(chat, "username", None) or "private chat"
    text = (
        f"chat_id: {chat.id}\n"
        f"type: {chat.type}\n"
        f"title: {title}"
    )

    logger.info(f"/chatid requested by user={getattr(user, 'id', None)}: {text.replace(chr(10), ' | ')}")

    try:
        await message.delete()
    except Exception:
        pass

    if user and (not OWNER_USER_IDS or user.id in OWNER_USER_IDS):
        try:
            await context.bot.send_message(chat_id=user.id, text=text)
            if chat.type != "private":
                await context.bot.send_message(
                    chat_id=chat.id,
                    text="ส่ง chat_id ให้ในแชตส่วนตัวแล้ว"
                )
            return
        except Exception as e:
            logger.warning(f"Could not DM chat_id to user {user.id}: {e}")

    if chat.type == "private":
        await message.reply_text(text)
    else:
        await context.bot.send_message(
            chat_id=chat.id,
            text="บันทึก chat_id ไว้ใน Render Logs แล้ว"
        )


async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not chat:
        return

    if not await guard_allowed_chat(update, context):
        return
    mark_update_seen()

    sender_chat = getattr(message, "sender_chat", None)
    sender_chat_is_group = bool(sender_chat and sender_chat.id == chat.id)

    logger.info(
        f"/purge received in chat {chat.id} "
        f"user={getattr(user, 'id', None)} "
        f"sender_chat={getattr(sender_chat, 'id', None)} "
        f"reply={bool(message.reply_to_message)}"
    )

    if not sender_chat_is_group and (not user or not await is_admin_cached(context, chat.id, user.id)):
        await message.reply_text("คำสั่งนี้ใช้ได้เฉพาะ admin")
        return

    target_message = message.reply_to_message
    if not target_message or not target_message.from_user:
        await message.reply_text("ใช้ /purge โดย reply ข้อความสแปมที่ต้องการล้าง")
        return

    target = target_message.from_user
    if await is_admin_cached(context, chat.id, target.id):
        await message.reply_text("ไม่ล้างข้อความของ admin")
        return

    add_permanent_mute(chat.id, target.id)

    # ——— 1) Ban + revoke (เป็น pass แรกแบบฟรี — แต่ไม่พึ่งมันลบจริง) ———
    ban_ok = False
    try:
        # revoke_messages=True — ให้ Telegram ช่วยลบอีกชั้น (เราจะลบทั้งหมดอยู่แล้ว)
        await context.bot.ban_chat_member(
            chat_id=chat.id,
            user_id=target.id,
            revoke_messages=True,
        )
        ban_ok = True
        logger.info(f"/purge ban OK for user {target.id} in chat {chat.id}")
    except Exception as e:
        logger.warning(f"/purge ban failed for user {target.id} in chat {chat.id}: {e}")
        # ban ไม่ผ่าน → fallback restrict (mute) แทน
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=target.id,
                permissions=ChatPermissions(can_send_messages=False),
            )
        except Exception as e2:
            logger.warning(f"/purge fallback restrict failed for user {target.id}: {e2}")

    # ——— 2) ลบโพสต์ทั้งหมดของ user คนนี้ย้อนหลัง ———
    # reply โพสต์เดียว → ลบทุกโพสต์ของ user คนนั้น (ที่อยู่ในกรอบ 48 ชม.)
    deleted = await purge_user_messages(
        context, chat.id, target.id,
        extra_ids=[target_message.message_id],   # โพสต์ที่ admin reply → รวมไว้เสมอ
    )

    # ลบคำสั่ง /purge ของ admin
    try:
        await message.delete()
    except Exception:
        pass

    # ——— 3) รายงานผล ———
    if deleted > 0:
        action_text = f"🚫 BAN + ลบโพสต์ทั้งหมด {deleted} ข้อความ"
    elif ban_ok:
        action_text = ("🚫 BAN สำเร็จ — แต่ลบข้อความไม่ได้ "
                       "(อาจเก่ากว่า 48 ชม. หรือถูกลบไปแล้ว)")
    else:
        action_text = "⚠️ ลบ/แบนไม่สำเร็จ — ตรวจสิทธิ์ Delete Messages ของบอท"

    await alert_action(
        context,
        chat.id,
        target,
        "Admin ใช้ /purge เพื่อล้างสแปมย้อนหลัง",
        action_text,
    )


# =============================
# GLOBAL ERROR HANDLER
# =============================
# [STABILITY #4] จับ error ทุกประเภทจาก PTB
# ป้องกัน unhandled exception ฆ่า bot process

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    global last_error_at, last_error_message
    error = context.error
    last_error_at = time.time()
    last_error_message = repr(error)[:500]

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

    if not TOKEN:
        logger.error("BOT_TOKEN is not set; cannot start Telegram bot.")
        raise SystemExit(1)

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
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))
    application.add_handler(CommandHandler("chatid", chatid_command))
    application.add_handler(CommandHandler("purge", purge_command))
    application.add_handler(MessageHandler(filters.ALL, check_message))

    # [STABILITY #4] Global error handler
    application.add_error_handler(error_handler)

    logger.info("Bot started...")

    application.run_polling(
        allowed_updates=ALLOWED_UPDATE_TYPES,
        drop_pending_updates=DROP_PENDING_UPDATES,
    )
