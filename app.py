"""
XAUUSD Auto-Trading Bot — MT5 + Signal Alerts
----------------------------------------------
Runs LOCALLY on Windows with MetaTrader5 installed.
Uses SMA crossover strategy with ATR-based SL/TP and position sizing.

Requirements:
    pip install MetaTrader5 requests flask

Environment variables (or fill directly for local use):
    DISCORD_WEBHOOK
    TELEGRAM_TOKEN
    CHAT_ID
    TWELVE_API_KEY      → free at twelvedata.com
    MT5_LOGIN           → your broker account number
    MT5_PASSWORD
    MT5_SERVER          → e.g. "ICMarkets-Demo"
    RISK_PERCENT        → % of balance per trade (default 1.0)
"""

import os
import time
import threading
import requests
import MetaTrader5 as mt5
from flask import Flask

app = Flask(__name__)

# ── CONFIG ──────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")
TWELVE_API_KEY  = os.getenv("TWELVE_API_KEY", "")

MT5_LOGIN       = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD    = os.getenv("MT5_PASSWORD", "")
MT5_SERVER      = os.getenv("MT5_SERVER", "")

SYMBOL          = "XAUUSD"
TWELVE_SYMBOL   = "XAU/USD"
INTERVAL        = "1min"
SMA_PERIOD      = 20
ATR_PERIOD      = 14
ATR_SL_MULT     = 1.5      # SL = ATR * 1.5
ATR_TP_MULT     = 3.0      # TP = ATR * 3.0  (1:2 R:R)
RISK_PERCENT    = float(os.getenv("RISK_PERCENT", "1.0"))
CHECK_EVERY     = 60       # seconds
MAGIC           = 234567   # unique ID for this bot's trades


# ── NOTIFICATIONS ────────────────────────────────────────────────────────────

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


# ── MT5 CONNECTION ───────────────────────────────────────────────────────────

def connect_mt5() -> bool:
    if not mt5.initialize():
        print(f"[MT5] initialize() failed: {mt5.last_error()}")
        return False
    if MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
        ok = mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
        if not ok:
            print(f"[MT5] login failed: {mt5.last_error()}")
            mt5.shutdown()
            return False
    info = mt5.account_info()
    if info is None:
        print("[MT5] Could not get account info.")
        return False
    print(f"[MT5] Connected — Account: {info.login} | Balance: {info.balance:.2f} {info.currency}")
    return True


# ── PRICE DATA (Twelve Data) ─────────────────────────────────────────────────

def get_prices(n: int = SMA_PERIOD + ATR_PERIOD + 5):
    if not TWELVE_API_KEY:
        print("[Error] TWELVE_API_KEY not set.")
        return None
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol":     TWELVE_SYMBOL,
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
        bars = list(reversed(data["values"]))  # oldest → newest
        closes = [float(b["close"]) for b in bars]
        highs  = [float(b["high"])  for b in bars]
        lows   = [float(b["low"])   for b in bars]
        return closes, highs, lows
    except Exception as e:
        print(f"[Price fetch error] {e}")
        return None


# ── INDICATORS ───────────────────────────────────────────────────────────────

def sma(prices: list, period: int) -> float:
    return sum(prices[-period:]) / period


