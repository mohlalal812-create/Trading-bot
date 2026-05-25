import sys
import os
sys.stdout = sys.stderr
os.environ["PYTHONUNBUFFERED"] = "1"

from flask import Flask
import threading
import time
import requests
from datetime import datetime, timezone

app = Flask(__name__)

# ───────────────── CONFIG ─────────────────
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")
TWELVE_API_KEY  = os.getenv("TWELVE_API_KEY", "")

SYMBOL = "XAU/USD"

SMA_PERIOD = 20
ATR_PERIOD = 14

ATR_SL_MULT  = 1.6
ATR_TP1_MULT = 1.8
ATR_TP2_MULT = 3.2

CHECK_EVERY = 30
COOLDOWN = 300

last_signal = None
last_time = 0

# ───────────────── SESSIONS ─────────────────
def in_trading_session():
    """
    Simple institutional filter:
    London + New York overlap (high volatility)
    UTC time used.
    """
    hour = datetime.utcnow().hour

    # London session: 07 - 16 UTC
    # NY session: 12 - 21 UTC
    return (7 <= hour <= 21)

# ───────────────── SEND ─────────────────

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


# ───────────────── DATA ─────────────────

def get_tf(interval, n=80):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": n,
        "apikey": TWELVE_API_KEY,
    }

    try:
        data = requests.get(url, params=params, timeout=10).json()
        if data.get("status") == "error":
            return None

        bars = list(reversed(data["values"]))
        closes = [float(b["close"]) for b in bars]
        highs  = [float(b["high"]) for b in bars]
        lows   = [float(b["low"]) for b in bars]

        return closes, highs, lows
    except:
        return None


# ───────────────── INDICATORS ─────────────────

def sma(prices, p):
    return sum(prices[-p:]) / p


def atr(highs, lows, closes, p=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    atr_val = sum(trs[:p]) / p
    for i in range(p, len(trs)):
        atr_val = (atr_val * (p - 1) + trs[i]) / p

    return atr_val


def rsi(closes, p=14):
    if len(closes) < p + 1:
        return 50

    gains, losses = [], []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:p]) / p
    avg_loss = sum(losses[:p]) / p

    for i in range(p, len(gains)):
        avg_gain = (avg_gain * (p - 1) + gains[i]) / p
        avg_loss = (avg_loss * (p - 1) + losses[i]) / p

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ───────────────── STRUCTURE LOGIC ─────────────────

def liquidity_sweep(closes, highs, lows):
    """
    Basic sweep detection:
    last candle breaks previous high/low but closes back inside.
    """
    if len(closes) < 3:
        return None

    prev_high = highs[-2]
    prev_low = lows[-2]
    last_close = closes[-1]
    last_high = highs[-1]
    last_low = lows[-1]

    # bullish sweep (fake breakdown)
    if last_low < prev_low and last_close > prev_low:
        return "BUY"

    # bearish sweep (fake breakout)
    if last_high > prev_high and last_close < prev_high:
        return "SELL"

    return None


# ───────────────── SIGNAL ENGINE ─────────────────

def check_signal():
    global last_signal, last_time

    now = time.time()
    if now - last_time < COOLDOWN:
        return

    if not in_trading_session():
        return

    m1 = get_tf("1min")
    m5 = get_tf("5min")
    m15 = get_tf("15min")

    if not m1 or not m5 or not m15:
        return

    c1, h1, l1 = m1
    c5, h5, l5 = m5
    c15, h15, l15 = m15

    price = c1[-1]
    rsi_val = rsi(c1)

    if rsi_val > 70 or rsi_val < 30:
        return

    atr_val = atr(h5, l5, c5, ATR_PERIOD)
    if atr_val == 0:
        return

    trend_15 = "UP" if c15[-1] > sma(c15, SMA_PERIOD) else "DOWN"
    trend_5  = "UP" if c5[-1] > sma(c5, SMA_PERIOD) else "DOWN"
    trend_1  = "UP" if c1[-1] > sma(c1, SMA_PERIOD) else "DOWN"

    sweep = liquidity_sweep(c1, h1, l1)

    sl = atr_val * ATR_SL_MULT
    tp1 = atr_val * ATR_TP1_MULT
    tp2 = atr_val * ATR_TP2_MULT

    bullish = trend_15 == "UP" and trend_5 == "UP" and trend_1 == "UP"
    bearish = trend_15 == "DOWN" and trend_5 == "DOWN" and trend_1 == "DOWN"

    # ── STRONG ENTRY RULES ──
    if bullish and (sweep == "BUY" or rsi_val < 60):
        last_signal = "BUY"
        last_time = now

        send(
            f"🔥 INSTITUTIONAL BUY\n"
            f"Price: {price:.2f}\n"
            f"SL: {price - sl:.2f}\n"
            f"TP1: {price + tp1:.2f}\n"
            f"TP2: {price + tp2:.2f}\n"
            f"RSI: {rsi_val:.1f}\n"
            f"Reason: MTF + Liquidity Sweep"
        )

    elif bearish and (sweep == "SELL" or rsi_val > 40):
        last_signal = "SELL"
        last_time = now

        send(
            f"🔥 INSTITUTIONAL SELL\n"
            f"Price: {price:.2f}\n"
            f"SL: {price + sl:.2f}\n"
            f"TP1: {price - tp1:.2f}\n"
            f"TP2: {price - tp2:.2f}\n"
            f"RSI: {rsi_val:.1f}\n"
            f"Reason: MTF + Liquidity Sweep"
        )


# ───────────────── LOOP ─────────────────

def loop():
    send("🚀 Institutional MTF Bot Started")

    while True:
        try:
            check_signal()
        except Exception as e:
            print("ERROR:", e)

        time.sleep(CHECK_EVERY)


# ───────────────── FLASK ─────────────────

@app.route("/")
def home():
    return "Institutional Bot Running", 200


@app.route("/status")
def status():
    return {
        "last_signal": last_signal,
        "cooldown": COOLDOWN
    }, 200


def run():
    threading.Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))


if __name__ == "__main__":
    run()
