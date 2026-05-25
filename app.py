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
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1508337970099388417/WCRz7Gv0qK7B2rW0Gpy_6W486j5_vigNxhqM3eRuMVeeOZ1V--IeT35EEEUxe-i_zvkx"
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")
TWELVE_API_KEY  = "286312c01d50410a9ee56a863143ad0f"

# ── PAIRS ─────────────────────────────────────────────────────────────────────
PAIRS = [
    {"symbol": "XAU/USD",  "name": "XAUUSD",  "pip": 2},
    {"symbol": "XAG/USD",  "name": "XAGUSD",  "pip": 4},
    {"symbol": "EUR/USD",  "name": "EURUSD",  "pip": 5},
    {"symbol": "GBP/USD",  "name": "GBPUSD",  "pip": 5},
    {"symbol": "USD/JPY",  "name": "USDJPY",  "pip": 3},
    {"symbol": "AUD/USD",  "name": "AUDUSD",  "pip": 5},
    {"symbol": "USD/CAD",  "name": "USDCAD",  "pip": 5},
    {"symbol": "USD/CHF",  "name": "USDCHF",  "pip": 5},
    {"symbol": "BTC/USD",  "name": "BTCUSD",  "pip": 2},
    {"symbol": "WTI/USD",  "name": "USOIL",   "pip": 2},
]

INTERVAL       = "1min"
SMA_PERIOD     = 20
ATR_PERIOD     = 14
ATR_SL_MULT    = 1.5
ATR_TP1_MULT   = 1.5
ATR_TP2_MULT   = 3.0
STRONG_THRESH  = 0.5
PAIR_DELAY     = 6     # seconds between each pair check (stay under rate limit)
LOOP_SLEEP     = 30    # seconds after full cycle before next round
NEWS_WARN_MINS = 30

GOLD_BULLISH_ON_BEAT = ["cpi", "inflation", "pce", "unemployment claims", "jobless", "core inflation"]
GOLD_BEARISH_ON_BEAT = ["non-farm", "nfp", "payroll", "gdp", "retail sales", "ism", "pmi", "interest rate", "fed", "fomc", "jobs"]
GOLD_KEYWORDS        = ["non-farm", "nfp", "interest rate", "fed", "fomc", "cpi", "inflation", "gdp",
                        "unemployment", "payroll", "pce", "powell", "gold", "xau", "jobs",
                        "retail sales", "ism", "pmi", "core"]


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

def get_prices(symbol, n: int = SMA_PERIOD + ATR_PERIOD + 5):
    if not TWELVE_API_KEY:
        return None
    try:
        resp = requests.get(
            "https://api.twelvedata.com/time_series",
            params={"symbol": symbol, "interval": INTERVAL, "outputsize": n, "apikey": TWELVE_API_KEY},
            timeout=10
        )
        data = resp.json()
        if data.get("status") == "error":
            print(f"[API error] {symbol}: {data.get('message')}")
            return None
        bars   = list(reversed(data["values"]))
        closes = [float(b["close"]) for b in bars]
        highs  = [float(b["high"])  for b in bars]
        lows   = [float(b["low"])   for b in bars]
        return closes, highs, lows
    except Exception as e:
        print(f"[Price fetch error] {symbol}: {e}")
        return None


# ── INDICATORS ────────────────────────────────────────────────────────────────

def sma(prices, period):
    return sum(prices[-period:]) / period

def atr(highs, lows, closes, period):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / period

def rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    return 100 - (100 / (1 + ag/al))


# ── HELPERS ───────────────────────────────────────────────────────────────────

def parse_number(val):
    if not val or str(val).strip() in ("", "N/A", "—"): return None
    val = str(val).strip().upper()
    mult = 1
    if val.endswith("K"): mult = 1_000; val = val[:-1]
    elif val.endswith("M"): mult = 1_000_000; val = val[:-1]
    elif val.endswith("B"): mult = 1_000_000_000; val = val[:-1]
    try: return float(val.replace("%","").replace(",","")) * mult
    except: return None

