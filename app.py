import os
import re
import json
import html
import time
import asyncio
import logging
import random
import threading
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse

from flask import Flask
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, TimedOut, NetworkError, Forbidden, BadRequest
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

# HTTPX logs include the full Telegram Bot API URL. Keep them out of INFO logs
# so the bot token is never printed to Render logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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

def parse_welcome_group_links(raw: str | None) -> list[dict]:
    links: list[dict] = []
    if not raw:
        return links

    for item in re.split(r"[\n,;]+", raw.strip()):
        item = item.strip()
        if not item:
            continue

        parts = [part.strip() for part in item.split("|")]
        if len(parts) < 2:
            logger.warning(f"Ignoring invalid WELCOME_GROUP_LINKS item: {item!r}")
            continue

        title = parts[0]
        url = parts[1]
        if not title or not url.startswith(("http://", "https://")):
            logger.warning(f"Ignoring invalid welcome link: {item!r}")
            continue

        chat_id = None
        if len(parts) >= 3 and parts[2]:
            try:
                chat_id = int(parts[2])
            except ValueError:
                logger.warning(f"Ignoring invalid welcome chat id in item: {item!r}")

        sex = None
        if len(parts) >= 4 and parts[3]:
            normalized_sex = parts[3].lower()
            if normalized_sex == "male":
                sex = "Male"
            elif normalized_sex == "female":
                sex = "Female"
            else:
                logger.warning(f"Ignoring invalid welcome sex in item: {item!r}")

        links.append({"title": title, "url": url, "chat_id": chat_id, "sex": sex})

    return links

TOKEN = os.getenv("BOT_TOKEN")
PORT  = parse_int_env("PORT", 10000)

# Optional production guards:
# ALLOWED_CHAT_IDS="-100111,-100222"  -> bot only works in these groups
# ALLOWED_SENDER_CHAT_IDS="-100111,-100222" -> trusted groups/channels may post as sender_chat
# LEAVE_UNAUTHORIZED_CHATS=true       -> bot leaves groups not in the allowlist
# DROP_PENDING_UPDATES=false          -> process Telegram backlog on restart
ALLOWED_CHAT_IDS          = parse_chat_ids(os.getenv("ALLOWED_CHAT_IDS"))
ALLOWED_SENDER_CHAT_IDS   = parse_chat_ids(os.getenv("ALLOWED_SENDER_CHAT_IDS"))
OWNER_USER_IDS           = parse_chat_ids(os.getenv("OWNER_USER_IDS"))
ALLOWED_BOT_USERNAMES    = parse_usernames(os.getenv("ALLOWED_BOT_USERNAMES"))
LEAVE_UNAUTHORIZED_CHATS = parse_bool_env("LEAVE_UNAUTHORIZED_CHATS", False)
DROP_PENDING_UPDATES     = parse_bool_env("DROP_PENDING_UPDATES", False)
ALLOWED_UPDATE_TYPES     = ["message", "edited_message", "chat_member"]

# ========================================================
# [COMPREHENSIVE ANTI-SPAM GUARDS] นโยบายป้องกันสแปมแบบครบวงจร
# สกัดกั้นทุกเทคนิคการหลบเลี่ยง (Checklist, Poll, Contact, Venue, Invoice, Game, Inline Bot)
# ========================================================
BLOCK_ALL_CHECKLISTS     = parse_bool_env("BLOCK_ALL_CHECKLISTS", True)
BLOCK_ALL_POLLS          = parse_bool_env("BLOCK_ALL_POLLS", False)
BLOCK_ALL_CONTACTS       = parse_bool_env("BLOCK_ALL_CONTACTS", True)
BLOCK_ALL_VENUES         = parse_bool_env("BLOCK_ALL_VENUES", True)
BLOCK_ALL_INVOICES       = parse_bool_env("BLOCK_ALL_INVOICES", True)
BLOCK_ALL_GAMES          = parse_bool_env("BLOCK_ALL_GAMES", True)
BLOCK_ALL_VIA_BOT        = parse_bool_env("BLOCK_ALL_VIA_BOT", True)
BLOCK_ALL_PAID_MEDIA     = parse_bool_env("BLOCK_ALL_PAID_MEDIA", True)

# ข้อความต้อนรับค่าเริ่มต้น
DEFAULT_WELCOME_TEXT = (
    "✨ ยินดีต้อนรับทุกท่านสู่พื้นที่ความบันเทิง! 🎉\n\n"
    "👋 ต้อนรับคุณ: {name}\n"
    "🌟 สมาชิกใหม่รอบนี้: {count} คน\n\n"
    "🔥 คัดมาให้แล้ว กลุ่มเด็ดในเครือครบทุกสาย!\n"
    "💯 การันตีเข้าฟรีทุกกลุ่ม ไม่มีค่าใช้จ่ายใดๆ\n"
    "👇 เลือกสายที่ชอบจากปุ่มด้านล่าง แล้วลุยเลย!"
)

# ลิงก์รูปภาพต้อนรับค่าเริ่มต้น (Cloudinary CDN - หมุนเวียนสลับ 5-6 รูปอัตโนมัติ)
DEFAULT_WELCOME_IMAGES: list[str] = [
    "https://res.cloudinary.com/u6yzvdyg/image/upload/v1794821596/image-gen-3_20261014-105001.png",
    "https://res.cloudinary.com/u6yzvdyg/image/upload/v1794821596/%E0%B9%82%E0%B8%9B%E0%B8%AA%E0%B9%80%E0%B8%95%E0%B8%AD%E0%B8%A3%E0%B9%8C%E0%B9%80%E0%B8%82%E0%B9%89%E0%B8%B2%E0%B8%81%E0%B8%A5%E0%B8%B8%E0%B9%88%E0%B8%A1_Telegram_%E0%B8%AA%E0%B9%84%E0%B8%95%E0%B8%A5%E0%B9%8C%E0%B8%99%E0%B8%B5%E0%B8%AD%E0%B8%AD%E0%B8%99.png",
    "https://res.cloudinary.com/u6yzvdyg/image/upload/v1794821596/%E0%B9%82%E0%B8%9B%E0%B8%AA%E0%B9%80%E0%B8%95%E0%B8%AD%E0%B8%A3%E0%B9%8C%E0%B8%8A%E0%B8%A7%E0%B8%99%E0%B9%80%E0%B8%82%E0%B9%89%E0%B8%B2%E0%B8%81%E0%B8%A5%E0%B8%B8%E0%B9%88%E0%B8%A1_Telegram_%E0%B8%AA%E0%B8%B5%E0%B8%8A%E0%B8%A1%E0%B8%9E%E0%B8%B9%E0%B8%AA%E0%B8%94%E0%B9%83%E0%B8%AA-2.png",
    "https://res.cloudinary.com/u6yzvdyg/image/upload/v1794821596/%E0%B9%82%E0%B8%9B%E0%B8%A3%E0%B9%82%E0%B8%A1%E0%B8%8A%E0%B8%B1%E0%B9%88%E0%B8%99%E0%B9%80%E0%B8%82%E0%B9%89%E0%B8%B2%E0%B8%81%E0%B8%A5%E0%B8%B8%E0%B9%88%E0%B8%A1_Telegram_%E0%B8%A3%E0%B8%B4%E0%B8%A1%E0%B8%AA%E0%B8%A3%E0%B8%B0-3.png",
    "https://res.cloudinary.com/u6yzvdyg/image/upload/v1794821595/%E0%B9%82%E0%B8%9B%E0%B8%A3%E0%B9%82%E0%B8%A1%E0%B8%95%E0%B8%81%E0%B8%A5%E0%B8%B8%E0%B9%88%E0%B8%A1_Telegram_%E0%B8%A3%E0%B8%B4%E0%B8%A1%E0%B8%AA%E0%B8%A3%E0%B8%B0%E0%B8%AA%E0%B8%B8%E0%B8%94%E0%B8%AA%E0%B8%94%E0%B9%83%E0%B8%AA-4.png",
]

