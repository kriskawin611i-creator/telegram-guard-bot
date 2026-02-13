import re
import asyncio
from urllib.parse import urlparse
import idna

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "YOUR_BOT_TOKEN"

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

# แปลงโดเมนไทยเป็น punycode
def normalize_domain(domain):
    try:
        return idna.encode(domain.strip().lower()).decode()
    except:
        return domain.strip().lower()

ALLOWED_DOMAINS_NORMALIZED = [normalize_domain(d) for d in ALLOWED_DOMAINS]

# regex จับ url
URL_REGEX = re.compile(
    r"(https?://[^\s]+)|(www\.[^\s]+)",
    re.IGNORECASE
)

def extract_domains(text):
    urls = URL_REGEX.findall(text)
    domains = []

    for match in urls:
        url = match[0] if match[0] else match[1]
        if not url.startswith("http"):
            url = "http://" + url

        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        domains.append(normalize_domain(domain))

    return domains


def is_allowed(domain):
    for allowed in ALLOWED_DOMAINS_NORMALIZED:
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


async def filter_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    domains = extract_domains(text)

    if not domains:
        return

    for domain in domains:
        if not is_allowed(domain):
            try:
                await update.message.delete()
            except:
                pass
            return


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            filter_links
        )
    )

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