def predict_direction(title, f_num, p_num, a_num=None):
    t    = title.lower()
    bull = any(k in t for k in GOLD_BULLISH_ON_BEAT)
    bear = any(k in t for k in GOLD_BEARISH_ON_BEAT)
    if not bull and not bear:
        return "NEUTRAL", "LOW", "Unknown impact"
    if a_num is not None:
        base, cmp, label, conf = f_num or p_num, a_num, "Actual vs Forecast", "HIGH"
    elif f_num is not None and p_num is not None:
        base, cmp, label, conf = p_num, f_num, "Forecast vs Previous", "MEDIUM"
    else:
        return "NEUTRAL", "LOW", "Not enough data"
    if base is None or cmp is None:
        return "NEUTRAL", "LOW", "Could not parse values"
    if bull:
        if cmp > base: return "BUY",  conf, f"{label}: {cmp} > {base} → inflation/weak jobs = price ↑"
        if cmp < base: return "SELL", conf, f"{label}: {cmp} < {base} → low inflation = price ↓"
    if bear:
        if cmp > base: return "SELL", conf, f"{label}: {cmp} > {base} → strong USD = price ↓"
        if cmp < base: return "BUY",  conf, f"{label}: {cmp} < {base} → weak USD = price ↑"
    return "NEUTRAL", "LOW", "No clear direction"

def calc_levels(direction, price, cur_atr):
    pullback = cur_atr * 0.3
    sl_dist  = cur_atr * ATR_SL_MULT
    tp1_dist = cur_atr * ATR_TP1_MULT
    tp2_dist = cur_atr * ATR_TP2_MULT
    if direction == "BUY":
        entry = round(price - pullback, 2)
        return entry, round(entry-sl_dist,2), round(entry+tp1_dist,2), round(entry+tp2_dist,2)
    else:
        entry = round(price + pullback, 2)
        return entry, round(entry+sl_dist,2), round(entry-tp1_dist,2), round(entry-tp2_dist,2)

def format_signal(pair_name, direction, label, emoji, entry, sl, tp1, tp2, rsi_val, note):
    return (
        f"{emoji} {label}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Pair:    {pair_name}\n"
        f"Action:  {direction}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Entry:   {entry}\n"
        f"SL:      {sl}  (-{round(abs(entry-sl),2)})\n"
        f"TP1:     {tp1}  (+{round(abs(entry-tp1),2)})  🎯\n"
        f"TP2:     {tp2}  (+{round(abs(entry-tp2),2)})  🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"RSI:     {rsi_val:.1f}\n"
        f"Note:    {note}"
    )


# ── SIGNAL LOGIC PER PAIR ─────────────────────────────────────────────────────

last_signals = {}   # {pair_name: last_signal}

