"""
XAUUSD Signal Bot (NO MT5)
--------------------------
Generates trading signals only (BUY / SELL)
Sends alerts via Telegram + Discord

Requirements:
pip install requests flask
"""

import os
import time
import requests
from flask import Flask

app = Flask(__name__)

# ───────── CONFIG ─────────
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")
TWELVE_API_KEY  = os.getenv("TWELVE_API_KEY", "")

SYMBOL        = "XAU/USD"
INTERVAL      = "1min"

SMA_PERIOD    = 20
ATR_PERIOD    = 14

CHECK_EVERY   = 60
COOLDOWN      = 300

last_signal = None
last_time = 0


# ───────── SEND ALERT ─────────
def send(msg):
    print(msg)

    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
        except:
            pass

    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=5)
        except:
            pass


# ───────── PRICE DATA ─────────
def get_prices(n=100):
    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": n,
        "apikey": TWELVE_API_KEY,
    }

    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()

        if "values" not in data:
            return None

        bars = list(reversed(data["values"]))

        closes = [float(x["close"]) for x in bars]
        highs  = [float(x["high"]) for x in bars]
        lows   = [float(x["low"]) for x in bars]

        return closes, highs, lows

    except:
        return None


# ───────── INDICATORS ─────────
def sma(data, period):
    return sum(data[-period:]) / period


def atr(highs, lows, closes, period):
    trs = []

    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < period:
        return sum(trs) / len(trs)

    return sum(trs[-period:]) / period


# ───────── SIGNAL ENGINE ─────────
def check_signals():
    global last_signal, last_time

    if time.time() - last_time < COOLDOWN:
        return

    data = get_prices()
    if not data:
        return

    closes, highs, lows = data

    if len(closes) < SMA_PERIOD + ATR_PERIOD:
        return

    price = closes[-1]
    prev  = closes[-2]

    sma_now = sma(closes, SMA_PERIOD)
    sma_prev = sma(closes[:-1], SMA_PERIOD)

    atr_val = atr(highs, lows, closes, ATR_PERIOD)

    sl = atr_val * 1.5
    tp = atr_val * 3.0

    # ───── BUY ─────
    if prev <= sma_prev and price > sma_now:
        if last_signal != "BUY":
            last_signal = "BUY"
            last_time = time.time()

            send(
                f"📈 BUY SIGNAL XAUUSD\n"
                f"Entry: {price:.2f}\n"
                f"SL: {price - sl:.2f}\n"
                f"TP: {price + tp:.2f}"
            )

    # ───── SELL ─────
    elif prev >= sma_prev and price < sma_now:
        if last_signal != "SELL":
            last_signal = "SELL"
            last_time = time.time()

            send(
                f"📉 SELL SIGNAL XAUUSD\n"
                f"Entry: {price:.2f}\n"
                f"SL: {price + sl:.2f}\n"
                f"TP: {price - tp:.2f}"
            )


# ───────── LOOP ─────────
def loop():
    send("Signal bot started (NO MT5)")

    while True:
        try:
            check_signals()
        except Exception as e:
            print("Error:", e)

        time.sleep(CHECK_EVERY)


# ───────── FLASK ─────────
@app.route("/")
def home():
    return "Signal Bot Running"


@app.route("/status")
def status():
    return {
        "symbol": SYMBOL,
        "signal": last_signal
    }


# ───────── START ─────────
if __name__ == "__main__":
    from threading import Thread
    Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
