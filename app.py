import sys
import os
sys.stdout = sys.stderr
os.environ["PYTHONUNBUFFERED"] = "1"

from flask import Flask
import threading
import time
import requests

app = Flask(__name__)

# ── CONFIG ───────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1508337970099388417/WCRz7Gv0qK7B2rW0Gpy_6W486j5_vigNxhqM3eRuMVeeOZ1V--IeT35EEEUxe-i_zvkx"
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")
TWELVE_API_KEY  = "286312c01d50410a9ee56a863143ad0f"

SYMBOL         = "XAU/USD"
INTERVAL       = "1min"
SMA_PERIOD     = 20
ATR_PERIOD     = 14
ATR_SL_MULT    = 1.5
ATR_TP_MULT    = 3.0
CHECK_EVERY    = 60   # seconds


# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────

def send(msg: str):
    print(msg)
    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
        except Exception as e:
            print(f"[Discord error] {e}")
    if TELEGRAM_TOKEN and CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=5)
        except Exception as e:
            print(f"[Telegram error] {e}")


# ── PRICE DATA ────────────────────────────────────────────────────────────────

def get_prices(n: int = SMA_PERIOD + ATR_PERIOD + 5):
    if not TWELVE_API_KEY:
        print("[Error] TWELVE_API_KEY not set.")
        return None
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol":     SYMBOL,
        "interval":   INTERVAL,
        "outputsize": n,
        "apikey":     TWELVE_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") == "error":
            print(f"[API error] {data.get('message')}")
            return None
        bars   = list(reversed(data["values"]))
        closes = [float(b["close"]) for b in bars]
        highs  = [float(b["high"])  for b in bars]
        lows   = [float(b["low"])   for b in bars]
        return closes, highs, lows
    except Exception as e:
        print(f"[Price fetch error] {e}")
        return None


# ── INDICATORS ────────────────────────────────────────────────────────────────

def sma(prices, period):
    return sum(prices[-period:]) / period

def atr(highs, lows, closes, period):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


# ── SIGNAL LOGIC ──────────────────────────────────────────────────────────────

last_signal = None

def check_signal():
    global last_signal

    result = get_prices()
    if result is None:
        return
    closes, highs, lows = result

    if len(closes) < SMA_PERIOD + ATR_PERIOD:
        print("[Warning] Not enough data.")
        return

    current_price = closes[-1]
    prev_price    = closes[-2]
    current_sma   = sma(closes, SMA_PERIOD)
    prev_sma      = sma(closes[:-1], SMA_PERIOD)
    current_atr   = atr(highs, lows, closes, ATR_PERIOD)

    sl_dist = current_atr * ATR_SL_MULT
    tp_dist = current_atr * ATR_TP_MULT

    sl_buy  = round(current_price - sl_dist, 2)
    tp_buy  = round(current_price + tp_dist, 2)
    sl_sell = round(current_price + sl_dist, 2)
    tp_sell = round(current_price - tp_dist, 2)

    if prev_price <= prev_sma and current_price > current_sma:
        if last_signal != "BUY":
            last_signal = "BUY"
            send(
                f"📈 BUY XAUUSD @ {current_price:.2f}\n"
                f"SL: {sl_buy}  |  TP: {tp_buy}\n"
                f"SMA: {current_sma:.2f}  ATR: {current_atr:.2f}"
            )

    elif prev_price >= prev_sma and current_price < current_sma:
        if last_signal != "SELL":
            last_signal = "SELL"
            send(
                f"📉 SELL XAUUSD @ {current_price:.2f}\n"
                f"SL: {sl_sell}  |  TP: {tp_sell}\n"
                f"SMA: {current_sma:.2f}  ATR: {current_atr:.2f}"
            )

    else:
        print(f"[No signal] Price={current_price:.2f}  SMA={current_sma:.2f}  ATR={current_atr:.2f}")


# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

def bot_loop():
    print("Bot started.")
    send(f"🤖 XAUUSD Signal Bot started\nStrategy: {SMA_PERIOD}-SMA crossover + ATR SL/TP")

    while True:
        try:
            check_signal()
        except Exception as e:
            print(f"[Loop error] {e}")
        time.sleep(CHECK_EVERY)


# ── FLASK ─────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return "Bot running", 200

@app.route("/status")
def status():
    return {"last_signal": last_signal}, 200


def run():
    thread = threading.Thread(target=bot_loop, daemon=True)
    thread.start()
    port = int(os.getenv("PORT", 10000))
    print(f"Flask starting on port {port}...")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()
