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
ATR_TP1_MULT   = 1.5   # TP1 = 1:1 risk reward
ATR_TP2_MULT   = 3.0   # TP2 = 1:2 risk reward
CHECK_EVERY    = 30
STRONG_THRESH  = 0.5


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

def rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ── SIGNAL FORMAT ─────────────────────────────────────────────────────────────

def format_signal(direction, label, emoji, entry, sl, tp1, tp2, rsi_val, note):
    sl_pips  = round(abs(entry - sl), 2)
    tp1_pips = round(abs(entry - tp1), 2)
    tp2_pips = round(abs(entry - tp2), 2)
    return (
        f"{emoji} {label}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Pair:    XAUUSD\n"
        f"Action:  {direction}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entry:   {entry:.2f}\n"
        f"SL:      {sl:.2f}  (-{sl_pips})\n"
        f"TP1:     {tp1:.2f}  (+{tp1_pips})  🎯\n"
        f"TP2:     {tp2:.2f}  (+{tp2_pips})  🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"RSI:     {rsi_val:.1f}\n"
        f"Note:    {note}"
    )


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
    current_rsi   = rsi(closes)

    candle_move = abs(current_price - prev_price)
    is_strong   = candle_move > (current_atr * STRONG_THRESH)

    sl_dist  = current_atr * ATR_SL_MULT
    tp1_dist = current_atr * ATR_TP1_MULT
    tp2_dist = current_atr * ATR_TP2_MULT

    trend = "↑" if current_price > current_sma else "↓"
    print(
        f"[Live] XAUUSD={current_price:.2f}  SMA={current_sma:.2f}  "
        f"ATR={current_atr:.2f}  RSI={current_rsi:.1f}  {trend}"
    )

    # ── BUY crossover ──
    if prev_price <= prev_sma and current_price > current_sma:
        if last_signal != "BUY":
            last_signal = "BUY"
            sl  = round(current_price - sl_dist, 2)
            tp1 = round(current_price + tp1_dist, 2)
            tp2 = round(current_price + tp2_dist, 2)

            if is_strong and current_rsi < 55:
                label = "STRONG BUY SIGNAL"
                emoji = "🔥"
                note  = "Strong momentum — full position ok"
            else:
                label = "WEAK BUY SIGNAL"
                emoji = "📈"
                note  = "Low momentum — reduce size, wait for TP1 first"

            send(format_signal("BUY", label, emoji, current_price, sl, tp1, tp2, current_rsi, note))

    # ── SELL crossover ──
    elif prev_price >= prev_sma and current_price < current_sma:
        if last_signal != "SELL":
            last_signal = "SELL"
            sl  = round(current_price + sl_dist, 2)
            tp1 = round(current_price - tp1_dist, 2)
            tp2 = round(current_price - tp2_dist, 2)

            if is_strong and current_rsi > 45:
                label = "STRONG SELL SIGNAL"
                emoji = "🔥"
                note  = "Strong momentum — full position ok"
            else:
                label = "WEAK SELL SIGNAL"
                emoji = "📉"
                note  = "Low momentum — reduce size, wait for TP1 first"

            send(format_signal("SELL", label, emoji, current_price, sl, tp1, tp2, current_rsi, note))

    # ── Strong trend (no crossover yet) ──
    elif is_strong:
        direction = "BUY" if current_price > current_sma else "SELL"
        if last_signal != f"TREND_{direction}":
            last_signal = f"TREND_{direction}"
            emoji = "📈" if direction == "BUY" else "📉"
            send(
                f"{emoji} STRONG TREND — {direction}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Price:  {current_price:.2f}\n"
                f"SMA:    {current_sma:.2f}\n"
                f"RSI:    {current_rsi:.1f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"No crossover yet — watch for entry on pullback to SMA"
            )


# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

def bot_loop():
    print("Bot started.")
    send(
        f"🤖 XAUUSD Signal Bot started\n"
        f"Strategy: {SMA_PERIOD}-SMA + ATR + RSI\n"
        f"Checking every {CHECK_EVERY}s"
    )

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
