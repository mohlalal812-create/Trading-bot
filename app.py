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

# ── CONFIG ───────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")
TWELVE_API_KEY  = os.getenv("TWELVE_API_KEY", "")

SYMBOL         = "XAU/USD"
INTERVAL       = "1min"
SMA_PERIOD     = 20
ATR_PERIOD     = 14
ATR_SL_MULT    = 1.5
ATR_TP1_MULT   = 1.5
ATR_TP2_MULT   = 3.0
CHECK_EVERY    = 30
STRONG_THRESH  = 0.4
NEWS_WARN_MINS = 30

last_signal = None
last_signal_time = 0
SIGNAL_COOLDOWN = 300

alerted_news = set()

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


# ── NEWS ─────────────────────────────────────────────────────────────

def fetch_news():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        return requests.get(url, timeout=10).json()
    except:
        return []

def check_news():
    events = fetch_news()
    if not events:
        return

    now = datetime.now(timezone.utc)

    for event in events:
        if event.get("impact") != "High":
            continue
        if event.get("country", "").upper() != "USD":
            continue

        title = event.get("title", "")
        date = event.get("date", "")
        etime = event.get("time", "")

        try:
            dt_str = f"{date} {etime}"
            event_dt = datetime.strptime(dt_str, "%Y-%m-%d %I:%M%p").replace(tzinfo=timezone.utc)
        except:
            continue

        mins_away = (event_dt - now).total_seconds() / 60

        if 0 < mins_away <= NEWS_WARN_MINS:
            key = f"{date}_{etime}_{title}"
            if key in alerted_news:
                continue

            alerted_news.add(key)

            send(
                f"📰 NEWS ALERT\n"
                f"Event: {title}\n"
                f"Time: {etime} UTC (~{int(mins_away)}m)\n"
                f"⚠️ Avoid trading around this time"
            )


# ── PRICE DATA ─────────────────────────────────────────────────────────────

def get_prices(n=60):
    if not TWELVE_API_KEY:
        print("Missing API key")
        return None

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
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


# ── INDICATORS (FIXED) ───────────────────────────────────────────────────────

def sma(prices, period):
    return sum(prices[-period:]) / period


def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    atr_val = sum(trs[:period]) / period

    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period

    return atr_val


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50

    gains, losses = [], []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ── SIGNAL FORMAT ─────────────────────────────────────────────────────────────

def format_signal(direction, label, emoji, entry, sl, tp1, tp2, rsi_val, note):
    return (
        f"{emoji} {label}\n"
        f"Pair: XAUUSD\n"
        f"Action: {direction}\n"
        f"Entry: {entry:.2f}\n"
        f"SL: {sl:.2f}\n"
        f"TP1: {tp1:.2f}\n"
        f"TP2: {tp2:.2f}\n"
        f"RSI: {rsi_val:.1f}\n"
        f"Note: {note}"
    )


# ── SIGNAL LOGIC (FIXED) ─────────────────────────────────────────────────────

def check_signal():
    global last_signal, last_signal_time

    now = time.time()
    if now - last_signal_time < SIGNAL_COOLDOWN:
        return

    result = get_prices()
    if not result:
        return

    closes, highs, lows = result

    if len(closes) < 50:
        return

    current = closes[-1]
    prev = closes[-2]

    sma_val = sma(closes, SMA_PERIOD)
    atr_val = atr(highs, lows, closes, ATR_PERIOD)
    rsi_val = rsi(closes)

    if rsi_val > 70 or rsi_val < 30:
        return

    candle_move = abs(current - prev)
    strength = abs(current - sma_val) / atr_val if atr_val else 0
    is_strong = candle_move > atr_val * STRONG_THRESH and strength > 0.8

    trend_up = current > sma_val
    prev_trend_up = prev > sma_val

    sl_dist = atr_val * ATR_SL_MULT
    tp1_dist = atr_val * ATR_TP1_MULT
    tp2_dist = atr_val * ATR_TP2_MULT

    if not prev_trend_up and trend_up:
        last_signal = "BUY"
        last_signal_time = now

        send(format_signal(
            "BUY",
            "BUY SIGNAL",
            "📈",
            current,
            current - sl_dist,
            current + tp1_dist,
            current + tp2_dist,
            rsi_val,
            "Trend reversal confirmed"
        ))

    elif prev_trend_up and not trend_up:
        last_signal = "SELL"
        last_signal_time = now

        send(format_signal(
            "SELL",
            "SELL SIGNAL",
            "📉",
            current,
            current + sl_dist,
            current - tp1_dist,
            current - tp2_dist,
            rsi_val,
            "Trend reversal confirmed"
        ))

    elif is_strong:
        send(f"STRONG TREND {'BUY' if trend_up else 'SELL'} — waiting for pullback")


# ── LOOP ──────────────────────────────────────────────────────────────────────

def bot_loop():
    send("Bot started")

    while True:
        try:
            check_news()
            check_signal()
        except Exception as e:
            print(e)

        time.sleep(CHECK_EVERY)


# ── FLASK ─────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return "Bot running"

@app.route("/status")
def status():
    return {"last_signal": last_signal}


def run():
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))


if __name__ == "__main__":
    run()
