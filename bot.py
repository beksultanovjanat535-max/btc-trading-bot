import os
import time
import logging
import requests
import threading
from datetime import datetime
from flask import Flask, jsonify

# ============================================================
# НАСТРОЙКА
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================

SYMBOL = "BTCUSDT"
CHECK_INTERVAL = 300  # 5 минут
RISK_PERCENT = 1.0    # 1% риск (оставляем)
LEVERAGE = 1

balance = 150         # Баланс
SL_PERCENT = 1.5      # Стоп-лосс

# НОВЫЕ ПОРОГИ (чаще фиксация)
TP1 = 0.8             # Продажа 20% при +0.8%
TP2 = 1.8             # Продажа 30% при +1.8%
TP3 = 2.5             # Трейлинг-стоп с +2.5%

# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = "8930303145:AAEI-SoKhSg5nH_PcMqwyHSiLoNw5QibQC8"
TELEGRAM_CHAT_ID = "6867317571"

# ============================================================
# ПЕРЕМЕННЫЕ
# ============================================================

current_price = 0
last_signal = "Нет сигнала"
signal_history = []
position = None
highest_price = 0
last_report_time = 0
last_15min_report = 0

# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    try:
        import requests as telegram_requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        r = telegram_requests.post(url, json=payload)
        if r.status_code == 200:
            logger.info("✅ Telegram OK")
        else:
            logger.error(f"❌ Telegram: {r.text}")
    except Exception as e:
        logger.error(f"❌ Telegram error: {e}")

# ============================================================
# ОТЧЕТЫ
# ============================================================

def send_15min_report():
    global position, last_signal
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
        price = float(r.json()["price"])
        pos = "Нет"
        pnl = "0.00"
        if position:
            pnl_val = (price - position["entry_price"]) * position["quantity"]
            pos = f"{position['quantity']:.3f} BTC"
            pnl = f"{pnl_val:+.2f}"
        analysis = analyze_market()
        score = analysis.get("score", 0)
        msg = f"""
🔄 <b>ОТЧЕТ (15 мин)</b>
⏰ {datetime.now().strftime('%H:%M:%S')}
💰 BTC: {price:.2f}
📊 Сигнал: {last_signal}
📊 Сила: {score:.1f}
📊 Позиция: {pos}
📈 PnL: {pnl} USDT
        """
        send_telegram(msg)
    except Exception as e:
        logger.error(f"Ошибка 15мин отчета: {e}")

def send_hourly_report():
    global position, last_signal
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
        price = float(r.json()["price"])
        pos = "Нет"
        pnl = "0.00"
        entry = "—"
        if position:
            pnl_val = (price - position["entry_price"]) * position["quantity"]
            pos = f"{position['quantity']:.3f} BTC"
            pnl = f"{pnl_val:+.2f}"
            entry = f"{position['entry_price']:.2f}"
        msg = f"""
📊 <b>ЕЖЕЧАСНЫЙ ОТЧЕТ</b>
⏰ {datetime.now().strftime('%H:%M')}
💰 BTC: {price:.2f}
📊 Позиция: {pos}
📈 PnL: {pnl} USDT
📉 Вход: {entry}
        """
        send_telegram(msg)
    except Exception as e:
        logger.error(f"Ошибка часового отчета: {e}")

# ============================================================
# ЭНДПОИНТЫ
# ============================================================

@app.route("/")
def home():
    return jsonify({"status": "running", "balance": balance, "price": current_price})

@app.route("/status")
def status():
    return jsonify({
        "symbol": SYMBOL, "price": current_price, "balance": balance,
        "position": position, "last_signal": last_signal,
        "signal_history": signal_history[-5:]
    })

@app.route("/signals")
def signals():
    return jsonify(analyze_market())

@app.route("/price")
def price():
    r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
    return jsonify(r.json())

# ============================================================
# ИНДИКАТОРЫ
# ============================================================

