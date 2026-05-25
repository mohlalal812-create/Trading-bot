"""
XAUUSD Auto-Trading Bot — FIXED VERSION
---------------------------------------
SMA crossover + ATR SL/TP
Includes safety filters: spread check, cooldown, MT5 protection

Requirements:
pip install MetaTrader5 requests flask
"""

import os
import time
import threading
import requests
import MetaTrader5 as mt5
from flask import Flask

app = Flask(__name__)

# ───────────────── CONFIG ─────────────────
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")
TWELVE_API_KEY  = os.getenv("TWELVE_API_KEY", "")

MT5_LOGIN       = os.getenv("MT5_LOGIN")
MT5_PASSWORD    = os.getenv("MT5_PASSWORD")
MT5_SERVER      = os.getenv("MT5_SERVER")

if not MT5_LOGIN or not MT5_PASSWORD or not MT5_SERVER:
    raise ValueError("Missing MT5 credentials")

MT5_LOGIN = int(MT5_LOGIN)

SYMBOL          = "XAUUSD"
TWELVE_SYMBOL   = "XAU/USD"
INTERVAL        = "1min"

SMA_PERIOD      = 20
ATR_PERIOD      = 14

ATR_SL_MULT     = 1.5
ATR_TP_MULT     = 3.0

RISK_PERCENT    = float(os.getenv("RISK_PERCENT", "1.0"))
CHECK_EVERY     = 60
MAGIC           = 234567

# ───────────── STATE ─────────────
last_signal = None
last_trade_time = 0
COOLDOWN = 180

app = Flask(__name__)

# ───────────── NOTIFY ─────────────
def send(msg: str):
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


# ───────────── MT5 CONNECT ─────────────
def connect_mt5():
    if not mt5.initialize():
        print(mt5.last_error())
        return False

    ok = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if not ok:
        print("Login failed:", mt5.last_error())
        return False

    info = mt5.account_info()
    print(f"Connected: {info.login} Balance={info.balance}")
    return True


# ───────────── PRICE DATA ─────────────
def get_prices(n=100):
    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": TWELVE_SYMBOL,
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


# ───────────── INDICATORS ─────────────
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


# ───────────── SAFETY ─────────────
def trading_allowed():
    t = mt5.terminal_info()
    return t and t.trade_allowed


def spread_ok():
    tick = mt5.symbol_info_tick(SYMBOL)
    info = mt5.symbol_info(SYMBOL)

    if not tick or not info:
        return False

    spread = tick.ask - tick.bid
    return spread < (info.point * 50)


# ───────────── POSITION ─────────────
def get_open_position():
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return None

    for p in positions:
        if p.magic == MAGIC:
            return p
    return None


def close_position(position):
    tick = mt5.symbol_info_tick(SYMBOL)

    if position.type == mt5.ORDER_TYPE_BUY:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": position.volume,
        "type": order_type,
        "position": position.ticket,
        "price": price,
        "magic": MAGIC,
        "comment": "close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    mt5.order_send(request)


# ───────────── LOT SIZE ─────────────
def calc_lot_size(sl_distance):
    info = mt5.account_info()
    balance = info.balance

    risk = balance * (RISK_PERCENT / 100)

    sym = mt5.symbol_info(SYMBOL)

    tick_value = sym.trade_tick_value
    tick_size  = sym.trade_tick_size

    risk_per_lot = (sl_distance / tick_size) * tick_value

    if risk_per_lot <= 0:
        return sym.volume_min

    lot = risk / risk_per_lot
    lot = round(lot, 2)

    return max(sym.volume_min, min(lot, sym.volume_max))


# ───────────── TRADE ─────────────
def open_trade(direction, sl, tp, lot):
    tick = mt5.symbol_info_tick(SYMBOL)

    if direction == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "magic": MAGIC,
        "comment": "bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    mt5.order_send(request)
    send(f"{direction} {SYMBOL} | Lot {lot} | SL {sl:.2f} TP {tp:.2f}")


# ───────────── STRATEGY ─────────────
def check_and_trade():
    global last_signal, last_trade_time

    if not trading_allowed():
        return

    if not spread_ok():
        return

    if time.time() - last_trade_time < COOLDOWN:
        return

    data = get_prices()
    if not data:
        return

    closes, highs, lows = data

    if len(closes) < SMA_PERIOD + ATR_PERIOD:
        return

    price = closes[-1]
    prev = closes[-2]

    sma_now = sma(closes, SMA_PERIOD)
    sma_prev = sma(closes[:-1], SMA_PERIOD)

    atr_val = atr(highs, lows, closes, ATR_PERIOD)

    sl_dist = atr_val * ATR_SL_MULT
    tp_dist = atr_val * ATR_TP_MULT

    pos = get_open_position()

    # BUY
    if prev <= sma_prev and price > sma_now:
        if last_signal != "BUY":
            last_signal = "BUY"

            if pos and pos.type == mt5.ORDER_TYPE_SELL:
                close_position(pos)
                time.sleep(1)

            if not get_open_position():
                open_trade("BUY", price - sl_dist, price + tp_dist, calc_lot_size(sl_dist))
                last_trade_time = time.time()

    # SELL
    elif prev >= sma_prev and price < sma_now:
        if last_signal != "SELL":
            last_signal = "SELL"

            if pos and pos.type == mt5.ORDER_TYPE_BUY:
                close_position(pos)
                time.sleep(1)

            if not get_open_position():
                open_trade("SELL", price + sl_dist, price - tp_dist, calc_lot_size(sl_dist))
                last_trade_time = time.time()


# ───────────── LOOP ─────────────
def bot_loop():
    if not connect_mt5():
        return

    send("Bot started")

    while True:
        try:
            check_and_trade()
        except Exception as e:
            print(e)

        time.sleep(CHECK_EVERY)


# ───────────── FLASK ─────────────
@app.route("/")
def home():
    return "Running"


@app.route("/status")
def status():
    pos = get_open_position()
    info = mt5.account_info()

    return {
        "balance": info.balance if info else None,
        "position": pos.ticket if pos else None
    }


# ───────────── START ─────────────
def run():
    threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))


if __name__ == "__main__":
    run()
