import os
import time
import requests
from flask import Flask
from datetime import datetime

app = Flask(__name__)

# ───────────────── CONFIG ─────────────────
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")

BASE_LOT = 0.10
COOLDOWN_SECONDS = 300  # prevents spam signals

last_trade_time = 0


# ───────────────── INDICATOR LOGIC ─────────────────
def calculate_signal(rsi, ema_fast, ema_slow):
    """
    Option C logic:
    - Trend first (EMA)
    - RSI used for strength
    """

    if ema_fast > ema_slow:
        trend = "BUY"
    elif ema_fast < ema_slow:
        trend = "SELL"
    else:
        return "NO_TRADE", 0.0, "NEUTRAL"

    # BUY conditions
    if trend == "BUY":
        if rsi < 35:
            return "STRONG BUY", 1.0, trend
        elif rsi < 45:
            return "WEAK BUY", 0.5, trend

    # SELL conditions
    if trend == "SELL":
        if rsi > 65:
            return "STRONG SELL", 1.0, trend
        elif rsi > 55:
            return "WEAK SELL", 0.5, trend

    return "NO_TRADE", 0.0, trend


# ───────────────── POSITION SIZING ─────────────────
def lot_size(base_lot, strength):
    return round(base_lot * strength, 2)


# ───────────────── ALERT SYSTEM ─────────────────
def send_discord(message):
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": message})
    except:
        pass


def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    except:
        pass


def send_alert(message):
    send_discord(message)
    send_telegram(message)


# ───────────────── SIGNAL BUILDER ─────────────────
def build_trade(rsi, ema_fast, ema_slow, price):
    global last_trade_time

    now = time.time()

    # cooldown protection
    if now - last_trade_time < COOLDOWN_SECONDS:
        return None

    signal, strength, trend = calculate_signal(rsi, ema_fast, ema_slow)

    if signal == "NO_TRADE":
        return None

    lot = lot_size(BASE_LOT, strength)

    # simple SL/TP logic (can be improved later with ATR)
    if "BUY" in signal:
        sl = price - 2.15
        tp1 = price + 2.16
        tp2 = price + 4.31
    else:
        sl = price + 2.15
        tp1 = price - 2.16
        tp2 = price - 4.31

    message = f"""
📊 XAUUSD SIGNAL
━━━━━━━━━━━━━━━━━━━━
Action: {signal}
Trend: {trend}
Strength: {strength}
Lot Size: {lot}

Entry: {price}
SL: {sl}
TP1: {tp1}
TP2: {tp2}

RSI: {rsi}
Time: {datetime.utcnow()}
━━━━━━━━━━━━━━━━━━━━
"""

    last_trade_time = now
    send_alert(message)

    return message


# ───────────────── EXAMPLE DATA ENDPOINT ─────────────────
@app.route("/signal")
def signal_demo():
    """
    Replace these with real API data later
    """
    rsi = 39.8
    ema_fast = 4550
    ema_slow = 4560
    price = 4554.14

    result = build_trade(rsi, ema_fast, ema_slow, price)

    if result:
        return {"status": "signal sent", "message": result}

    return {"status": "no trade"}


# ───────────────── RUN SERVER ─────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
