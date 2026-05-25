from flask import Flask
import threading
import time
import random
import requests

app = Flask(__name__)

DISCORD_WEBHOOK = "YOUR_DISCORD_WEBHOOK"
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

def send_discord(msg):
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg})
    except:
        pass

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except:
        pass

def bot_loop():
    last = None
    while True:
        price = random.uniform(2300, 2400)

        if price > 2370:
            signal = f"SELL XAUUSD @ {price:.2f}"
        elif price < 2330:
            signal = f"BUY XAUUSD @ {price:.2f}"
        else:
            signal = None

        if signal and signal != last:
            print(signal)
            send_discord(signal)
            send_telegram(signal)
            last = signal

        time.sleep(10)

threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Bot running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
