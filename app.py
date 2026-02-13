import os
import re
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==============================
# BOT TOKEN
# ==============================
TOKEN = os.getenv("BOT_TOKEN")

# ==============================
# ALLOWED DOMAINS
# ==============================
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

# ==============================
# FUNCTION ตรวจสอบลิงก์
# ==============================
def extract_urls(text):
    url_pattern = r"(https?://[^\s]+)"
    return re.findall(url_pattern, text)


def is_allowed(url):
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # ตัด www.
    if domain.startswith("www."):
        domain = domain[4:]

    return domain in ALLOWED_DOMAINS


# ==============================
# HANDLER ลบลิงก์
# ==============================
async def check_links(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        return

    urls = extract_urls(update.message.text)

    if not urls:
        return

    for url in urls:
        if not is_allowed(url):
            try:
                await update.message.delete()
                print(f"ลบลิงก์สแปม: {url}")
                return
            except Exception as e:
                print("ลบข้อความไม่ได้:", e)


# ==============================
# START BOT
# ==============================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            check_links
        )
    )

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
