import re
import os
import threading
from urllib.parse import urlparse
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Bot is running!"

ALLOWED_DOMAINS = [
    "t-hoy.com",
    "mangath.live",
    "นางแบบ.live (https://%E0%B8%99%E0%B8%B2%E0%B8%87%E0%B9%81%E0%B8%9A%E0%B8%9A.live)",
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
    "ฟิวแฟน.live (https://%E0%B8%9F%E0%B8%B4%E0%B8%A7%E0%B9%81%E0%B8%9F%E0%B8%99.live)",
    "1000drink.com",
    "ppnewsth.com",
    "แจกวาร์ป.live (https://%E0%B9%81%E0%B8%88%E0%B8%81%E0%B8%A7%E0%B8%B2%E0%B8%A3%E0%B9%8C%E0%B8%9B.live)",
    "longsanam.com",
    "toodtidgameth.com",
    "ttphoo.com",
    "larnom.com",
    "ockock.com",
    "kongcheer.com",
    "madamporns.com",
    "โอลี่แฟน.live (https://%E0%B9%82%E0%B8%AD%E0%B8%A5%E0%B8%B5%E0%B9%88%E0%B9%81%E0%B8%9F%E0%B8%99.live)",
    "โกดังญี่ปุ่น.com (https://%E0%B9%82%E0%B8%81%E0%B8%94%E0%B8%B1%E0%B8%87%E0%B8%8D%E0%B8%B5%E0%B9%88%E0%B8%9B%E0%B8%B8%E0%B9%88%E0%B8%99.com)",
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

URL_REGEX = re.compile(
    r"(https?://[^\s]+|www\.[^\s]+|t\.me/[^\s]+)",
    re.IGNORECASE
)

def extract_domain(url):
    if not url.startswith("http"):
        url = "http://" + url
    parsed = urlparse(url)
    return parsed.netloc.lower().replace("www.", "")

async def check_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = update.message.text or update.message.caption
    if not text:
        return

    urls = URL_REGEX.findall(text)

    for url in urls:
        domain = extract_domain(url)
        if domain not in ALLOWED_DOMAINS:
            await update.message.delete()
            break

def run_bot():
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(
        MessageHandler(filters.TEXT | filters.CaptionRegex(".*"), check_links)
    )
    application.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app_web.run(host="0.0.0.0", port=PORT)
