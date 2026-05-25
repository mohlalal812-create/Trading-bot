import sys, os
sys.stdout = sys.stderr
os.environ["PYTHONUNBUFFERED"] = "1"

from flask import Flask, jsonify
import threading, time, requests, math
from datetime import datetime, timezone

app = Flask(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1508337970099388417/WCRz7Gv0qK7B2rW0Gpy_6W486j5_vigNxhqM3eRuMVeeOZ1V--IeT35EEEUxe-i_zvkx"
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID         = os.getenv("CHAT_ID", "")
TWELVE_API_KEY  = "286312c01d50410a9ee56a863143ad0f"

PAIRS = [
    {"symbol": "XAU/USD", "name": "XAUUSD"},
    {"symbol": "EUR/USD", "name": "EURUSD"},
    {"symbol": "GBP/USD", "name": "GBPUSD"},
    {"symbol": "USD/JPY", "name": "USDJPY"},
]

# Indicator settings
EMA_FAST       = 9
EMA_SLOW       = 21
MACD_FAST      = 12
MACD_SLOW      = 26
MACD_SIGNAL    = 9
BB_PERIOD      = 20
BB_STD         = 2.0
ATR_PERIOD     = 14
ATR_SL_MULT    = 1.5
ATR_TP1_MULT   = 1.5
ATR_TP2_MULT   = 3.0
SR_LOOKBACK    = 50    # candles to detect S/R
SR_ZONE        = 0.3   # ATR multiplier for S/R zone width

PAIR_DELAY     = 15    # seconds between pairs
LOOP_SLEEP     = 60
NEWS_WARN_MINS = 30

GOLD_BULLISH_ON_BEAT = ["cpi","inflation","pce","unemployment claims","jobless","core inflation"]
GOLD_BEARISH_ON_BEAT = ["non-farm","nfp","payroll","gdp","retail sales","ism","pmi","interest rate","fed","fomc","jobs"]
GOLD_KEYWORDS        = ["non-farm","nfp","interest rate","fed","fomc","cpi","inflation","gdp",
                        "unemployment","payroll","pce","powell","gold","xau","jobs",
                        "retail sales","ism","pmi","core"]


# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────
def send(msg):
    print(msg)
    if DISCORD_WEBHOOK:
        try: requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
        except Exception as e: print(f"[Discord error] {e}")
    if TELEGRAM_TOKEN and CHAT_ID:
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                           data={"chat_id": CHAT_ID, "text": msg}, timeout=5)
        except Exception as e: print(f"[Telegram error] {e}")


# ── PRICE DATA ────────────────────────────────────────────────────────────────
def get_prices(symbol, interval="1min", n=80):
    try:
        resp = requests.get("https://api.twelvedata.com/time_series",
            params={"symbol": symbol, "interval": interval,
                    "outputsize": n, "apikey": TWELVE_API_KEY}, timeout=10)
        data = resp.json()
        if data.get("status") == "error":
            print(f"[API error] {symbol}/{interval}: {data.get('message')}")
            return None
        bars   = list(reversed(data["values"]))
        closes = [float(b["close"]) for b in bars]
        highs  = [float(b["high"])  for b in bars]
        lows   = [float(b["low"])   for b in bars]
        return closes, highs, lows
    except Exception as e:
        print(f"[Fetch error] {symbol}: {e}")
        return None


# ── INDICATORS ────────────────────────────────────────────────────────────────
def ema(prices, period):
    k, e = 2/(period+1), prices[0]
    for p in prices[1:]: e = p*k + e*(1-k)
    return e

def ema_series(prices, period):
    k, result = 2/(period+1), [prices[0]]
    for p in prices[1:]: result.append(p*k + result[-1]*(1-k))
    return result

def macd(closes):
    fast   = ema_series(closes, MACD_FAST)
    slow   = ema_series(closes, MACD_SLOW)
    macd_l = [f-s for f,s in zip(fast, slow)]
    signal = ema_series(macd_l, MACD_SIGNAL)
    hist   = [m-s for m,s in zip(macd_l, signal)]
    return macd_l[-1], signal[-1], hist[-1], macd_l[-2], signal[-2]

