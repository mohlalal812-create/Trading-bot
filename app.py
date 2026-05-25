from flask import Flask
import threading
import time
import requests
import os

app = Flask(__LUCAS__)

# --- CONFIG (set these as environment variables, never hardcode) ---
DISCORD_WEBHOOK = os.getenv("LUASSON_WEBHOOK", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")
TWELVE_API_KEY  = os.getenv("TWELVE_API_KEY", "")   # free at twelvedata.com

SYMBOL          = "XAU/USD"
INTERVAL        = "1min"
SMA_PERIOD      = 20        # periods for simple moving average
CHECK_EVERY     = 60        # seconds between checks


# --- NOTIFICATIONS ---

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


# --- PRICE DATA (Twelve Data free tier) ---

def get_prices(n: int = SMA_PERIOD + 1):
    """
    Fetch the last `n` closing prices for XAUUSD from Twelve Data.
    Returns a list of floats (oldest → newest), or None on failure.
    """
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

        closes = [float(bar["close"]) for bar in reversed(data["values"])]
        return closes

    except Exception as e:
        print(f"[Price fetch error] {e}")
        return None


# --- STRATEGY: SMA crossover ---

def sma(prices: list, period: int) -> float:
    return sum(prices[-period:]) / period


last_signal = None   # track last signal so we don't spam


def check_signal():
    global last_signal

    prices = get_prices(SMA_PERIOD + 1)
    if not prices or len(prices) < SMA_PERIOD + 1:
        print("[Warning] Not enough price data.")
        return

    current_price = prices[-1]
    prev_price    = prices[-2]
    current_sma   = sma(prices, SMA_PERIOD)
    prev_sma      = sma(prices[:-1], SMA_PERIOD)

    # Crossover: price crosses ABOVE sma → BUY
    if prev_price <= prev_sma and current_price > current_sma:
        signal = f"📈 BUY  {SYMBOL} @ {current_price:.2f}  (price crossed above {SMA_PERIOD}-SMA {current_sma:.2f})"
        if last_signal != "BUY":
            send(signal)
            last_signal = "BUY"

    # Crossover: price crosses BELOW sma → SELL
    elif prev_price >= prev_sma and current_price < current_sma:
        signal = f"📉 SELL {SYMBOL} @ {current_price:.2f}  (price crossed below {SMA_PERIOD}-SMA {current_sma:.2f})"
        if last_signal != "SELL":
            send(signal)
            last_signal = "SELL"

    else:
        print(f"[No signal] Price={current_price:.2f}  SMA={current_sma:.2f}")


# --- MAIN LOOP ---

def bot_loop():
    print("Bot started. Checking every", CHECK_EVERY, "seconds.")
    send(f"🤖 XAUUSD bot started — monitoring {SYMBOL} with {SMA_PERIOD}-period SMA.")

    while True:
        try:
            check_signal()
        except Exception as e:
            print(f"[Loop error] {e}")

        time.sleep(CHECK_EVERY)


# --- FLASK ---

@app.route("/")
def home():
    return "Bot is running", 200


@app.route("/health")
def health():
    return {"status": "ok", "symbol": SYMBOL}, 200


def run():
    thread = threading.Thread(target=bot_loop, daemon=True)
    thread.start()

    port = int(os.getenv("PORT", 10000))
    print(f"Flask starting on port {port}…")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()