def parse_welcome_images(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    items = []
    for part in re.split(r'[\r\n,;]+', raw_value):
        cleaned = part.strip()
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            items.append(cleaned)
    return items

DEFAULT_WELCOME_GROUP_LINKS: list[dict] = [
    # --- กลุ่มผู้หญิง (Female) ---
    {"title": "กลุ่มดูดxี", "url": "https://t.me/doodmheev1", "chat_id": -1004371195122, "sex": "Female"},
    {"title": "คนคลั่งxี", "url": "https://t.me/klangheev1", "chat_id": -1004375248238, "sex": "Female"},
    {"title": "จุดกางแตด", "url": "https://t.me/judkangtadd", "chat_id": -1004375050476, "sex": "Female"},
    {"title": "แจกวาร์ป งาน แรร์ ทาง บ้าน", "url": "https://t.me/clipfreex", "chat_id": -1002035635578, "sex": "Female"},
    {"title": "ช่องส่องหมี", "url": "https://t.me/shongmhee", "chat_id": -1001880394930, "sex": "Female"},
    {"title": "ถ้ำหมี", "url": "https://t.me/+WeYXLsfg24c5YmVl", "chat_id": -1003865858642, "sex": "Female"},
    {"title": "ถ้ำxีระเบิด", "url": "https://t.me/+VdnlHkxPUkAzYjU1", "chat_id": -1003990789699, "sex": "Female"},
    {"title": "รวมพลคนบ้าxี", "url": "https://t.me/baheev1", "chat_id": -1004362852597, "sex": "Female"},
    {"title": "ส่องหมี", "url": "https://t.me/shongmheeav", "chat_id": -1003854594118, "sex": "Female"},
    {"title": "หนังชมพูพาวทัวร์", "url": "https://t.me/+uSukf44DUmJiMTA1", "chat_id": -1004361620745, "sex": "Female"},
    {"title": "av นานาชาติ", "url": "https://t.me/avshongmhee", "chat_id": -1003771094654, "sex": "Female"},
    {"title": "xีพาหลอน", "url": "https://t.me/+o3i__Vseq_BmZTQ1", "chat_id": -1004451470486, "sex": "Female"},
    {"title": "xีฟรีมีให้ดู", "url": "https://t.me/+ekNGZZ0jxiY5MTY1", "chat_id": -1004445162475, "sex": "Female"},
    {"title": "หลวงพ่อโต V3", "url": "https://t.me/+ygBNsfWZhrszOWQ9", "chat_id": -1003750183920, "sex": "Female"},
    {"title": "หลวงพ่อโต V2", "url": "https://t.me/+FJreLFsYRGczYWE9", "chat_id": -1003964195315, "sex": "Female"},
    {"title": "หลวงพ่อโต V1", "url": "https://t.me/+UKpA_MFYjeYxYWU1", "chat_id": -1003964261161, "sex": "Female"},
    {"title": "อัศวินโต๊ะกลม", "url": "https://t.me/+YhaQ5nEkDe1mOWE1", "chat_id": -1004347482362, "sex": "Female"},
    {"title": "อัศวินโต้ะส้ม", "url": "https://t.me/+JJHSILN2jPJlZjY1", "chat_id": -1004354431499, "sex": "Female"},
    {"title": "อัศวินโต๊ะหมี", "url": "https://t.me/+aHZjKajBxlJkNTY1", "chat_id": -1004343764829, "sex": "Female"},
    {"title": "รักษาxี", "url": "https://t.me/+IkpgFxolriU0ZTM1", "chat_id": -1004303453836, "sex": "Female"},
    {"title": "ศาลาพักควx", "url": "https://t.me/+X5vxtODrQSM2Yjk1", "chat_id": -1004448563805, "sex": "Female"},
    {"title": "คลิปหรรษา", "url": "https://t.me/+8ST4DjJ4_lRiYTQ1", "chat_id": -1004458793062, "sex": "Female"},
    {"title": "ควxเพื่อนxี", "url": "https://t.me/+OaY34hglMbZkZDE1", "chat_id": -1004450739951, "sex": "Female"},
    {"title": "กลิ่นดีที่xี", "url": "https://t.me/+kyWiidOVNpUwMzM1", "chat_id": -1004304758102, "sex": "Female"},
    {"title": "xีดีที่นี้", "url": "https://t.me/+8Wn_BZ2vBso4YmY9", "chat_id": -1004296876048, "sex": "Female"},
    {"title": "เขื่อนจะแตก", "url": "https://t.me/+9nE4WdvB6FkyNjU1", "chat_id": -1003979538788, "sex": "Female"},
    {"title": "ช่วงเวลาวัยหนุ่ม", "url": "https://t.me/+l1791fWv_ZgwZDdl", "chat_id": -1003913348963, "sex": "Female"},
    {"title": "ดoนxีบิน", "url": "https://t.me/+t9fiV42gGXVkMDU1", "chat_id": -1004272453579, "sex": "Female"},
    {"title": "นักล่าxี", "url": "https://t.me/+6-9URc7-fGxiZGY1", "chat_id": -1003957812190, "sex": "Female"},
    {"title": "วาร์ปxีรีเทิร์น", "url": "https://t.me/+c0BvcQliwLg1ODY1", "chat_id": -1004291436088, "sex": "Female"},
    {"title": "เหนียวมือครับ", "url": "https://t.me/+tx0yOG32-MVmNzFl", "chat_id": -1004499308589, "sex": "Female"},
    {"title": "ทิชชู่หมดม้วน", "url": "https://t.me/+5UL4fiTEalYyZjg1", "chat_id": -1003979381617, "sex": "Female"},
    {"title": "ราตรีนี้มีวาร์ป", "url": "https://t.me/+fbFoUa7rya4yNDk1", "chat_id": -1004298274076, "sex": "Female"},
    {"title": "ตาค้างยันหว่าง", "url": "https://t.me/+CnTECVkSUrM1NzVl", "chat_id": -1004423991150, "sex": "Female"},
    {"title": "ถ้ำนี้หมีดุ", "url": "https://t.me/+OxFEmH9dibk2Mzk1", "chat_id": -1004414997183, "sex": "Female"},
    {"title": "ซอยตันมันหยด", "url": "https://t.me/+rOvgV2YYqpQ4NzU1", "chat_id": -1004363237865, "sex": "Female"},
    {"title": "ทีเด็ดแดนลับ", "url": "https://t.me/+a6SWU4_21EJhYjU1", "chat_id": -1004363883271, "sex": "Female"},
    {"title": "ผีอำจนเตียงสั่น", "url": "https://t.me/+cfajjE7u7l42MDY1", "chat_id": -1003911470527, "sex": "Female"},
    {"title": "เล่นผีผ้าห่ม", "url": "https://t.me/+8Fvliv6TyUEwZDU1", "chat_id": -1003737652780, "sex": "Female"},
    {"title": "ปราบผีอาหวัง", "url": "https://t.me/+aMxsA4fOq6RhYWE1", "chat_id": -1004418279680, "sex": "Female"},
    {"title": "คลิปตลกเบาสมอง", "url": "https://t.me/+45fGwZHF4PJhMWY1", "chat_id": -1004379471744, "sex": "Female"},
    {"title": "Dooballvip24", "url": "https://t.me/+V9QSI8WNxLZhY2M1", "chat_id": -1004350098344, "sex": "Female"},

    # --- กลุ่มผู้ชาย (Male) ---
    {"title": "โหนกระดอ", "url": "https://t.me/+Tirl3g9R0ZoxMjll", "chat_id": -1003732267246, "sex": "Male"},
    {"title": "ซดน้ำกาม", "url": "https://t.me/Sodnamkam", "chat_id": -1003896586263, "sex": "Male"},
    {"title": "ผู้หล่อบอกต่อด้วย", "url": "https://t.me/+6NTwQa3tBSNmZDNl", "chat_id": -1004436318702, "sex": "Male"},
    {"title": "เกย์นัว", "url": "https://t.me/+-yTwB68YcCpjZjA1", "chat_id": -1004414579757, "sex": "Male"},
    {"title": "เกย์ตัวตึง", "url": "https://t.me/+wy4F-MnoMyc4NjQ1", "chat_id": -1003944373391, "sex": "Male"},
    {"title": "แซ่บลืมผัว", "url": "https://t.me/+bo8mOfm4lSQyYjJl", "chat_id": -1004269071183, "sex": "Male"},
    {"title": "เกย์นัวทั่วไทย", "url": "https://t.me/+wXzRItHRxKlhNTg1", "chat_id": -1004480688488, "sex": "Male"},
    {"title": "หิวผู้จนตาลาย", "url": "https://t.me/+V-mrzgNjJsE2ODA9", "chat_id": -1003943751968, "sex": "Male"},
    {"title": "กล้วยหอม", "url": "https://t.me/+knxqhpUXNAIxN2Rl", "chat_id": -1003882571475, "sex": "Male"},
    {"title": "ผู้ชายขายน้ำ", "url": "https://t.me/+5KbHhpjj0Lc5ZThl", "chat_id": -1003994940584, "sex": "Male"},
    {"title": "แซ่บไม่ไหว", "url": "https://t.me/+hLWitwnUT65jMGZl", "chat_id": -1004347432718, "sex": "Male"},
    {"title": "คลังสาวแซ่บ", "url": "https://t.me/+VrLqO6bWm5k0YzBl", "chat_id": -1004324481876, "sex": "Male"},
    {"title": "แซ่บตัวแม่", "url": "https://t.me/+ZAvXzFFAXZ8xZDY1", "chat_id": -1004494015250, "sex": "Male"},
    {"title": "แซ่บไม่พัก", "url": "https://t.me/+NXYafR0HFVtiNmY1", "chat_id": -1004445151646, "sex": "Male"},
    {"title": "สาวขี้อ้อน", "url": "https://t.me/+JovwmGEeHyMwYThl", "chat_id": -1003663690684, "sex": "Male"},
    {"title": "OnlyFansX", "url": "https://t.me/+gsjw-bZcxJYxNzNl", "chat_id": -1004411326095, "sex": "Male"},
    {"title": "แอบแซ่บ", "url": "https://t.me/+g3rdpd5JyAI4MTM1", "chat_id": -1004430170651, "sex": "Male"},
    {"title": "ห้องลับสาวแซ่บ", "url": "https://t.me/+rcFCXvIO8KliZGI9", "chat_id": -1004348635742, "sex": "Male"},
    {"title": "นางฟ้าพาติดเรท", "url": "https://t.me/+D2zAGjCW7Cc0ZGFl", "chat_id": -1004392387293, "sex": "Male"},
    {"title": "JAVxTH", "url": "https://t.me/+ESNH7vOyokk4YWQ1", "chat_id": -1003458434322, "sex": "Male"},
    {"title": "ดูซีรีส์กัน", "url": "https://t.me/+I1q23AUv24dhMWM1", "chat_id": -1004459446053, "sex": "Male"},
    {"title": "ละครแนวตั้ง", "url": "https://t.me/+Nu-MChv-hlpiMGZl", "chat_id": -1004313946578, "sex": "Male"},
    {"title": "ซีรีส์ฟิน", "url": "https://t.me/+HgRm1A1SyuVhNDY1", "chat_id": -1004298273520, "sex": "Male"},
    {"title": "ติดละครสั้น", "url": "https://t.me/+8lkVLMI9ELM2Y2Zl", "chat_id": -1004406516433, "sex": "Male"},
    {"title": "Doball24HR", "url": "https://t.me/+rHrHLeTRtk43MTA1", "chat_id": -1003949103951, "sex": "Male"},
    {"title": "ดูบอลสด4K", "url": "https://t.me/+M8q8ZMrKmeY5YWY1", "chat_id": -1004308599662, "sex": "Male"},
]

WELCOME_ENABLED         = parse_bool_env("WELCOME_ENABLED", True)
WELCOME_BUTTON_COUNT    = max(1, parse_int_env("WELCOME_BUTTON_COUNT", 6))
WELCOME_BUTTONS_PER_ROW = max(1, parse_int_env("WELCOME_BUTTONS_PER_ROW", 2))
WELCOME_BATCH_SECONDS   = max(1, parse_int_env("WELCOME_BATCH_SECONDS", 60))
WELCOME_SEND_TIMEOUT    = max(1, parse_int_env("WELCOME_SEND_TIMEOUT", 5))
WELCOME_MAX_CONCURRENT  = 1
WELCOME_IDLE_WAIT       = max(1, parse_int_env("WELCOME_IDLE_WAIT", 10))
WELCOME_SEND_INTERVAL   = max(0, parse_int_env("WELCOME_SEND_INTERVAL_MS", 1000)) / 1000
WELCOME_TEXT            = os.getenv("WELCOME_TEXT", DEFAULT_WELCOME_TEXT).replace("\\n", "\n")
WELCOME_GROUP_LINKS     = parse_welcome_group_links(os.getenv("WELCOME_GROUP_LINKS")) or DEFAULT_WELCOME_GROUP_LINKS
WELCOME_IMAGES          = parse_welcome_images(os.getenv("WELCOME_IMAGE_URLS") or os.getenv("WELCOME_IMAGE_URL")) or DEFAULT_WELCOME_IMAGES

CRITICAL_API_RETRIES    = max(0, parse_int_env("CRITICAL_API_RETRIES", 2))
CRITICAL_RETRY_MAX_WAIT = max(1, parse_int_env("CRITICAL_RETRY_MAX_WAIT", 30))

# =============================
# CONCURRENCY CONTROL
# =============================
# [STABILITY #1] จำกัด coroutine พร้อมกันสูงสุด 30 ตัว
# กันไม่ให้ spam 1000 ข้อความ/วินาที สร้าง coroutine ไม่จำกัด → OOM crash

MAX_CONCURRENT = 30
_semaphore: asyncio.Semaphore | None = None  # สร้างใน event loop จริง (ดูด้านล่าง)
_active_moderation = 0
_moderation_idle_event: asyncio.Event | None = None

# Alert dedup — ถ้า user ถูก alert ไปแล้วใน 60 วินาที → ไม่ alert ซ้ำ
# กัน bot ส่ง alert ท่วม group เวลาโดน spam หนัก
ALERT_COOLDOWN      = 60    # วินาที
user_last_alert: dict[tuple[int, int], float] = {}

# เมื่อตรวจเจอสแปมเมอร์ ให้ ban พร้อมสั่ง Telegram ลบประวัติข้อความของ user คนนั้น
# ถ้า Telegram ไม่อนุญาตหรือ bot สิทธิ์ไม่พอ จะ fallback เป็น mute ถาวรแบบเดิม
PURGE_SPAMMER_HISTORY_ON_DETECT = True

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
        "drop_pending_updates": DROP_PENDING_UPDATES,
        "processed_updates_count": processed_updates_count,
        "last_update_at": last_update_at,
        "last_error_at": last_error_at,
        "cache_sizes": {
            "join_times": len(join_times),
            "user_messages": len(user_messages),
            "pending_welcome_chats": len(pending_welcome_members),
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
ALERT_STATE_TTL = max(ALERT_COOLDOWN * 10, 600)
SPAM_WINDOW = 10
BACKLOG_MESSAGE_AGE = 30

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

def retry_after_seconds(error: RetryAfter) -> float:
    value = getattr(error, "retry_after", 1)
    if hasattr(value, "total_seconds"):
        value = value.total_seconds()
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return 1.0

async def run_critical_action(label: str, operation, retries: int | None = None):
    """Run a destructive moderation API call with bounded transient retries."""
    max_retries = CRITICAL_API_RETRIES if retries is None else max(0, retries)

    for attempt in range(max_retries + 1):
        try:
            return True, await operation()
        except RetryAfter as e:
            if attempt >= max_retries:
                logger.error(f"{label} failed after rate-limit retries: {e}")
                break

            delay = retry_after_seconds(e)
            if delay > CRITICAL_RETRY_MAX_WAIT:
                logger.error(
                    f"{label} requires retry after {delay:.1f}s; "
                    f"exceeds safety limit {CRITICAL_RETRY_MAX_WAIT}s"
                )
                break

            logger.warning(f"{label} rate limited; retrying in {delay:.1f}s")
            await asyncio.sleep(delay + 0.1)
        except BadRequest as e:
            # In PTB, BadRequest is a subclass of NetworkError.
            # We must catch BadRequest before NetworkError so Telegram 400 errors
            # (like message already deleted) are not mistaken for network glitches and retried.
            err_text = str(e).lower()
            if "message to delete not found" in err_text or "message can't be deleted" in err_text:
                logger.info(f"{label}: message already deleted or not found: {e}")
                return True, None
            logger.warning(f"{label} bad request (will not retry): {e}")
            break
        except (TimedOut, NetworkError) as e:
            if attempt >= max_retries:
                logger.error(f"{label} failed after network retries: {e}")
                break
            delay = min(0.5 * (2 ** attempt), 2.0)
            logger.warning(f"{label} network error; retrying in {delay:.1f}s: {e}")
            await asyncio.sleep(delay)
        except Forbidden as e:
            logger.error(f"{label} forbidden; check bot admin permissions: {e}")
            break
        except Exception as e:
            logger.error(f"{label} failed: {e}")
            break

    return False, None

async def delete_message_critical(
    message,
    label: str,
    retries: int | None = None,
) -> bool:
    ok, _ = await run_critical_action(label, message.delete, retries=retries)
    return ok

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
    log_key = (chat_id, user_id)
    log = list(user_message_log.get(log_key, []))

    ids: set[int] = set(extra_ids or [])     # ข้อความที่ admin reply → รวมไว้เสมอ
    timestamps: dict[int, float] = {mid: timestamp for mid, timestamp in log}
    for mid, t in log:
        if now - t < MESSAGE_DELETABLE_AGE:  # เกิน 48 ชม. ลบไม่ได้ ข้ามไป
            ids.add(mid)

    if not ids:
        user_message_log.pop(log_key, None)
        return 0

    ids_list = sorted(ids)
    deleted  = 0
    failed_ids: set[int] = set()

    bulk_delete_fn = getattr(context.bot, "delete_messages", None)
    has_bulk = callable(bulk_delete_fn)

    # ลบทีละชุด 100 ตัว (delete_messages = bulk API ของ Telegram ถ้ามีในไลบรารี)
    for i in range(0, len(ids_list), 100):
        chunk = ids_list[i:i + 100]
        if has_bulk:
            ok, _ = await run_critical_action(
                f"bulk delete chat={chat_id} user={user_id}",
                lambda chunk=chunk: bulk_delete_fn(
                    chat_id=chat_id,
                    message_ids=chunk,
                ),
            )
            if ok:
                deleted += len(chunk)
                continue
            logger.warning(f"bulk delete failed in chat {chat_id} — fallback per-message")

        # fallback: ลบทีละข้อความ (สำหรับ PTB เก่า หรือกรณี bulk delete ไม่สำเร็จ)
        for mid in chunk:
            single_ok, _ = await run_critical_action(
                f"delete message={mid} chat={chat_id}",
                lambda mid=mid: context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=mid,
                ),
                retries=1,
            )
            if single_ok:
                deleted += 1
            else:
                failed_ids.add(mid)

    # Keep failed IDs for a later /purge instead of losing them before the API
    # confirms deletion. Entries without an original timestamp get "now".
    if failed_ids:
        user_message_log[log_key] = [
            (mid, timestamps.get(mid, now))
            for mid in sorted(failed_ids)
        ][-MESSAGE_LOG_MAX_PER_USER:]
    else:
        user_message_log.pop(log_key, None)

    logger.info(f"purge_user_messages: deleted {deleted}/{len(ids_list)} msgs "
                f"for user {user_id} in chat {chat_id}")
    return deleted

# =============================
# ADMIN CACHE
# =============================

admin_cache     : dict[tuple, tuple[bool, float]] = {}
ADMIN_CACHE_TTL = 300  # 5 นาที

async def is_admin_cached(context, chat_id: int, user_id: int) -> bool | None:
    """Return True/False when verified, or None when Telegram cannot verify."""
    now = time.time()
    key = (chat_id, user_id)
    if key in admin_cache:
        result, expire = admin_cache[key]
        if now < expire:
            return result

    ok, member = await run_critical_action(
        f"get_chat_member chat={chat_id} user={user_id}",
        lambda: context.bot.get_chat_member(chat_id, user_id),
        retries=1,
    )
    if not ok or member is None:
        # Never cache a network/permission failure as "not admin". Doing so can
        # cause a real admin to be moderated for five minutes.
        return None

    result = member.status in ("administrator", "creator")
    admin_cache[key] = (result, time.time() + ADMIN_CACHE_TTL)
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
    key = user_key(chat_id, user_id)
    if key in user_muted_permanent:
        return
    user_muted_permanent.add(key)
    save_muted()

# =============================
# WELCOME MESSAGE
# =============================

last_welcome_messages: dict[int, int] = {}
stale_welcome_messages: dict[int, set[int]] = defaultdict(set)
pending_welcome_members: dict[int, dict[int, object]] = defaultdict(dict)
pending_welcome_threads: dict[int, int | None] = {}
welcome_tasks: dict[int, asyncio.Task] = {}
_welcome_semaphore: asyncio.Semaphore | None = None
_welcome_rate_lock: asyncio.Lock | None = None
_last_welcome_send_at = 0.0

def welcome_member_names(members) -> str:
    labels: list[str] = []
    for member in members[:3]:
        try:
            labels.append(member.mention_html())
        except Exception:
            name = getattr(member, "full_name", None) or getattr(member, "first_name", None) or "สมาชิกใหม่"
            labels.append(html.escape(name))

    if len(members) > 3:
        labels.append(f"และอีก {len(members) - 3} คน")

    return ", ".join(labels) if labels else "สมาชิกใหม่"

def build_welcome_text(members) -> str:
    template = WELCOME_TEXT or DEFAULT_WELCOME_TEXT
    safe_template = html.escape(template)
    names_str = welcome_member_names(members)
    count_str = str(len(members)) if members else "1"
    return safe_template.replace("{name}", names_str).replace("{count}", count_str)

WELCOME_FEMALE_ICONS = ["🌸", "💋", "🍑", "💖", "🎀", "🍓", "💃", "🍒"]
WELCOME_MALE_ICONS   = ["⚡", "🔥", "👑", "🚀", "🎯", "💥", "💪", "🥊"]

def build_welcome_keyboard(chat_id: int) -> InlineKeyboardMarkup | None:
    if not WELCOME_GROUP_LINKS:
        return None

    female_pool: list[dict] = []
    male_pool: list[dict] = []
    other_pool: list[dict] = []

    for item in WELCOME_GROUP_LINKS:
        item_chat_id = item.get("chat_id")
        if item_chat_id is not None and item_chat_id == chat_id:
            continue

        sex = str(item.get("sex") or "").lower()
        if sex == "female":
            female_pool.append(item)
        elif sex == "male":
            male_pool.append(item)
        else:
            other_pool.append(item)

    if not female_pool and not male_pool and not other_pool:
        return None

    random.shuffle(female_pool)
    random.shuffle(male_pool)
    random.shuffle(other_pool)

    f_icons = list(WELCOME_FEMALE_ICONS)
    m_icons = list(WELCOME_MALE_ICONS)
    random.shuffle(f_icons)
    random.shuffle(m_icons)

    # 2 ปุ่มต่อแถว: ฝั่งซ้ายกลุ่มหญิง | ฝั่งขวากลุ่มชาย (อิโมจิไม่ซ้ำกันทั้ง 6 ปุ่ม)
    num_rows = max(1, WELCOME_BUTTON_COUNT // 2)
    rows: list[list[InlineKeyboardButton]] = []

    for _ in range(num_rows):
        row: list[InlineKeyboardButton] = []

        # ซ้าย: กลุ่มหญิง
        f_item = female_pool.pop() if female_pool else (other_pool.pop() if other_pool else None)
        if f_item:
            title = f_item.get("title", "กลุ่มหญิง")
            icon = f_icons.pop() if f_icons else "🌸"
            btn_title = f"{icon} {title}"
            row.append(InlineKeyboardButton(btn_title, url=f_item["url"]))

        # ขวา: กลุ่มชาย
        m_item = male_pool.pop() if male_pool else (other_pool.pop() if other_pool else None)
        if m_item:
            title = m_item.get("title", "กลุ่มชาย")
            icon = m_icons.pop() if m_icons else "⚡"
            btn_title = f"{icon} {title}"
            row.append(InlineKeyboardButton(btn_title, url=m_item["url"]))

        if row:
            rows.append(row)

    return InlineKeyboardMarkup(rows) if rows else None

async def wait_for_moderation_idle(timeout: float) -> bool:
    if _active_moderation == 0:
        return True
    if _moderation_idle_event is None:
        return False

    try:
        await asyncio.wait_for(_moderation_idle_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    return _active_moderation == 0

async def wait_for_welcome_send_slot() -> None:
    global _welcome_rate_lock, _last_welcome_send_at
    if _welcome_rate_lock is None:
        _welcome_rate_lock = asyncio.Lock()

    async with _welcome_rate_lock:
        delay = WELCOME_SEND_INTERVAL - (time.monotonic() - _last_welcome_send_at)
        if delay > 0:
            await asyncio.sleep(delay)
        _last_welcome_send_at = time.monotonic()

async def wait_for_welcome_batch(application) -> bool:
    """Wait for the batch window, but leave promptly when the app is stopping."""
    deadline = time.monotonic() + WELCOME_BATCH_SECONDS
    while True:
        if not getattr(application, "running", False):
            return False

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True

        await asyncio.sleep(min(1.0, remaining))

_welcome_image_idx = 0
_welcome_image_lock: asyncio.Lock | None = None

async def get_next_welcome_image() -> str | None:
    global _welcome_image_idx, _welcome_image_lock
    if not WELCOME_IMAGES:
        return None
    if _welcome_image_lock is None:
        _welcome_image_lock = asyncio.Lock()
    async with _welcome_image_lock:
        img = WELCOME_IMAGES[_welcome_image_idx % len(WELCOME_IMAGES)]
        _welcome_image_idx = (_welcome_image_idx + 1) % len(WELCOME_IMAGES)
        return img

async def send_welcome_message(bot, chat_id: int, thread_id: int | None, members) -> bool:
    photo_url = await get_next_welcome_image()
    welcome_text = build_welcome_text(members)
    reply_markup = build_welcome_keyboard(chat_id)

    sent_message = None

    # ส่งแบบรูปภาพพร้อมแคปชันและปุ่ม ถ้ามีรูปภาพตั้งค่าไว้
    if photo_url:
        send_kwargs = {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": welcome_text,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        }
        if thread_id:
            send_kwargs["message_thread_id"] = thread_id

        try:
            sent_message = await asyncio.wait_for(
                bot.send_photo(**send_kwargs),
                timeout=WELCOME_SEND_TIMEOUT,
            )
        except Exception as e:
            logger.warning(
                f"send_photo failed in chat {chat_id} ({e}); falling back to send_message"
            )

    # ส่งแบบข้อความธรรมดา (กรณีไม่มีรูปภาพ หรือส่งรูปภาพไม่สำเร็จ)
    if sent_message is None:
        send_kwargs = {
            "chat_id": chat_id,
            "text": welcome_text,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
            "disable_web_page_preview": True,
        }
        if thread_id:
            send_kwargs["message_thread_id"] = thread_id

        try:
            sent_message = await asyncio.wait_for(
                bot.send_message(**send_kwargs),
                timeout=WELCOME_SEND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Skipped welcome in chat {chat_id}: send exceeded "
                f"{WELCOME_SEND_TIMEOUT}s"
            )
            return False
        except Exception as e:
            logger.warning(f"Failed to send welcome in chat {chat_id}: {e}")
            return False

    old_message_ids = set(stale_welcome_messages.pop(chat_id, set()))
    old_message_id = last_welcome_messages.get(chat_id)
    if old_message_id:
        old_message_ids.add(old_message_id)
    last_welcome_messages[chat_id] = sent_message.message_id

    failed_deletes: set[int] = set()
    remaining_old_ids = list(old_message_ids)
    for index, old_message_id in enumerate(remaining_old_ids):
        if old_message_id == sent_message.message_id:
            continue
        if _active_moderation > 0:
            failed_deletes.update(remaining_old_ids[index:])
            break
        try:
            await asyncio.wait_for(
                bot.delete_message(chat_id=chat_id, message_id=old_message_id),
                timeout=WELCOME_SEND_TIMEOUT,
            )
        except (TimedOut, NetworkError, RetryAfter, asyncio.TimeoutError) as e:
            failed_deletes.add(old_message_id)
            logger.info(f"Could not delete old welcome message {old_message_id} in chat {chat_id}: {e}")
        except (BadRequest, Forbidden) as e:
            # Permanent failure (already deleted or no permission): do not retry
            # forever on every future welcome.
            logger.info(f"Discarded stale welcome message {old_message_id} in chat {chat_id}: {e}")
        except Exception as e:
            failed_deletes.add(old_message_id)
            logger.info(f"Could not delete old welcome message {old_message_id} in chat {chat_id}: {e}")

    if failed_deletes:
        stale_welcome_messages[chat_id].update(sorted(failed_deletes)[-10:])

    return True

async def run_welcome_queue(application, bot, chat_id: int) -> None:
    global _welcome_semaphore

    try:
        while pending_welcome_members.get(chat_id):
            logger.info(
                f"Welcome queued for chat {chat_id}; "
                f"collecting members for {WELCOME_BATCH_SECONDS}s"
            )
            if not await wait_for_welcome_batch(application):
                return

            members_by_id = pending_welcome_members.pop(chat_id, {})
            thread_id = pending_welcome_threads.pop(chat_id, None)

            if not members_by_id:
                continue

            # Welcome is optional and always waits behind active moderation.
            # Keep the collected batch in this worker rather than losing it if
            # the group happens to be busy at the end of the 60-second window.
            while getattr(application, "running", False):
                if not await wait_for_moderation_idle(WELCOME_IDLE_WAIT):
                    continue

                if _welcome_semaphore is None:
                    _welcome_semaphore = asyncio.Semaphore(WELCOME_MAX_CONCURRENT)

                async with _welcome_semaphore:
                    if _active_moderation > 0:
                        continue

                    await wait_for_welcome_send_slot()
                    if _active_moderation > 0:
                        continue

                    await send_welcome_message(
                        bot,
                        chat_id,
                        thread_id,
                        list(members_by_id.values()),
                    )
                    break
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Welcome queue failed in chat {chat_id}: {e}")
    finally:
        current_task = asyncio.current_task()
        if welcome_tasks.get(chat_id) is current_task:
            welcome_tasks.pop(chat_id, None)

        if (
            pending_welcome_members.get(chat_id)
            and chat_id not in welcome_tasks
            and getattr(application, "running", False)
        ):
            welcome_tasks[chat_id] = application.create_task(
                run_welcome_queue(application, bot, chat_id),
                name=f"welcome-{chat_id}",
            )

def queue_welcome_message(message, chat, members, context) -> None:
    if not WELCOME_ENABLED or not chat:
        return

    chat_id = chat.id
    human_members = [member for member in members if not getattr(member, "is_bot", False)]
    if not human_members:
        return

    for member in human_members:
        pending_welcome_members[chat_id][member.id] = member

    thread_id = getattr(message, "message_thread_id", None) if message else None
    if thread_id:
        pending_welcome_threads[chat_id] = thread_id

    task = welcome_tasks.get(chat_id)
    if task and not task.done():
        return

    welcome_tasks[chat_id] = context.application.create_task(
        run_welcome_queue(context.application, context.bot, chat_id),
        name=f"welcome-{chat_id}",
    )

# =============================
# ALERT SYSTEM (พร้อมรองรับ Forum Topic)
# =============================

async def alert_action(
    context,
    chat_id: int,
    user,
    reason: str,
    action: str,
    thread_id: int | None = None,
):
    """
    ส่ง alert แต่ข้าม user ที่เพิ่งถูก alert ไปใน 60 วินาที
    กันบอทส่ง alert ท่วม group เวลาโดน spam หนัก
    พร้อมรองรับ Forum Topic (thread_id) และมี fallback ส่งเข้าห้องหลัก
    """
    if user:
        now  = time.time()
        key  = user_key(chat_id, user.id)
        last = user_last_alert.get(key, 0)
        if now - last < ALERT_COOLDOWN:
            return  # ไม่ alert ซ้ำ
        user_last_alert[key] = now

    name = user.mention_html() if user else "Unknown"
    text = (
        f"🚫 GUARD BOT\n\n"
        f"ตรวจพบการกระทำต้องสงสัย\n\n"
        f"ผู้ใช้: {name}\n"
        f"เหตุผล: {reason}\n\n"
        f"การดำเนินการ: {action}\n\n"
        f"ระบบป้องกันกลุ่มทำงานอัตโนมัติ"
    )

    send_kwargs = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if thread_id:
        send_kwargs["message_thread_id"] = thread_id

    try:
        await context.bot.send_message(**send_kwargs)
    except Exception as e:
        # Fallback: หากส่งใน topic ไม่ผ่าน (เช่น topic ถูกปิด/ลบ) ให้ลองส่งเข้าห้องหลัก
        if thread_id:
            send_kwargs.pop("message_thread_id", None)
            try:
                await context.bot.send_message(**send_kwargs)
            except Exception as e2:
                logger.warning(f"Failed fallback alert in chat {chat_id}: {e2}")
        else:
            logger.warning(f"Failed to send alert in chat {chat_id}: {e}")

# =============================
# SETTINGS & PATTERNS
# =============================

SUSPICIOUS_WORDS_TH = [
    "ดูฟรี", "กดดู", "ลิงก์นี้", "คลิกที่นี่", "เข้าดู", "ดูต่อ",
    "แจกฟรี", "เครดิตฟรี", "full clip", "full video", "ver video",
    "มปลาย", "มต้น", "เด็กนักเรียน", "เด็กมัธยม", "บริสุทธิ์", "ใสๆ",
    "สล็อต", "บาคาร่า", "แทงบอล", "ต้องฝาก",
    "ได้เงินจริง", "ถอนเงิน", "เครดิตฟรีไม่ต้องฝาก", "โปรแรง", "โปรแจก",
    "แอดไลน์", "แอดไลน์มา", "ขาย", "รับงาน", "รับทำ",
    "ไซด์ไลน์", "งานเอ็น", "เด็กเอ็น", "งานนอก",
    "เด็ก", "เดก", "แลก",
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

# ดักจับลิงก์แบบครอบคลุม: ทั้ง web url, protocol tg://, startapp, startgroup, shorteners, และโดเมนยอดนิยม
LINK_PATTERNS = [
    r"http[s]?://",
    r"hxxp[s]?://",
    r"www\.",
    r"tg://",
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
# UNIVERSAL CONTENT EXTRACTOR (รองรับ Checklist, Poll, Contact, Venue, Invoice, Game, Quote)
# =============================

def extract_all_message_text(message) -> str:
    """
    ดึงข้อความทั้งหมดอย่างครบวงจร:
    1) ข้อความปกติ (message.text)
    2) แคปชั่นรูปภาพ/วิดีโอ (message.caption)
    3) Telegram Checklist (title และ tasks ทั้งหมด)
    4) Telegram Poll (question และ options ทั้งหมด)
    5) Contact Cards (first_name, last_name, phone_number, vcard)
    6) Venue & Location (title, address)
    7) Invoice & Payments (title, description)
    8) HTML5 Game (title, description)
    9) Message Quotes (message.quote.text)
    10) Web app data
    รองรับทั้ง Python Telegram Bot Object และดิบ JSON จาก api_kwargs
    """
    parts: list[str] = []

    # 1. ข้อความทั่วไป และ Caption
    if getattr(message, "text", None):
        parts.append(message.text)
    if getattr(message, "caption", None):
        parts.append(message.caption)

    # 2. Telegram Checklist (Bot API 9.1+)
    checklist = getattr(message, "checklist", None)
    if not checklist and hasattr(message, "api_kwargs") and message.api_kwargs:
        checklist = message.api_kwargs.get("checklist")

    if checklist:
        if isinstance(checklist, dict):
            title = checklist.get("title")
            if title:
                parts.append(str(title))
            for task in checklist.get("tasks", []):
                if isinstance(task, dict):
                    t_text = task.get("text")
                    if t_text:
                        parts.append(str(t_text))
                elif hasattr(task, "text") and task.text:
                    parts.append(str(task.text))
        else:
            title = getattr(checklist, "title", None)
            if title:
                parts.append(str(title))
            tasks = getattr(checklist, "tasks", None) or []
            for task in tasks:
                t_text = (
                    getattr(task, "text", None)
                    if hasattr(task, "text")
                    else task.get("text")
                    if isinstance(task, dict)
                    else None
                )
                if t_text:
                    parts.append(str(t_text))

    # 3. Telegram Poll
    poll = getattr(message, "poll", None)
    if not poll and hasattr(message, "api_kwargs") and message.api_kwargs:
        poll = message.api_kwargs.get("poll")

    if poll:
        if isinstance(poll, dict):
            q = poll.get("question")
            if q:
                parts.append(str(q))
            for opt in poll.get("options", []):
                if isinstance(opt, dict):
                    o_text = opt.get("text")
                    if o_text:
                        parts.append(str(o_text))
                elif hasattr(opt, "text") and opt.text:
                    parts.append(str(opt.text))
        else:
            q = getattr(poll, "question", None)
            if q:
                parts.append(str(q))
            options = getattr(poll, "options", None) or []
            for opt in options:
                o_text = (
                    getattr(opt, "text", None)
                    if hasattr(opt, "text")
                    else opt.get("text")
                    if isinstance(opt, dict)
                    else None
                )
                if o_text:
                    parts.append(str(o_text))

    # 4. Contact Cards (สกัดชื่อ นามสกุล เบอร์โทรศัพท์ และ vcard)
    contact = getattr(message, "contact", None)
    if contact:
        for attr in ("first_name", "last_name", "phone_number", "vcard"):
            val = getattr(contact, attr, None)
            if val:
                parts.append(str(val))

    # 5. Venue (สกัดชื่อสถานที่และที่อยู่)
    venue = getattr(message, "venue", None)
    if venue:
        for attr in ("title", "address"):
            val = getattr(venue, attr, None)
            if val:
                parts.append(str(val))

    # 6. Invoice (สกัดชื่อและรายละเอียดใบแจ้งหนี้)
    invoice = getattr(message, "invoice", None)
    if invoice:
        for attr in ("title", "description"):
            val = getattr(invoice, attr, None)
            if val:
                parts.append(str(val))

    # 7. Game (สกัดชื่อและคำอธิบายเกม)
    game = getattr(message, "game", None)
    if game:
        for attr in ("title", "description"):
            val = getattr(game, attr, None)
            if val:
                parts.append(str(val))

    # 8. Quote (สกัดข้อความในก้อน Quote Reply)
    quote = getattr(message, "quote", None)
    if quote:
        q_text = getattr(quote, "text", None)
        if q_text:
            parts.append(str(q_text))

    # 9. Web App Data
    web_app_data = getattr(message, "web_app_data", None)
    if web_app_data:
        data_text = getattr(web_app_data, "data", None)
        if data_text:
            parts.append(str(data_text))

    return " ".join(parts).strip()


def extract_all_entities(message) -> list:
    """ดึง Message Entities ทั้งหมด ทั้งจาก text, caption, checklist tasks, poll และ quote"""
    entities = list(getattr(message, "entities", None) or [])
    entities += list(getattr(message, "caption_entities", None) or [])

    # Checklist Entities
    checklist = getattr(message, "checklist", None)
    if not checklist and hasattr(message, "api_kwargs") and message.api_kwargs:
        checklist = message.api_kwargs.get("checklist")

    if checklist:
        if isinstance(checklist, dict):
            entities.extend(checklist.get("title_entities", []) or [])
            for task in checklist.get("tasks", []):
                if isinstance(task, dict):
                    entities.extend(task.get("text_entities", []) or [])
        else:
            entities.extend(getattr(checklist, "title_entities", None) or [])
            for task in getattr(checklist, "tasks", None) or []:
                if hasattr(task, "text_entities") and task.text_entities:
                    entities.extend(task.text_entities)
                elif isinstance(task, dict):
                    entities.extend(task.get("text_entities", []) or [])

    # Poll Entities
    poll = getattr(message, "poll", None)
    if not poll and hasattr(message, "api_kwargs") and message.api_kwargs:
        poll = message.api_kwargs.get("poll")

    if poll:
        if isinstance(poll, dict):
            entities.extend(poll.get("question_entities", []) or [])
            entities.extend(poll.get("explanation_entities", []) or [])
        else:
            entities.extend(getattr(poll, "question_entities", None) or [])
            entities.extend(getattr(poll, "explanation_entities", None) or [])

    # Quote Entities
    quote = getattr(message, "quote", None)
    if quote:
        entities.extend(getattr(quote, "entities", None) or [])

    return entities


def is_checklist_message(message) -> bool:
    """ตรวจสอบว่าเป็นข้อความประเภท Checklist หรือไม่"""
    if getattr(message, "checklist", None):
        return True
    if hasattr(message, "api_kwargs") and message.api_kwargs and "checklist" in message.api_kwargs:
        return True
    return False


def is_poll_message(message) -> bool:
    """ตรวจสอบว่าเป็นข้อความประเภท Poll หรือไม่"""
    if getattr(message, "poll", None):
        return True
    if hasattr(message, "api_kwargs") and message.api_kwargs and "poll" in message.api_kwargs:
        return True
    return False

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
        r"(https?://[^\s]+|www\.[^\s]+|tg://[^\s]+|t\.me/[^\s]+|telegram\.me/[^\s]+)",
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
    """
    ตรวจจับลิงก์แบบครบวงจร:
    1) สแกนจากข้อความทั้งหมด (รวม Checklist, Caption, Poll, Contact, Venue)
    2) สแกนจาก Entities (url, text_link, mention, text_mention, email)
    3) สแกนจากปุ่มคีย์บอร์ดใต้โพสต์ (inline_keyboard: url, login_url, web_app)
    """
    text = extract_all_message_text(message)
    if contains_link(text):
        return True

    entities = extract_all_entities(message)
    for entity in entities:
        if isinstance(entity, dict):
            e_type = str(entity.get("type", "")).lower()
            e_url = entity.get("url")
        else:
            e_type = entity_type_name(entity)
            e_url = getattr(entity, "url", None)

        if e_type in LINK_ENTITY_TYPES:
            return True
        if e_url:
            return True

    reply_markup = getattr(message, "reply_markup", None)
    for row in getattr(reply_markup, "inline_keyboard", None) or []:
        for button in row:
            if getattr(button, "url", None):
                return True
            if getattr(button, "login_url", None):
                return True
            if getattr(button, "web_app", None):
                return True

    return False

def contains_bad_word(text: str) -> bool:
    text = normalize_text(text)
    for word in SUSPICIOUS_WORDS_TH:
        if word in text:
            return True
    for word in SUSPICIOUS_WORDS_EN:
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])",
            text,
        ):
            return True
    return False

def contains_disallowed_language(text: str) -> bool:
    """อนุญาตไทย อังกฤษ เลขสากล 0-9 และเลขไทย ๐-๙ บล็อกภาษาและตัวเลขต่างด้าวทั้งหมด"""
    text = unicodedata.normalize("NFC", text or "")

    for ch in text:
        category = unicodedata.category(ch)
        code = ord(ch)

        if category.startswith("L"):
            # อนุญาตตัวอักษรไทยทั้งหมดใน Unicode Thai block
            if 0x0E00 <= code <= 0x0E7F:
                continue

            # อนุญาตเฉพาะตัวอักษรอังกฤษ A-Z และ a-z
            if "A" <= ch <= "Z" or "a" <= ch <= "z":
                continue

            return True

        if category.startswith("N"):
            # อนุญาตเลขสากล 0-9 และเลขไทย ๐-๙
            if "0" <= ch <= "9" or 0x0E50 <= code <= 0x0E59:
                continue

            # บล็อกเลขระบบอื่น เช่น ١٢٣ และ ۱۲۳
            return True

        if category.startswith("M"):
            # อนุญาตสระ/วรรณยุกต์ไทย และตัวประกอบอีโมจิเท่านั้น
            if 0x0E00 <= code <= 0x0E7F:
                continue
            if 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF:
                continue
            if code == 0x20E3:  # combining enclosing keycap
                continue
            return True

        # อีโมจิ ช่องว่าง และเครื่องหมายให้ผ่านไปตรวจด้วยระบบเดิม
        if not category.startswith(("L", "N")):
            continue

    return False

def count_custom_emojis(message) -> int:
    """นับจำนวน Custom Emojis ในข้อความ (ป้องกันสแปมเมอร์ใช้แผงภาพพรีวิวคลิปโป๊)"""
    count = 0
    for entity in extract_all_entities(message):
        e_type = str(entity.get("type", "") if isinstance(entity, dict) else getattr(entity, "type", "")).lower()
        if "custom_emoji" in e_type:
            count += 1
    return count

def contains_suspicious_emoji(message, text: str) -> bool:
    """ตรวจจับอีโมจิต้องสงสัย ทั้ง Unicode Emojis ปกติ และ Custom Emoji Packs"""
    standard_count = sum(text.count(emoji) for emoji in SUSPICIOUS_EMOJIS)
    custom_count = count_custom_emojis(message)
    return (standard_count + custom_count) >= EMOJI_THRESHOLD

def is_spam(chat_id: int, user_id: int) -> bool:
    now = time.time()
    key = user_key(chat_id, user_id)
    user_messages[key] = [t for t in user_messages[key] if now - t < SPAM_WINDOW]
    user_messages[key].append(now)
    return len(user_messages[key]) >= 5

def is_backlog_message(message) -> bool:
    """Identify old queued updates without skipping their content checks."""
    message_date = getattr(message, "date", None)
    if not message_date:
        return False
    try:
        return time.time() - message_date.timestamp() > BACKLOG_MESSAGE_AGE
    except (AttributeError, TypeError, ValueError, OSError):
        return False

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
    text = extract_all_message_text(message)
    return bool(re.search(r"(unique collectible|gift from|pepe nft|sending as a gift)", text.lower()))

def forbidden_message_reason(message, text: str) -> str | None:
    """
    ตรวจสอบสาเหตุข้อความต้องห้ามอย่างครอบคลุมครบ 20 รูปแบบ:
    Forward, Story, Gift, Checklist, Poll, Contact, Venue, Invoice, Game, Paid Media, Inline Bot, Link, Bad Word, ภาษา และ Emoji
    """
    if is_any_forward(message):
        return "Forward ข้อความ (ทุกรูปแบบ)"
    if is_story_share(message):
        return "แชร์ Story"
    if is_gift_message(message):
        return "ส่ง Gift / NFT / Collectible"

    # 1. ตรวจจับ Checklist
    if is_checklist_message(message):
        if message_has_link(message):
            return "ส่ง Checklist ที่มีลิงก์ (ห้ามส่งลิงก์ทุกชนิด)"
        if contains_bad_word(text):
            return "ส่ง Checklist ที่มีคำต้องห้าม"
        if contains_disallowed_language(text):
            return "ส่ง Checklist ที่มีตัวอักษร/ตัวเลขไม่อนุญาต"
        if contains_suspicious_emoji(message, text):
            return f"ส่ง Checklist ที่มี Emoji ต้องสงสัย >= {EMOJI_THRESHOLD} ตัว"
        if BLOCK_ALL_CHECKLISTS:
            return "ส่ง Checklist (ไม่อนุญาตให้ใช้ฟีเจอร์นี้ในกลุ่ม)"

    # 2. ตรวจจับ Poll
    if is_poll_message(message):
        if message_has_link(message):
            return "ส่ง Poll ที่มีลิงก์ (ห้ามส่งลิงก์ทุกชนิด)"
        if contains_bad_word(text):
            return "ส่ง Poll ที่มีคำต้องห้าม"
        if BLOCK_ALL_POLLS:
            return "ส่ง Poll (ไม่อนุญาตให้ใช้โพลในกลุ่ม)"

    # 3. ตรวจจับ Contact Cards
    if getattr(message, "contact", None):
        if message_has_link(message):
            return "ส่ง Contact Card ที่มีลิงก์ (ห้ามส่งลิงก์ทุกชนิด)"
        if contains_bad_word(text):
            return "ส่ง Contact Card ที่มีคำต้องห้าม"
        if BLOCK_ALL_CONTACTS:
            return "ส่ง Contact Card (ไม่อนุญาตในกลุ่ม)"

    # 4. ตรวจจับ Venue / Location
    if getattr(message, "venue", None):
        if message_has_link(message):
            return "ส่งสถานที่ (Venue) ที่มีลิงก์ (ห้ามส่งลิงก์ทุกชนิด)"
        if contains_bad_word(text):
            return "ส่งสถานที่ (Venue) ที่มีคำต้องห้าม"
        if BLOCK_ALL_VENUES:
            return "ส่งสถานที่ (Venue) ไม่อนุญาตในกลุ่ม"

    # 5. ตรวจจับ Invoices & Payments
    if getattr(message, "invoice", None) and BLOCK_ALL_INVOICES:
        return "ส่ง Invoice / Payment Card (ไม่อนุญาตให้สมาชิกทั่วไปส่ง)"

    # 6. ตรวจจับ Telegram Game
    if getattr(message, "game", None) and BLOCK_ALL_GAMES:
        return "ส่ง Game (ไม่อนุญาตให้สมาชิกทั่วไปส่ง)"

    # 7. ตรวจจับ Paid Media (Telegram Stars)
    if getattr(message, "paid_media", None):
        if message_has_link(message):
            return "ส่ง Paid Media ที่มีลิงก์"
        if contains_bad_word(text):
            return "ส่ง Paid Media ที่มีคำต้องห้าม"
        if BLOCK_ALL_PAID_MEDIA:
            return "ส่ง Paid Media (ไม่อนุญาตในกลุ่ม)"

    # 8. ตรวจจับ Inline Bot (via_bot)
    via_bot = getattr(message, "via_bot", None)
    if via_bot:
        bot_username = (getattr(via_bot, "username", "") or "").lstrip("@").lower()
        if not bot_username or bot_username not in ALLOWED_BOT_USERNAMES:
            if message_has_link(message):
                return f"ส่งข้อความผ่านบอท @{bot_username} ที่มีลิงก์"
            if contains_bad_word(text):
                return f"ส่งข้อความผ่านบอท @{bot_username} ที่มีคำต้องห้าม"
            if BLOCK_ALL_VIA_BOT:
                return f"ส่งข้อความผ่านบอท @{bot_username} (ไม่อนุญาตให้ใช้บอทภายนอก)"

    # 9. กฎพื้นฐาน: Username, Link, Bad Word, ภาษา และ Emoji
    if re.search(r"@\w+", text):
        return "ส่ง @username"
    if message_has_link(message):
        return "ส่งลิงก์ทุกชนิด"
    if contains_bad_word(text):
        return "ใช้คำต้องห้าม"
    if contains_disallowed_language(text):
        return "ใช้ตัวอักษรหรือตัวเลขที่ไม่อนุญาต"
    if contains_suspicious_emoji(message, text):
        return f"ส่ง Emoji ต้องสงสัย >= {EMOJI_THRESHOLD} ตัว"

    return None

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

    chat = update.effective_chat
    if not chat or not update.chat_member or not update.chat_member.new_chat_member:
        return

    chat_id = chat.id
    new_member = update.chat_member.new_chat_member
    user = getattr(new_member, "user", None)
    if not user:
        return
    user_id = user.id

    # Promotion/demotion/member changes invalidate any cached admin result.
    admin_cache.pop((chat_id, user_id), None)

    old_status = getattr(update.chat_member.old_chat_member, "status", None)
    new_status = new_member.status

    if new_status == "member" and old_status in ("left", "kicked", "restricted", None):
        join_times[user_key(chat_id, user_id)] = time.time()
        cleanup_join_times()
        if not getattr(user, "is_bot", False):
            queue_welcome_message(None, chat, [user], context)


async def _handle_new_members_impl(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    thread_id = getattr(message, "message_thread_id", None)

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

        member_admin_status = await is_admin_cached(context, chat.id, member.id)
        if member_admin_status is None:
            logger.warning(
                f"Skipped bot-join enforcement in chat {chat.id}: "
                f"could not verify bot {member.id} admin status"
            )
            continue
        if member_admin_status:
            logger.info(f"Admin bot @{username or member.id} is allowed in chat {chat.id}")
            continue

        inviter_admin_status: bool | None = False
        if inviter and inviter.id not in {member.id, context.bot.id}:
            inviter_admin_status = await is_admin_cached(context, chat.id, inviter.id)
            if inviter_admin_status is None:
                logger.warning(
                    f"Skipped bot-join enforcement in chat {chat.id}: "
                    f"could not verify inviter {inviter.id} admin status"
                )
                continue

        if inviter_admin_status:
            logger.info(
                f"Admin user {inviter.id} added bot @{username or member.id} "
                f"to chat {chat.id}; allowed"
            )
            continue

        bot_banned, _ = await run_critical_action(
            f"ban added bot={member.id} chat={chat.id}",
            lambda member_id=member.id: context.bot.ban_chat_member(
                chat_id=chat.id,
                user_id=member_id,
                revoke_messages=True,
            ),
        )
        inviter_banned = False

        if inviter and inviter.id not in {member.id, context.bot.id}:
            inviter_banned, _ = await run_critical_action(
                f"ban bot inviter={inviter.id} chat={chat.id}",
                lambda inviter_id=inviter.id: context.bot.ban_chat_member(
                    chat_id=chat.id,
                    user_id=inviter_id,
                    revoke_messages=True,
                ),
            )

        await delete_message_critical(
            message,
            f"delete bot-join service message chat={chat.id}",
        )

        action_parts = []
        if bot_banned:
            action_parts.append("แบนบอทแปลกหน้า")
        if inviter_banned:
            action_parts.append("แบนคนที่เพิ่มบอท")
        action_text = " + ".join(action_parts) if action_parts else "พยายามแบนแล้ว แต่สิทธิ์บอทอาจไม่พอ"
        bot_label = f"@{username}" if username else f"ID: {member.id}"

        await alert_action(
            context,
            chat.id,
            None,
            "พบการเพิ่มบอทโดยผู้ใช้ที่ไม่ใช่แอดมิน",
            f"บอท: {bot_label} | การดำเนินการ: {action_text}",
            thread_id=thread_id,
        )

    cleanup_join_times()
    queue_welcome_message(message, chat, new_members, context)


async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Bot-join enforcement is moderation too, so welcome traffic must wait.
    await run_moderation_operation(
        lambda: _handle_new_members_impl(update, context)
    )

# =============================
# MAIN FILTER
# =============================

async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return

    if not await guard_allowed_chat(update, context):
        return
    mark_update_seen()

    chat = update.effective_chat
    if not chat:
        return
    chat_id = chat.id

    command_text = message.text or ""
    if update.message is not None and re.match(
        r"^/purge(?:@\w+)?(?:\s|$)",
        command_text,
        re.IGNORECASE,
    ):
        await purge_command(update, context)
        return

    sender_chat = getattr(message, "sender_chat", None)
    if sender_chat:
        # Messages sent as this group are anonymous-admin messages. External
        # channels are inspected separately so channel spam cannot bypass rules.
        if sender_chat.id == chat_id:
            return
        # Telegram may hide the human admin behind a linked group/channel and
        # expose only sender_chat. Trust only explicitly configured internal
        # groups/channels; every other external sender_chat remains moderated.
        if sender_chat.id in ALLOWED_SENDER_CHAT_IDS:
            logger.info(
                f"Allowed trusted sender_chat {sender_chat.id} in chat {chat_id}"
            )
            return
        await run_moderation_operation(
            lambda: process_sender_chat_message(
                message,
                sender_chat,
                chat_id,
                context,
            )
        )
        return

    user = message.from_user
    if not user:
        return

    # บันทึก message_id ของ user ไว้ก่อน เผื่อ admin สั่ง /purge ภายหลัง
    record_message(chat_id, user.id, message.message_id)

    # Every message is checked. Skipping rapid messages would create a window
    # where a clean post followed by a malicious post can evade moderation.
    await run_moderation_operation(
        lambda: _process_message(message, user, chat_id, context)
    )


async def run_moderation_operation(operation) -> None:
    """Run moderation with bounded concurrency and expose its busy state."""
    global _active_moderation, _moderation_idle_event

    if _moderation_idle_event is None:
        _moderation_idle_event = asyncio.Event()
        _moderation_idle_event.set()

    _active_moderation += 1
    _moderation_idle_event.clear()
    try:
        if _semaphore is None:
            await operation()
        else:
            async with _semaphore:
                await operation()
    finally:
        _active_moderation = max(0, _active_moderation - 1)
        if _active_moderation == 0:
            _moderation_idle_event.set()


async def process_sender_chat_message(message, sender_chat, chat_id: int, context) -> None:
    """Moderate a message posted as an external channel/sender chat."""
    text = extract_all_message_text(message)
    reason = forbidden_message_reason(message, text)
    if not reason:
        return

    thread_id = getattr(message, "message_thread_id", None)

    deleted = await delete_message_critical(
        message,
        f"delete sender_chat message chat={chat_id} sender_chat={sender_chat.id}",
        retries=0,
    )

    ban_method = getattr(context.bot, "ban_chat_sender_chat", None)
    sender_banned = False
    if callable(ban_method):
        sender_banned, _ = await run_critical_action(
            f"ban sender_chat={sender_chat.id} chat={chat_id}",
            lambda: ban_method(chat_id=chat_id, sender_chat_id=sender_chat.id),
        )
    else:
        logger.error("Installed Telegram library does not support ban_chat_sender_chat")

    if sender_banned:
        action = "🚫 ลบข้อความ + แบนช่องผู้ส่ง"
    elif deleted:
        action = "⚠️ ลบข้อความแล้ว แต่แบนช่องผู้ส่งไม่สำเร็จ"
    else:
        action = "⚠️ ลบข้อความและแบนช่องผู้ส่งไม่สำเร็จ — ตรวจสิทธิ์บอท"

    sender_name = html.escape(
        str(getattr(sender_chat, "title", None) or sender_chat.id)
    )
    await alert_action(
        context,
        chat_id,
        None,
        f"ช่องภายนอก {sender_name}: {reason}",
        action,
        thread_id=thread_id,
    )


async def _process_message(message, user, chat_id: int, context):
    """
    Logic จริงทั้งหมดอยู่ในฟังก์ชันนี้
    ห่อด้วย try/except เพื่อกันไม่ให้ exception ตัวเดียวล่มทั้ง bot
    """
    try:
        # สกัดข้อความแบบครบวงจร (รวม Checklist, Caption, Poll, Contact, Venue, Quote)
        text = extract_all_message_text(message)
        thread_id = getattr(message, "message_thread_id", None)

        # ——— Admin bypass ———
        admin_status = await is_admin_cached(context, chat_id, user.id)
        if admin_status is None:
            logger.warning(
                f"Skipped destructive moderation for user {user.id} in chat "
                f"{chat_id}: admin status could not be verified"
            )
            return
        if admin_status:
            return

        # ————————————————————————
        # PERMANENT MUTE HELPER
        # ————————————————————————

        async def mute_permanent() -> str:
            if PURGE_SPAMMER_HISTORY_ON_DETECT:
                ban_ok, _ = await run_critical_action(
                    f"ban spammer={user.id} chat={chat_id}",
                    lambda: context.bot.ban_chat_member(
                        chat_id=chat_id,
                        user_id=user.id,
                        revoke_messages=True,
                    ),
                )
                if ban_ok:
                    deleted_count = await purge_user_messages(
                        context,
                        chat_id,
                        user.id,
                    )
                    if deleted_count:
                        return f"🚫 BAN + ลบประวัติ {deleted_count} ข้อความ"
                    return "🚫 BAN สำเร็จ"

            restrict_ok, _ = await run_critical_action(
                f"restrict spammer={user.id} chat={chat_id}",
                lambda: context.bot.restrict_chat_member(
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
                ),
            )
            if restrict_ok:
                return "🔇 MUTE ถาวร"
            return "⚠️ BAN/MUTE ไม่สำเร็จ — ตรวจสิทธิ์บอท"

        async def enforce_violation(reason: str) -> None:
            # The spam message and sender are handled before any optional alert.
            # Mark synchronously before the first API await so simultaneous
            # messages from the same spammer do not trigger duplicate bans.
            add_permanent_mute(chat_id, user.id)
            deleted = await delete_message_critical(
                message,
                f"delete violation chat={chat_id} user={user.id}",
                retries=0,
            )

            # นำข้อความที่เพิ่งลบสำเร็จออกจาก log ทันที เพื่อไม่ให้ purge_user_messages พยายามลบซ้ำ
            if deleted:
                log_key = (chat_id, user.id)
                if log_key in user_message_log:
                    user_message_log[log_key] = [
                        e for e in user_message_log[log_key] if e[0] != message.message_id
                    ]

            action = await mute_permanent()
            if not deleted:
                action += " (ลบข้อความต้นเหตุไม่สำเร็จ)"
            await alert_action(
                context,
                chat_id,
                user,
                reason,
                action,
                thread_id=thread_id,
            )

        # ——— Non-admin bot forbidden content ———
        if getattr(user, "is_bot", False):
            reason = forbidden_message_reason(message, text)
            if reason:
                await enforce_violation(f"บอทไม่ใช่แอดมิน: {reason}")
                return

        # ——— Already permanently muted ———
        if is_permanently_muted(chat_id, user.id):
            await delete_message_critical(
                message,
                f"delete muted-user message chat={chat_id} user={user.id}",
                retries=0,
            )
            return

        # ——— สแกนเงื่อนไขข้อความต้องห้ามครบทุกรูปแบบ ———
        reason = forbidden_message_reason(message, text)
        if reason:
            await enforce_violation(reason)
            return

        # ——— New member link (ตรวจเฉพาะสมาชิกใหม่ช่วง 60 วินาทีแรก) ———
        join_key = user_key(chat_id, user.id)
        if join_key in join_times and time.time() - join_times[join_key] < 60:
            if message_has_link(message):
                await enforce_violation("สมาชิกใหม่ส่งลิงก์")
                return

        # ——— Spam flood ———
        if (
            getattr(message, "edit_date", None) is None
            and not is_backlog_message(message)
            and is_spam(chat_id, user.id)
        ):
            await enforce_violation("Spam ข้อความ")
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


async def _purge_command_impl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not chat:
        return

    if not await guard_allowed_chat(update, context):
        return
    mark_update_seen()

    thread_id = getattr(message, "message_thread_id", None)
    sender_chat = getattr(message, "sender_chat", None)
    sender_chat_is_group = bool(sender_chat and sender_chat.id == chat.id)

    logger.info(
        f"/purge received in chat {chat.id} "
        f"user={getattr(user, 'id', None)} "
        f"sender_chat={getattr(sender_chat, 'id', None)} "
        f"reply={bool(message.reply_to_message)}"
    )

    if not sender_chat_is_group:
        if not user:
            await message.reply_text("คำสั่งนี้ใช้ได้เฉพาะ admin")
            return

        invoker_admin_status = await is_admin_cached(context, chat.id, user.id)
        if invoker_admin_status is None:
            await message.reply_text(
                "ตรวจสอบสิทธิ์ admin ไม่สำเร็จชั่วคราว กรุณาลองใหม่"
            )
            return
        if not invoker_admin_status:
            await message.reply_text("คำสั่งนี้ใช้ได้เฉพาะ admin")
            return

    target_message = message.reply_to_message
    if not target_message or not target_message.from_user:
        await message.reply_text("ใช้ /purge โดย reply ข้อความสแปมที่ต้องการล้าง")
        return

    target = target_message.from_user
    target_admin_status = await is_admin_cached(context, chat.id, target.id)
    if target_admin_status is None:
        await message.reply_text(
            "ตรวจสอบสถานะผู้ใช้เป้าหมายไม่สำเร็จชั่วคราว กรุณาลองใหม่"
        )
        return
    if target_admin_status:
        await message.reply_text("ไม่ล้างข้อความของ admin")
        return

    add_permanent_mute(chat.id, target.id)

    # ——— 1) Ban + revoke (เป็น pass แรกแบบฟรี — แต่ไม่พึ่งมันลบจริง) ———
    ban_ok, _ = await run_critical_action(
        f"/purge ban user={target.id} chat={chat.id}",
        lambda: context.bot.ban_chat_member(
            chat_id=chat.id,
            user_id=target.id,
            revoke_messages=True,
        ),
    )
    restrict_ok = False
    if ban_ok:
        logger.info(f"/purge ban OK for user {target.id} in chat {chat.id}")
    else:
        # ban ไม่ผ่าน → fallback restrict (mute) แทน
        restrict_ok, _ = await run_critical_action(
            f"/purge restrict user={target.id} chat={chat.id}",
            lambda: context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=target.id,
                permissions=ChatPermissions(can_send_messages=False),
            ),
        )

    # ——— 2) ลบโพสต์ทั้งหมดของ user คนนี้ย้อนหลัง ———
    # reply โพสต์เดียว → ลบทุกโพสต์ของ user คนนั้น (ที่อยู่ในกรอบ 48 ชม.)
    deleted = await purge_user_messages(
        context, chat.id, target.id,
        extra_ids=[target_message.message_id],   # โพสต์ที่ admin reply → รวมไว้เสมอ
    )

    # ลบคำสั่ง /purge ของ admin
    await delete_message_critical(
        message,
        f"delete /purge command chat={chat.id}",
        retries=0,
    )

    # ——— 3) รายงานผล ———
    if ban_ok and deleted > 0:
        action_text = f"🚫 BAN + ลบโพสต์ทั้งหมด {deleted} ข้อความ"
    elif ban_ok:
        action_text = ("🚫 BAN สำเร็จ — แต่ลบข้อความไม่ได้ "
                       "(อาจเก่ากว่า 48 ชม. หรือถูกลบไปแล้ว)")
    elif restrict_ok and deleted > 0:
        action_text = f"🔇 MUTE ถาวร + ลบโพสต์ทั้งหมด {deleted} ข้อความ"
    elif restrict_ok:
        action_text = "🔇 MUTE ถาวรสำเร็จ — แต่ลบข้อความไม่ได้"
    elif deleted > 0:
        action_text = (
            f"⚠️ ลบโพสต์ได้ {deleted} ข้อความ แต่ BAN/MUTE ไม่สำเร็จ"
        )
    else:
        action_text = "⚠️ ลบ/แบนไม่สำเร็จ — ตรวจสิทธิ์ Delete Messages ของบอท"

    await alert_action(
        context,
        chat.id,
        target,
        "Admin ใช้ /purge เพื่อล้างสแปมย้อนหลัง",
        action_text,
        thread_id=thread_id,
    )


async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Manual cleanup has the same priority as automatic moderation.
    await run_moderation_operation(
        lambda: _purge_command_impl(update, context)
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
        delay = retry_after_seconds(error)
        logger.warning(f"Telegram flood control: retry after {delay:.1f}s")
        await asyncio.sleep(min(delay, CRITICAL_RETRY_MAX_WAIT))
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

    # สร้างตัวควบคุมงานก่อนเริ่ม polling; event จะถูกผูกกับ loop เมื่อใช้งาน
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    _moderation_idle_event = asyncio.Event()
    _moderation_idle_event.set()

    # Flask สำหรับ UptimeRobot ping (daemon thread)
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    logger.info(f"Health check server started on port {PORT}")

    # [STABILITY] ApplicationBuilder พร้อม timeout และ connection pool
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .concurrent_updates(MAX_CONCURRENT)
        .connection_pool_size(MAX_CONCURRENT + WELCOME_MAX_CONCURRENT + 5)
        .read_timeout(15)               # รอ Telegram ตอบกลับ 15 วินาที
        .write_timeout(15)
        .connect_timeout(15)
        .pool_timeout(10)
        .build()
    )

    application.add_handler(ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))
    application.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, check_message))
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
