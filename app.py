import os
import re
from urllib.parse import urlparse
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

app = Flask(__name__)
application = ApplicationBuilder().token(TOKEN).build()

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

URL_PATTERN = re.compile(r'(https?://[^\s]+|www\.[^\s]+)')

def is_allowed(url):
    try:
        if not url.startswith("http"):
            url = "http://" + url
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        return any(domain.endswith(allowed) for allowed in ALLOWED_DOMAINS)
    except:
        return False

async def check_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        urls = URL_PATTERN.findall(update.message.text)
        for url in urls:
            if not is_allowed(url):
                await update.message.delete()
                break

application.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, check_links)
)

@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "ok"

@app.route("/")
def home():
    return "Bot is running"

if __name__ == "__main__":
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )
