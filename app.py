import os
import re
import threading
from urllib.parse import urlparse

from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

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
# ALLOWED DOMAINS
# =============================

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

URL_PATTERN = r"(https?://[^\s]+|www\.[^\s]+)"

def extract_urls(text):
    return re.findall(URL_PATTERN, text)

def is_allowed(url):
    if not url.startswith("http"):
        url = "http://" + url

    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    return domain in ALLOWED_DOMAINS

# =============================
# BOT LOGIC
# =============================

async def check_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    urls = extract_urls(update.message.text)

    for url in urls:
        if not is_allowed(url):
            try:
                await update.message.delete()
                print(f"Deleted spam link: {url}")
                return
            except Exception as e:
                print("Delete failed:", e)

# =============================
# MAIN
# =============================

if __name__ == "__main__":

    # รัน Flask ใน thread แยก
    web_thread = threading.Thread(target=run_web)
    web_thread.start()

    # รัน bot ใน main thread (สำคัญ!)
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), check_links)
    )

    print("Bot started...")
    application.run_polling()