def atr(highs: list, lows: list, closes: list, period: int) -> float:
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i]  - lows[i],
            abs(highs[i]  - closes[i - 1]),
            abs(lows[i]   - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


# ── POSITION SIZING ──────────────────────────────────────────────────────────

def calc_lot_size(sl_distance: float) -> float:
    """
    Risk RISK_PERCENT of account balance on this trade.
    sl_distance is in price units (e.g. 2.50 for $2.50 SL on gold).
    """
    info    = mt5.account_info()
    balance = info.balance
    risk_usd = balance * (RISK_PERCENT / 100)

    sym_info  = mt5.symbol_info(SYMBOL)
    tick_size = sym_info.trade_tick_size        # e.g. 0.01
    tick_val  = sym_info.trade_tick_value       # USD value per tick per lot
    lot_step  = sym_info.volume_step            # e.g. 0.01
    min_lot   = sym_info.volume_min
    max_lot   = sym_info.volume_max

    ticks_in_sl = sl_distance / tick_size
    risk_per_lot = ticks_in_sl * tick_val

    if risk_per_lot <= 0:
        return min_lot

    raw_lot = risk_usd / risk_per_lot
    lot = round(raw_lot / lot_step) * lot_step
    lot = max(min_lot, min(max_lot, lot))
    return round(lot, 2)


# ── TRADE MANAGEMENT ─────────────────────────────────────────────────────────

def get_open_position():
    """Return the bot's current open position, or None."""
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions:
        for p in positions:
            if p.magic == MAGIC:
                return p
    return None


def close_position(position):
    tick = mt5.symbol_info_tick(SYMBOL)
    if position.type == mt5.ORDER_TYPE_BUY:
        price     = tick.bid
        order_type = mt5.ORDER_TYPE_SELL
    else:
        price     = tick.ask
        order_type = mt5.ORDER_TYPE_BUY

    request = {
        "action":   mt5.TRADE_ACTION_DEAL,
        "symbol":   SYMBOL,
        "volume":   position.volume,
        "type":     order_type,
        "position": position.ticket,
        "price":    price,
        "magic":    MAGIC,
        "comment":  "bot_close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        send(f"✅ Closed #{position.ticket} | P&L: {result.profit:.2f}")
    else:
        send(f"❌ Close failed: {result.comment}")


def open_trade(direction: str, price: float, sl: float, tp: float, lot: float):
    tick      = mt5.symbol_info_tick(SYMBOL)
    entry     = tick.ask if direction == "BUY" else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

    request = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "symbol":      SYMBOL,
        "volume":      lot,
        "type":        order_type,
        "price":       entry,
        "sl":          round(sl, 2),
        "tp":          round(tp, 2),
        "magic":       MAGIC,
        "comment":     "bot_open",
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        send(
            f"{'📈' if direction == 'BUY' else '📉'} {direction} {SYMBOL} "
            f"@ {entry:.2f} | Lot: {lot} | SL: {sl:.2f} | TP: {tp:.2f}"
        )
    else:
        send(f"❌ Order failed ({direction}): {result.comment} [retcode {result.retcode}]")


# ── SIGNAL + EXECUTION ───────────────────────────────────────────────────────

last_signal = None


def check_and_trade():
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

    # ── BUY signal ──
    if prev_price <= prev_sma and current_price > current_sma:
        if last_signal != "BUY":
            last_signal = "BUY"
            position = get_open_position()

            if position and position.type == mt5.ORDER_TYPE_SELL:
                close_position(position)
                time.sleep(1)

            if not get_open_position():
                sl  = current_price - sl_dist
                tp  = current_price + tp_dist
                lot = calc_lot_size(sl_dist)
                open_trade("BUY", current_price, sl, tp, lot)

    # ── SELL signal ──
    elif prev_price >= prev_sma and current_price < current_sma:
        if last_signal != "SELL":
            last_signal = "SELL"
            position = get_open_position()

            if position and position.type == mt5.ORDER_TYPE_BUY:
                close_position(position)
                time.sleep(1)

            if not get_open_position():
                sl  = current_price + sl_dist
                tp  = current_price - tp_dist
                lot = calc_lot_size(sl_dist)
                open_trade("SELL", current_price, sl, tp, lot)

    else:
        pos = get_open_position()
        pos_info = f" | Open: #{pos.ticket} {('BUY' if pos.type == 0 else 'SELL')} {pos.volume}lot" if pos else ""
        print(f"[No signal] Price={current_price:.2f}  SMA={current_sma:.2f}  ATR={current_atr:.2f}{pos_info}")


# ── MAIN LOOP ────────────────────────────────────────────────────────────────

def bot_loop():
    print("Connecting to MT5…")
    if not connect_mt5():
        send("❌ Could not connect to MT5. Check credentials.")
        return

    info = mt5.account_info()
    send(
        f"🤖 Bot started\n"
        f"Account: {info.login} | Balance: {info.balance:.2f} {info.currency}\n"
        f"Symbol: {SYMBOL} | Strategy: {SMA_PERIOD}-SMA crossover\n"
        f"Risk per trade: {RISK_PERCENT}%"
    )

    while True:
        try:
            check_and_trade()
        except Exception as e:
            print(f"[Loop error] {e}")
        time.sleep(CHECK_EVERY)


# ── FLASK ────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return "Bot running", 200


@app.route("/status")
def status():
    pos = get_open_position()
    info = mt5.account_info()
    return {
        "balance":  info.balance if info else None,
        "equity":   info.equity  if info else None,
        "position": {
            "ticket":    pos.ticket,
            "type":      "BUY" if pos.type == 0 else "SELL",
            "volume":    pos.volume,
            "open_price": pos.price_open,
            "profit":    pos.profit,
        } if pos else None
    }, 200


def run():
    thread = threading.Thread(target=bot_loop, daemon=True)
    thread.start()
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()
