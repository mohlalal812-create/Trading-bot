from flask import Flask
import threading
import time
import requests
import random

app = Flask(__name__)

DISCORD_WEBHOOK = "YOUR_DISCORD_WEBHOOK"
TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"


def send(msg):
    print(msg)
    try:
        if DISCORD_WEBHOOK:
            requests.post(DISCORD_WEBHOOK, json={"content": msg})
    except:
        pass

    try:
        if TELEGRAM_TOKEN and CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except:
        pass


def bot_loop():
    print("BOT STARTED SUCCESSFULLY")

    while True:
        price = random.uniform(2300, 2400)

        if price > 2370:
            send(f"SELL XAUUSD @ {price:.2f}")
        elif price < 2330:
            send(f"BUY XAUUSD @ {price:.2f}")

        time.sleep(10)


@app.route("/")
def home():
    return "Bot running"


def run():
    thread = threading.Thread(target=bot_loop)
    thread.daemon = True
    thread.start()

    print("Flask starting...")

    app.run(host="0.0.0.0", port=10000)


if __name__ == "__main__":
    run()