def check_pair(pair):
    name   = pair["name"]
    symbol = pair["symbol"]

    result = get_prices(symbol)
    if not result: return
    closes, highs, lows = result
    if len(closes) < SMA_PERIOD + ATR_PERIOD: return

    price     = closes[-1]
    prev      = closes[-2]
    cur_sma   = sma(closes, SMA_PERIOD)
    prev_sma  = sma(closes[:-1], SMA_PERIOD)
    cur_atr   = atr(highs, lows, closes, ATR_PERIOD)
    cur_rsi   = rsi(closes)
    is_strong = abs(price - prev) > (cur_atr * STRONG_THRESH)
    last      = last_signals.get(name)

    trend = "↑" if price > cur_sma else "↓"
    print(f"[{name}] {price}  SMA={cur_sma:.5g}  RSI={cur_rsi:.1f}  {trend}")

    sl_dist  = cur_atr * ATR_SL_MULT
    tp1_dist = cur_atr * ATR_TP1_MULT
    tp2_dist = cur_atr * ATR_TP2_MULT

    if prev <= prev_sma and price > cur_sma:
        if last != "BUY":
            last_signals[name] = "BUY"
            sl  = round(price - sl_dist, 5)
            tp1 = round(price + tp1_dist, 5)
            tp2 = round(price + tp2_dist, 5)
            if is_strong and cur_rsi < 55:
                label, emoji, note = "STRONG BUY SIGNAL", "🔥", "Strong momentum — full position ok"
            else:
                label, emoji, note = "WEAK BUY SIGNAL", "📈", "Low momentum — reduce size, wait for TP1 first"
            send(format_signal(name, "BUY", label, emoji, price, sl, tp1, tp2, cur_rsi, note))

    elif prev >= prev_sma and price < cur_sma:
        if last != "SELL":
            last_signals[name] = "SELL"
            sl  = round(price + sl_dist, 5)
            tp1 = round(price - tp1_dist, 5)
            tp2 = round(price - tp2_dist, 5)
            if is_strong and cur_rsi > 45:
                label, emoji, note = "STRONG SELL SIGNAL", "🔥", "Strong momentum — full position ok"
            else:
                label, emoji, note = "WEAK SELL SIGNAL", "📉", "Low momentum — reduce size, wait for TP1 first"
            send(format_signal(name, "SELL", label, emoji, price, sl, tp1, tp2, cur_rsi, note))

    elif is_strong:
        direction = "BUY" if price > cur_sma else "SELL"
        if last != f"TREND_{direction}":
            last_signals[name] = f"TREND_{direction}"
            emoji = "📈" if direction == "BUY" else "📉"
            send(
                f"{emoji} STRONG TREND — {direction}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Pair:   {name}\n"
                f"Price:  {price}\n"
                f"SMA:    {cur_sma:.5g}\n"
                f"RSI:    {cur_rsi:.1f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"No crossover yet — watch for entry on pullback"
            )


# ── NEWS CALENDAR ─────────────────────────────────────────────────────────────

alerted_news      = set()
post_news_alerted = set()

def fetch_news():
    try:
        return requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10
        ).json()
    except Exception as e:
        print(f"[News error] {e}")
        return []