def get_klines(limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval=5m&limit={limit}"
        data = requests.get(url).json()
        return [{"open": float(c[1]), "high": float(c[2]), "low": float(c[3]),
                 "close": float(c[4]), "volume": float(c[5])} for c in data]
    except:
        return []

def ema(data, period):
    if len(data) < period: return data[-1] if data else 0
    k = 2 / (period + 1)
    e = data[0]
    for p in data[1:]:
        e = (p - e) * k + e
    return e

def rsi(data, period=14):
    if len(data) < period + 1: return 50
    gains, losses = [], []
    for i in range(1, len(data)):
        d = data[i] - data[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    return 100 - (100 / (1 + ag/al))

def atr(klines, period=14):
    if len(klines) < period + 1: return 0
    tr = []
    for i in range(1, len(klines)):
        tr.append(max(klines[i]["high"] - klines[i]["low"],
                      abs(klines[i]["high"] - klines[i-1]["close"]),
                      abs(klines[i]["low"] - klines[i-1]["close"])))
    return sum(tr[-period:]) / period

def bb(data, period=20, std=2):
    if len(data) < period: return None, None, None
    sma = sum(data[-period:]) / period
    var = sum((x - sma) ** 2 for x in data[-period:]) / period
    sd = var ** 0.5
    return sma + sd*std, sma, sma - sd*std

def macd(data):
    if len(data) < 26: return None, None, None
    e12 = ema(data, 12)
    e26 = ema(data, 26)
    m = e12 - e26
    vals = [ema(data[:i+1],12) - ema(data[:i+1],26) for i in range(26, len(data))]
    s = ema(vals, 9) if len(vals) >= 9 else 0
    return m, s, m - s

def sr(klines, lookback=20):
    h = [k["high"] for k in klines[-lookback:]]
    l = [k["low"] for k in klines[-lookback:]]
    c = [k["close"] for k in klines[-lookback:]]
    return min(l), sum(c)/len(c), max(h)

# ============================================================
# АНАЛИЗ
# ============================================================

def analyze_market():
    global current_price
    klines = get_klines(100)
    if not klines or len(klines) < 30:
        return {"signal": "NO", "reason": "Нет данных", "indicators": {}, "score": 0}
    closes = [k["close"] for k in klines]
    current_price = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    pe9 = ema(closes[:-1], 9)
    pe21 = ema(closes[:-1], 21)
    r = rsi(closes)
    a = atr(klines)
    ub, mb, lb = bb(closes)
    m, ms, mh = macd(closes)
    sup, piv, res = sr(klines)

    score = 0
    sigs = []

    if pe9 <= pe21 and e9 > e21:
        score += 2; sigs.append("BUY_EMA")
    elif pe9 >= pe21 and e9 < e21:
        score -= 2; sigs.append("SELL_EMA")

    if r < 35:
        score += 2; sigs.append("BUY_RSI")
        if r < 28: score += 1; sigs.append("BUY_RSI_STRONG")
    elif r > 65:
        score -= 2; sigs.append("SELL_RSI")
        if r > 72: score -= 1; sigs.append("SELL_RSI_STRONG")

    if lb and current_price < lb:
        score += 1.5; sigs.append("BUY_BB")
    elif ub and current_price > ub:
        score -= 1.5; sigs.append("SELL_BB")

    if m and ms:
        if m > ms and mh > 0: score += 1.5; sigs.append("BUY_MACD")
        elif m < ms and mh < 0: score -= 1.5; sigs.append("SELL_MACD")

    if current_price <= sup * 1.01:
        score += 1; sigs.append("BUY_SUPPORT")
    elif current_price >= res * 0.99:
        score -= 1; sigs.append("SELL_RESISTANCE")

    signal = "NO"
    reason = f"Слабый сигнал ({score:.1f})"

    # 🔥 НОВЫЙ ПОРОГ 1.5
    if score >= 1.5:
        signal = "BUY"
        reason = f"BUY ({score:.1f}): {', '.join(sigs)}"
    elif score <= -1.5:
        signal = "SELL"
        reason = f"SELL ({score:.1f}): {', '.join(sigs)}"

    return {
        "signal": signal, "reason": reason, "score": round(score, 1),
        "signals": sigs,
        "indicators": {
            "ema9": round(e9, 2), "ema21": round(e21, 2), "rsi": round(r, 2),
            "atr": round(a, 2), "support": round(sup, 2), "resistance": round(res, 2)
        }
    }

# ============================================================
# УМНАЯ ПРОДАЖА
# ============================================================

def smart_sell():
    global position, balance, highest_price, last_signal
    if not position: return

    try:
        p = current_price
        ep = position["entry_price"]
        q = position["quantity"]

        if p > highest_price: highest_price = p
        gain = (p - ep) / ep * 100

        if gain >= TP1 and not position.get("tp1"):
            sq = q * 0.2
            balance += sq * p
            position["quantity"] -= sq
            position["tp1"] = True
            send_telegram(f"📈 <b>ФИКСАЦИЯ 20%</b>\n💵 {p:.2f} USDT\n📊 +{gain:.2f}%")
            logger.info(f"✅ 20% при +{gain:.2f}%")

        elif gain >= TP2 and not position.get("tp2"):
            sq = position["quantity"] * 0.3
            balance += sq * p
            position["quantity"] -= sq
            position["tp2"] = True
            send_telegram(f"📈 <b>ФИКСАЦИЯ 30%</b>\n💵 {p:.2f} USDT\n📊 +{gain:.2f}%")
            logger.info(f"✅ 30% при +{gain:.2f}%")

        elif gain >= TP3:
            trail = highest_price * 0.985
            if p <= trail:
                sq = position["quantity"]
                balance += sq * p
                pnl = (p - ep) * sq
                send_telegram(f"🔴 <b>ТРЕЙЛИНГ-СТОП</b>\n💵 {p:.2f}\n📈 +{pnl:.2f} USDT")
                logger.info(f"🔴 Трейлинг-стоп: +{pnl:.2f}")
                position = None
                last_signal = "SELL"

    except Exception as e:
        logger.error(f"smart_sell: {e}")

# ============================================================
# ТОРГОВЛЯ
# ============================================================

def execute_trade(side):
    global balance, position, last_signal, highest_price
    try:
        p = current_price
        if p == 0: return {"error": "Нет цены"}

        sld = p * (SL_PERCENT / 100)
        rm = balance * (RISK_PERCENT / 100)
        q = round(rm / sld, 3)
        if q < 0.001: return {"error": "Объем мал"}

        if side == "BUY":
            cost = q * p
            if balance < cost: return {"error": "Мало баланса"}
            balance -= cost
            highest_price = p
            position = {
                "side": "LONG", "quantity": q, "entry_price": p,
                "sl_price": p - sld, "tp1": False, "tp2": False
            }
            last_signal = "BUY"
            send_telegram(f"""
🟢 <b>ПОКУПКА</b>
💰 {q:.3f} BTC
💵 {p:.2f} USDT
📊 Баланс: {balance:.2f}
🛑 SL: {position['sl_price']:.2f}
🎯 Фиксация: +{TP1}%, +{TP2}%
            """)
            return {"status": "ok"}

        elif side == "SELL":
            if not position: return {"error": "Нет позиции"}
            q = position["quantity"]
            pnl = (p - position["entry_price"]) * q
            balance += q * p
            position = None
            last_signal = "SELL"
            highest_price = 0
            emoji = "📈" if pnl > 0 else "📉"
            txt = f"ПРИБЫЛЬ: +{pnl:.2f} ✅" if pnl > 0 else f"УБЫТОК: {pnl:.2f} ❌"
            send_telegram(f"🔴 <b>ПРОДАЖА</b>\n{emoji} {txt}\n💵 {p:.2f}\n📊 {balance:.2f}")
            return {"status": "ok"}

        return {"error": "Неверная сторона"}
    except Exception as e:
        logger.error(f"trade: {e}")
        return {"error": str(e)}

# ============================================================
# ОСНОВНОЙ ЦИКЛ
# ============================================================

def check_market():
    global last_signal, signal_history, position, balance, highest_price
    global last_report_time, last_15min_report, current_price

    send_telegram("🚀 <b>Умный бот запущен!</b>\nПорог 1.5 | Фиксация: +0.8%, +1.8%")

    while True:
        try:
            now = time.time()
            if int(now // 900) != last_15min_report:
                last_15min_report = int(now // 900)
                send_15min_report()
            if int(now // 3600) != last_report_time:
                last_report_time = int(now // 3600)
                send_hourly_report()

            # Цена
            try:
                r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
                current_price = float(r.json()["price"])
                logger.info(f"💰 BTC: {current_price:.2f}")
            except Exception as e:
                logger.error(f"Цена: {e}")
                time.sleep(60)
                continue

            # Позиция
            if position:
                ep = position["entry_price"]
                gain = (current_price - ep) / ep * 100
                logger.info(f"📊 Позиция: +{gain:.2f}%")

                if current_price <= position["sl_price"]:
                    logger.info("🔴 STOP-LOSS")
                    execute_trade("SELL")
                    time.sleep(5)
                    continue

                smart_sell()
                time.sleep(2)
                continue

            # Анализ
            analysis = analyze_market()
            signal = analysis["signal"]
            reason = analysis["reason"]
            score = analysis["score"]
            last_signal = signal

            logger.info(f"📊 Сила: {score:.1f} | {reason}")

            signal_history.append({
                "time": datetime.now().isoformat(),
                "signal": signal, "price": current_price, "score": score
            })
            if len(signal_history) > 100: signal_history.pop(0)

            if signal == "BUY" and not position:
                logger.info(f"🟢 BUY! {reason}")
                send_telegram(f"🟢 <b>СИГНАЛ BUY</b>\n📊 {reason}\n💵 {current_price:.2f}")
                execute_trade("BUY")

            elif signal == "SELL" and position:
                logger.info(f"🔴 SELL! {reason}")
                send_telegram(f"🔴 <b>СИГНАЛ SELL</b>\n📊 {reason}\n💵 {current_price:.2f}")
                execute_trade("SELL")

            # 🔥 Умный выход при слабом сигнале
            elif position and score < 0.5:
                ep = position["entry_price"]
                gain = (current_price - ep) / ep * 100
                if gain < 0.3:
                    logger.info(f"⚠️ Слабый сигнал ({score:.1f}), продажа")
                    send_telegram(f"⚠️ <b>ВЫХОД</b>\nСлабый сигнал: {score:.1f}")
                    execute_trade("SELL")

        except Exception as e:
            logger.error(f"❌ {e}")
            send_telegram(f"⚠️ Ошибка: {e}")

        time.sleep(CHECK_INTERVAL)

# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    logger.info("🤖 БОТ v4.2 ЗАПУЩЕН")
    logger.info(f"💰 Баланс: {balance} USDT | Риск: {RISK_PERCENT}%")
    logger.info(f"🎯 Фиксация: +{TP1}% (20%), +{TP2}% (30%), трейлинг +{TP3}%")
    logger.info(f"⚡ Порог сигнала: 1.5")

    thread = threading.Thread(target=check_market, daemon=True)
    thread.start()

    app.run(host="0.0.0.0", port=5000)