def bollinger(closes, period=BB_PERIOD, std_mult=BB_STD):
    recent = closes[-period:]
    mid    = sum(recent)/period
    std    = math.sqrt(sum((x-mid)**2 for x in recent)/period)
    return mid, mid + std_mult*std, mid - std_mult*std   # mid, upper, lower

def atr_val(highs, lows, closes, period=ATR_PERIOD):
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    return sum(trs[-period:]) / period

def rsi_val(closes, period=14):
    gains = [max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
    losses= [max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
    ag, al = sum(gains[-period:])/period, sum(losses[-period:])/period
    if al == 0: return 100
    return 100 - (100/(1+ag/al))

def support_resistance(highs, lows, closes, cur_atr, lookback=SR_LOOKBACK):
    """Find recent swing highs/lows as S/R levels."""
    h = highs[-lookback:]
    l = lows[-lookback:]
    levels = []
    for i in range(2, len(h)-2):
        if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
            levels.append(("R", h[i]))
        if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
            levels.append(("S", l[i]))
    return levels

def near_sr(price, levels, cur_atr):
    """Check if price is near a support or resistance level."""
    zone = cur_atr * SR_ZONE
    for kind, level in levels:
        if abs(price - level) <= zone:
            return kind, level
    return None, None


# ── SIGNAL SCORING ────────────────────────────────────────────────────────────
def score_signal(direction, closes_1m, highs_1m, lows_1m, closes_5m, highs_5m, lows_5m):
    """
    Score a potential signal 0-10 using multiple confirmations.
    Returns (score, breakdown_dict)
    """
    score    = 0
    breakdown = {}
    price    = closes_1m[-1]
    cur_atr  = atr_val(highs_1m, lows_1m, closes_1m)

    # 1. EMA crossover (1min) — 2 pts
    fast_now  = ema(closes_1m[-EMA_FAST*3:], EMA_FAST)
    slow_now  = ema(closes_1m[-EMA_SLOW*3:], EMA_SLOW)
    fast_prev = ema(closes_1m[-EMA_FAST*3-1:-1], EMA_FAST)
    slow_prev = ema(closes_1m[-EMA_SLOW*3-1:-1], EMA_SLOW)
    ema_cross = (direction=="BUY" and fast_now > slow_now and fast_prev <= slow_prev) or \
                (direction=="SELL" and fast_now < slow_now and fast_prev >= slow_prev)
    ema_agree = (direction=="BUY" and fast_now > slow_now) or \
                (direction=="SELL" and fast_now < slow_now)
    if ema_cross:
        score += 2; breakdown["EMA"] = "✅ Crossover confirmed"
    elif ema_agree:
        score += 1; breakdown["EMA"] = "🟡 EMA aligned"
    else:
        breakdown["EMA"] = "❌ EMA against signal"

    # 2. MACD (1min) — 2 pts
    m_val, m_sig, m_hist, m_val_prev, m_sig_prev = macd(closes_1m)
    macd_cross = (direction=="BUY"  and m_val > m_sig and m_val_prev <= m_sig_prev) or \
                 (direction=="SELL" and m_val < m_sig and m_val_prev >= m_sig_prev)
    macd_agree = (direction=="BUY"  and m_hist > 0) or (direction=="SELL" and m_hist < 0)
    if macd_cross:
        score += 2; breakdown["MACD"] = "✅ MACD crossover"
    elif macd_agree:
        score += 1; breakdown["MACD"] = "🟡 MACD aligned"
    else:
        breakdown["MACD"] = "❌ MACD against signal"

    # 3. Bollinger Bands (1min) — 1 pt
    bb_mid, bb_upper, bb_lower = bollinger(closes_1m)
    if direction == "BUY"  and price <= bb_lower:
        score += 1; breakdown["BB"] = f"✅ Price at lower band ({bb_lower:.4g})"
    elif direction == "SELL" and price >= bb_upper:
        score += 1; breakdown["BB"] = f"✅ Price at upper band ({bb_upper:.4g})"
    elif direction == "BUY"  and price < bb_mid:
        score += 0; breakdown["BB"] = f"🟡 Price below midband"
    else:
        breakdown["BB"] = "➡️ BB neutral"

    # 4. RSI filter — 1 pt
    r = rsi_val(closes_1m)
    if direction == "BUY"  and r < 45:
        score += 1; breakdown["RSI"] = f"✅ RSI oversold ({r:.1f})"
    elif direction == "SELL" and r > 55:
        score += 1; breakdown["RSI"] = f"✅ RSI overbought ({r:.1f})"
    else:
        breakdown["RSI"] = f"🟡 RSI neutral ({r:.1f})"

    # 5. Multi-timeframe (5min) — 2 pts
    if closes_5m:
        fast_5m = ema(closes_5m[-EMA_FAST*3:], EMA_FAST)
        slow_5m = ema(closes_5m[-EMA_SLOW*3:], EMA_SLOW)
        _, _, hist_5m, _, _ = macd(closes_5m)
        tf_agree = ((direction=="BUY"  and fast_5m > slow_5m and hist_5m > 0) or
                    (direction=="SELL" and fast_5m < slow_5m and hist_5m < 0))
        if tf_agree:
            score += 2; breakdown["5MIN"] = "✅ 5min confirms direction"
        else:
            breakdown["5MIN"] = "❌ 5min disagrees"
    else:
        breakdown["5MIN"] = "⚠️ 5min data unavailable"

    # 6. Support/Resistance — 1 pt
    levels = support_resistance(highs_1m, lows_1m, closes_1m, cur_atr)
    sr_kind, sr_level = near_sr(price, levels, cur_atr)
    if direction == "BUY"  and sr_kind == "S":
        score += 1; breakdown["S/R"] = f"✅ Near support ({sr_level:.4g})"
    elif direction == "SELL" and sr_kind == "R":
        score += 1; breakdown["S/R"] = f"✅ Near resistance ({sr_level:.4g})"
    elif sr_kind:
        breakdown["S/R"] = f"⚠️ Near {sr_kind} level ({sr_level:.4g})"
    else:
        breakdown["S/R"] = "➡️ No nearby S/R"

    return score, breakdown, r


def score_label(score):
    if score >= 8: return "🔥 STRONG",  "Full position ok — high confidence"
    if score >= 5: return "📊 MEDIUM",  "Half position — wait for TP1 before adding"
    return              "⚠️ WEAK",    "Small size only — low confluence"


# ── WIN RATE TRACKER ──────────────────────────────────────────────────────────
pending_signals = {}   # {pair: {direction, entry, sl, tp1, tp2, time}}
win_rate_stats  = {}   # {pair: {wins, losses, tp1_hits}}

def update_win_rate(name, closes):
    if name not in pending_signals: return
    sig   = pending_signals[name]
    price = closes[-1]
    stats = win_rate_stats.setdefault(name, {"wins":0,"losses":0,"tp1":0})

    if sig["direction"] == "BUY":
        if price >= sig["tp2"]:
            stats["wins"] += 1; stats["tp1"] += 1
            send(f"✅ TP2 HIT — {name} | +{round(sig['tp2']-sig['entry'],2)}")
            del pending_signals[name]
        elif price >= sig["tp1"] and not sig.get("tp1_hit"):
            stats["tp1"] += 1; sig["tp1_hit"] = True
            send(f"🎯 TP1 HIT — {name} | +{round(sig['tp1']-sig['entry'],2)} | Move SL to entry")
        elif price <= sig["sl"]:
            stats["losses"] += 1
            send(f"❌ SL HIT — {name} | -{round(sig['entry']-sig['sl'],2)}")
            del pending_signals[name]
    else:
        if price <= sig["tp2"]:
            stats["wins"] += 1; stats["tp1"] += 1
            send(f"✅ TP2 HIT — {name} | +{round(sig['entry']-sig['tp2'],2)}")
            del pending_signals[name]
        elif price <= sig["tp1"] and not sig.get("tp1_hit"):
            stats["tp1"] += 1; sig["tp1_hit"] = True
            send(f"🎯 TP1 HIT — {name} | +{round(sig['entry']-sig['tp1'],2)} | Move SL to entry")
        elif price >= sig["sl"]:
            stats["losses"] += 1
            send(f"❌ SL HIT — {name} | -{round(sig['sl']-sig['entry'],2)}")
            del pending_signals[name]


# ── SIGNAL LOGIC ──────────────────────────────────────────────────────────────
last_signals = {}

def check_pair(pair):
    global last_update_time
    name, symbol = pair["name"], pair["symbol"]

    # Fetch 1min data
    d1 = get_prices(symbol, "1min", 80)
    if not d1: return
    closes_1m, highs_1m, lows_1m = d1

    # Update win tracker
    update_win_rate(name, closes_1m)

    # 5min fetch disabled to stay within free API rate limit
    closes_5m, highs_5m, lows_5m = None, None, None

    price    = closes_1m[-1]
    prev     = closes_1m[-2]
    cur_atr  = atr_val(highs_1m, lows_1m, closes_1m)
    cur_rsi  = rsi_val(closes_1m)

    fast_now  = ema(closes_1m[-EMA_FAST*3:], EMA_FAST)
    slow_now  = ema(closes_1m[-EMA_SLOW*3:], EMA_SLOW)
    fast_prev = ema(closes_1m[-EMA_FAST*3-1:-1], EMA_FAST)
    slow_prev = ema(closes_1m[-EMA_SLOW*3-1:-1], EMA_SLOW)

    trend = "↑" if fast_now > slow_now else "↓"
    print(f"[{name}] {price}  EMA9={fast_now:.5g}  EMA21={slow_now:.5g}  RSI={cur_rsi:.1f}  {trend}")

    now_ts = time.time()
    if now_ts - last_update_time >= 1800:
        last_update_time = now_ts
        lines = []
        for p in PAIRS:
            pname = p["name"]
            ls = last_signals.get(pname, "none")
            ps = win_rate_stats.get(pname, {})
            total = ps.get("wins", 0) + ps.get("losses", 0)
            wr = f"{int(ps.get('wins',0)/total*100)}%" if total > 0 else "N/A"
            lines.append(f"{pname}: {ls.upper()} | WR: {wr}")
        send("📊 HOURLY UPDATE\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines))

    # Detect crossover OR strong trend
    direction = None
    gap = abs(fast_now - slow_now)
    strong_gap = gap > (cur_atr * 0.1)

    if fast_prev <= slow_prev and fast_now > slow_now:
        direction = "BUY"   # fresh crossover
    elif fast_prev >= slow_prev and fast_now < slow_now:
        direction = "SELL"  # fresh crossover
    elif fast_now > slow_now and strong_gap:
        direction = "BUY"   # strong uptrend
    elif fast_now < slow_now and strong_gap:
        direction = "SELL"  # strong downtrend

    if not direction or last_signals.get(name) == direction:
        return

    # Score the signal
    score, breakdown, r = score_signal(
        direction, closes_1m, highs_1m, lows_1m,
        closes_5m, highs_5m, lows_5m
    )

    # Only send if score >= 3 (filter out very weak signals)
    if score < 2:
        print(f"[{name}] Signal filtered out — score {score}/9")
        return

    last_signals[name] = direction

    sl_dist  = cur_atr * ATR_SL_MULT
    tp1_dist = cur_atr * ATR_TP1_MULT
    tp2_dist = cur_atr * ATR_TP2_MULT

    if direction == "BUY":
        sl  = round(price - sl_dist, 5)
        tp1 = round(price + tp1_dist, 5)
        tp2 = round(price + tp2_dist, 5)
    else:
        sl  = round(price + sl_dist, 5)
        tp1 = round(price - tp1_dist, 5)
        tp2 = round(price - tp2_dist, 5)

    # Store for win rate tracking
    pending_signals[name] = {
        "direction": direction, "entry": price,
        "sl": sl, "tp1": tp1, "tp2": tp2, "tp1_hit": False
    }

    s_label, s_note = score_label(score)
    emoji = "📈" if direction == "BUY" else "📉"

    # Build breakdown string
    bd_str = "\n".join(f"  {v}" for v in breakdown.values())

    # Win rate string
    stats = win_rate_stats.get(name, {})
    total = stats.get("wins", 0) + stats.get("losses", 0)
    wr_str = f"{stats['wins']}/{total} ({int(stats['wins']/total*100)}% WR)" if total > 0 else "New"

    send(
        f"{emoji} {s_label} — {direction} {name}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Score:   {score}/9\n"
        f"Entry:   {price}\n"
        f"SL:      {sl}  (-{round(abs(price-sl),5)})\n"
        f"TP1:     {tp1}  (+{round(abs(price-tp1),5)})  🎯\n"
        f"TP2:     {tp2}  (+{round(abs(price-tp2),5)})  🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"RSI:     {r:.1f}\n"
        f"Win Rate: {wr_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Confluences:\n{bd_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Note: {s_note}"
    )


# ── NEWS ──────────────────────────────────────────────────────────────────────
alerted_news, post_news_alerted = set(), set()

def parse_number(val):
    if not val or str(val).strip() in ("","N/A","—"): return None
    val = str(val).strip().upper()
    mult = 1
    if val.endswith("K"): mult=1_000; val=val[:-1]
    elif val.endswith("M"): mult=1_000_000; val=val[:-1]
    elif val.endswith("B"): mult=1_000_000_000; val=val[:-1]
    try: return float(val.replace("%","").replace(",","")) * mult
    except: return None

def predict_direction(title, f_num, p_num, a_num=None):
    t = title.lower()
    bull = any(k in t for k in GOLD_BULLISH_ON_BEAT)
    bear = any(k in t for k in GOLD_BEARISH_ON_BEAT)
    if not bull and not bear: return "NEUTRAL","LOW","Unknown impact"
    if a_num is not None: base,cmp,label,conf = f_num or p_num,a_num,"Actual vs Forecast","HIGH"
    elif f_num and p_num: base,cmp,label,conf = p_num,f_num,"Forecast vs Previous","MEDIUM"
    else: return "NEUTRAL","LOW","Not enough data"
    if base is None or cmp is None: return "NEUTRAL","LOW","Could not parse"
    if bull:
        if cmp>base: return "BUY",conf,f"{label}: {cmp}>{base} → inflation/weak jobs = price ↑"
        if cmp<base: return "SELL",conf,f"{label}: {cmp}<{base} → low inflation = price ↓"
    if bear:
        if cmp>base: return "SELL",conf,f"{label}: {cmp}>{base} → strong USD = price ↓"
        if cmp<base: return "BUY",conf,f"{label}: {cmp}<{base} → weak USD = price ↑"
    return "NEUTRAL","LOW","No clear direction"

def calc_levels(direction, price, cur_atr):
    pb = cur_atr*0.3
    s,t1,t2 = cur_atr*ATR_SL_MULT, cur_atr*ATR_TP1_MULT, cur_atr*ATR_TP2_MULT
    if direction=="BUY":
        e=round(price-pb,2); return e,round(e-s,2),round(e+t1,2),round(e+t2,2)
    else:
        e=round(price+pb,2); return e,round(e+s,2),round(e-t1,2),round(e-t2,2)

def check_news():
    try: events = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json",timeout=10).json()
    except: return
    now = datetime.now(timezone.utc)
    for event in events:
        if event.get("impact")!="High" or event.get("country","").upper()!="USD": continue
        title,date,etime = event.get("title",""),event.get("date",""),event.get("time","")
        forecast,previous,actual = event.get("forecast",""),event.get("previous",""),event.get("actual","")
        try: event_dt = datetime.strptime(f"{date} {etime}","%Y-%m-%d %I:%M%p").replace(tzinfo=timezone.utc)
        except: continue
        mins_away = (event_dt-now).total_seconds()/60
        is_gold = any(kw in title.lower() for kw in GOLD_KEYWORDS)
        key = f"{date}_{etime}_{title}"
        f_num,p_num,a_num = parse_number(forecast),parse_number(previous),parse_number(actual)

        if 0<mins_away<=NEWS_WARN_MINS and key not in alerted_news:
            alerted_news.add(key)
            direction,confidence,reason = predict_direction(title,f_num,p_num)
            dir_emoji = "📈" if direction=="BUY" else "📉" if direction=="SELL" else "➡️"
            conf_icon = "🟢" if confidence=="HIGH" else "🟡" if confidence=="MEDIUM" else "🔴"
            pd = get_prices("XAU/USD"); pb=""
            if pd and direction!="NEUTRAL":
                c,h,l=pd; cp=c[-1]; ca=atr_val(h,l,c)
                en,sl,tp1,tp2=calc_levels(direction,cp,ca)
                pb=(f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"XAUUSD: {cp:.2f}\nEntry: {en}  SL: {sl}\nTP1: {tp1} 🎯  TP2: {tp2} 🚀\n")
            send(f"📰 NEWS — {int(mins_away)}min away\n━━━━━━━━━━━━━━━━━━━━\n"
                 f"Event: {title}\nTime: {etime} UTC\nForecast: {forecast or 'N/A'}  Previous: {previous or 'N/A'}\n"
                 f"━━━━━━━━━━━━━━━━━━━━\n{'⚠️ MAJOR GOLD IMPACT' if is_gold else '⚠️ USD IMPACT'}\n"
                 f"Direction: {dir_emoji} {direction}  ({conf_icon} {confidence})\nReason: {reason}\n{pb}"
                 f"⏸ Pause ALL pairs until news settles")

        post_key = f"POST_{key}"
        if actual and mins_away<=5 and post_key not in post_news_alerted:
            post_news_alerted.add(post_key)
            time.sleep(8)
            pd=get_prices("XAU/USD")
            if not pd: continue
            c,h,l=pd; cp=c[-1]; ca=atr_val(h,l,c); cr=rsi_val(c)
            direction,confidence,reason=predict_direction(title,f_num,p_num,a_num)
            en,sl,tp1,tp2=calc_levels(direction,cp,ca)
            dir_emoji="📈" if direction=="BUY" else "📉" if direction=="SELL" else "➡️"
            action="🟢 BUY" if direction=="BUY" else "🔴 SELL" if direction=="SELL" else "⚪ STAND ASIDE"
            conf_icon="🟢" if confidence=="HIGH" else "🟡" if confidence=="MEDIUM" else "🔴"
            send(f"🚨 NEWS DROPPED — {title}\n━━━━━━━━━━━━━━━━━━━━\n"
                 f"Actual: {actual}  Forecast: {forecast or 'N/A'}  Previous: {previous or 'N/A'}\n"
                 f"━━━━━━━━━━━━━━━━━━━━\nAnalysis: {reason}\nSignal: {dir_emoji} {action}  ({conf_icon} {confidence})\n"
                 f"━━━━━━━━━━━━━━━━━━━━\nXAUUSD: {cp:.2f}  RSI: {cr:.1f}\n"
                 f"Entry: {en}  SL: {sl}  (-{round(abs(en-sl),2)})\n"
                 f"TP1: {tp1}  (+{round(abs(en-tp1),2)}) 🎯\nTP2: {tp2}  (+{round(abs(en-tp2),2)}) 🚀\n"
                 f"⚡ Wait for candle to close before entering")



# ── TRADING ECONOMICS NEWS ────────────────────────────────────────────────────
from bs4 import BeautifulSoup

te_seen_headlines = set()

TE_KEYWORDS = [
    "gold", "xau", "silver", "xag", "dollar", "usd", "fed", "federal reserve",
    "interest rate", "inflation", "cpi", "gdp", "payroll", "nfp", "fomc",
    "powell", "oil", "crude", "euro", "pound", "yen", "bitcoin", "btc",
    "recession", "rate hike", "rate cut", "treasury", "bond", "jobs"
]

BULLISH_WORDS = ["rise", "rises", "rose", "surges", "jumps", "gains", "rally",
                 "climbs", "higher", "beats", "strong", "hawkish", "rate hike",
                 "above forecast", "better than expected"]

BEARISH_WORDS = ["fall", "falls", "fell", "drops", "decline", "slips", "lower",
                 "misses", "weak", "dovish", "rate cut", "below forecast",
                 "worse than expected", "recession", "slowdown"]

def te_sentiment(headline):
    h = headline.lower()
    bull = sum(1 for w in BULLISH_WORDS if w in h)
    bear = sum(1 for w in BEARISH_WORDS if w in h)
    if bull > bear:   return "📈 Bullish", "🟢"
    if bear > bull:   return "📉 Bearish", "🔴"
    return "➡️ Neutral", "🟡"

def check_te_news():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get("https://tradingeconomics.com/stream", headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "lxml")
        items = soup.select("div.te-stream-item, article, div.stream-item, div[class*='stream']")

        if not items:
            # fallback — try finding any news-like divs
            items = soup.find_all("div", class_=lambda c: c and "stream" in c.lower())

        new_count = 0
        for item in items[:20]:
            headline = item.get_text(separator=" ", strip=True)
            if not headline or len(headline) < 20:
                continue

            # Filter for relevant keywords
            h_lower = headline.lower()
            if not any(kw in h_lower for kw in TE_KEYWORDS):
                continue

            # Deduplicate
            key = headline[:80]
            if key in te_seen_headlines:
                continue
            te_seen_headlines.add(key)
            new_count += 1

            # Determine sentiment
            sentiment, s_icon = te_sentiment(headline)

            # Truncate long headlines
            display = headline[:200] + "..." if len(headline) > 200 else headline

            send(
                f"📡 TRADING ECONOMICS NEWS\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{display}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Sentiment: {s_icon} {sentiment}\n"
                f"Source: Trading Economics"
            )

            if new_count >= 3:  # max 3 new items per check
                break

        if new_count == 0:
            print("[TE] No new relevant news")

    except Exception as e:
        print(f"[TE error] {e}")

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def bot_loop():
    print("Bot started.")
    send("🤖 Advanced Signal Bot started\n"
         "Indicators: EMA + MACD + Bollinger + S/R\n"
         "Quality: Multi-timeframe + Score filter + Win tracker\n"
         f"Pairs: {', '.join(p['name'] for p in PAIRS)}")
    while True:
        try:
            check_news()
            check_te_news()
            for pair in PAIRS:
                try: check_pair(pair)
                except Exception as e: print(f"[{pair['name']} error] {e}")
                time.sleep(PAIR_DELAY)
        except Exception as e:
            print(f"[Loop error] {e}")
        time.sleep(LOOP_SLEEP)


# ── FLASK ─────────────────────────────────────────────────────────────────────
@app.route("/")
def home(): return "Bot running", 200

@app.route("/status")
def status():
    stats = {}
    for name, s in win_rate_stats.items():
        total = s["wins"] + s["losses"]
        stats[name] = {
            "wins": s["wins"], "losses": s["losses"],
            "win_rate": f"{int(s['wins']/total*100)}%" if total > 0 else "N/A",
            "last_signal": last_signals.get(name, "none")
        }
    return jsonify(stats)

def run():
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.getenv("PORT", 10000))
    print(f"Flask starting on port {port}...")
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    run()