def check_news():
    events = fetch_news()
    if not events: return
    now = datetime.now(timezone.utc)

    for event in events:
        if event.get("impact") != "High": continue
        if event.get("country", "").upper() != "USD": continue

        title    = event.get("title", "")
        date     = event.get("date", "")
        etime    = event.get("time", "")
        forecast = event.get("forecast", "")
        previous = event.get("previous", "")
        actual   = event.get("actual", "")

        try:
            event_dt = datetime.strptime(f"{date} {etime}", "%Y-%m-%d %I:%M%p").replace(tzinfo=timezone.utc)
        except:
            continue

        mins_away = (event_dt - now).total_seconds() / 60
        is_gold   = any(kw in title.lower() for kw in GOLD_KEYWORDS)
        key       = f"{date}_{etime}_{title}"
        f_num, p_num, a_num = parse_number(forecast), parse_number(previous), parse_number(actual)

        # Pre-news
        if 0 < mins_away <= NEWS_WARN_MINS and key not in alerted_news:
            alerted_news.add(key)
            direction, confidence, reason = predict_direction(title, f_num, p_num)
            dir_emoji = "📈" if direction == "BUY" else "📉" if direction == "SELL" else "➡️"
            conf_icon = "🟢" if confidence == "HIGH" else "🟡" if confidence == "MEDIUM" else "🔴"

            price_data = get_prices("XAU/USD")
            price_block = ""
            if price_data and direction != "NEUTRAL":
                closes, highs, lows = price_data
                cur_price = closes[-1]
                cur_atr   = atr(highs, lows, closes, ATR_PERIOD)
                entry, sl, tp1, tp2 = calc_levels(direction, cur_price, cur_atr)
                price_block = (
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"XAUUSD Now: {cur_price:.2f}\n"
                    f"Entry:  {entry:.2f}  (wait for pullback)\n"
                    f"SL:     {sl:.2f}\n"
                    f"TP1:    {tp1:.2f}  🎯\n"
                    f"TP2:    {tp2:.2f}  🚀\n"
                )

            send(
                f"📰 NEWS ALERT — {int(mins_away)}min away\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Event:     {title}\n"
                f"Time:      {etime} UTC\n"
                f"Forecast:  {forecast or 'N/A'}\n"
                f"Previous:  {previous or 'N/A'}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{'⚠️ MAJOR GOLD IMPACT' if is_gold else '⚠️ USD HIGH IMPACT'}\n"
                f"Direction: {dir_emoji} {direction}  ({conf_icon} {confidence})\n"
                f"Reason:    {reason}\n"
                f"{price_block}"
                f"⏸ Pause ALL pairs — enter after news settles"
            )

        # Post-news
        post_key = f"POST_{key}"
        if actual and mins_away <= 5 and post_key not in post_news_alerted:
            post_news_alerted.add(post_key)
            time.sleep(8)
            price_data = get_prices("XAU/USD")
            if not price_data: continue
            closes, highs, lows = price_data
            cur_price = closes[-1]
            cur_atr   = atr(highs, lows, closes, ATR_PERIOD)
            cur_rsi   = rsi(closes)
            direction, confidence, reason = predict_direction(title, f_num, p_num, a_num)
            entry, sl, tp1, tp2 = calc_levels(direction, cur_price, cur_atr)
            dir_emoji  = "📈" if direction == "BUY" else "📉" if direction == "SELL" else "➡️"
            action     = "🟢 BUY" if direction == "BUY" else "🔴 SELL" if direction == "SELL" else "⚪ STAND ASIDE"
            conf_icon  = "🟢" if confidence == "HIGH" else "🟡" if confidence == "MEDIUM" else "🔴"
            send(
                f"🚨 NEWS DROPPED — {title}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Actual:    {actual}\n"
                f"Forecast:  {forecast or 'N/A'}\n"
                f"Previous:  {previous or 'N/A'}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Analysis:  {reason}\n"
                f"Signal:    {dir_emoji} {action}  ({conf_icon} {confidence})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"XAUUSD:  {cur_price:.2f}  |  RSI: {cur_rsi:.1f}\n"
                f"Entry:   {entry:.2f}  (pullback zone)\n"
                f"SL:      {sl:.2f}  (-{round(abs(entry-sl),2)})\n"
                f"TP1:     {tp1:.2f}  (+{round(abs(entry-tp1),2)})  🎯\n"
                f"TP2:     {tp2:.2f}  (+{round(abs(entry-tp2),2)})  🚀\n"
                f"⚡ Wait for candle to close before entering"
            )


# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

def bot_loop():
    print("Bot started.")
    pair_names = ", ".join(p["name"] for p in PAIRS)
    send(
        f"🤖 Multi-Pair Signal Bot started\n"
        f"Pairs: {pair_names}\n"
        f"Strategy: {SMA_PERIOD}-SMA + ATR + RSI\n"
        f"News: pre + post release analysis"
    )

    while True:
        try:
            check_news()
            for pair in PAIRS:
                try:
                    check_pair(pair)
                except Exception as e:
                    print(f"[{pair['name']} error] {e}")
                time.sleep(PAIR_DELAY)  # stagger to avoid rate limit
        except Exception as e:
            print(f"[Loop error] {e}")
        time.sleep(LOOP_SLEEP)


# ── FLASK ─────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return "Bot running", 200

@app.route("/status")
def status():
    return {"signals": last_signals}, 200

def run():
    thread = threading.Thread(target=bot_loop, daemon=True)
    thread.start()
    port = int(os.getenv("PORT", 10000))
    print(f"Flask starting on port {port}...")
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    run()
