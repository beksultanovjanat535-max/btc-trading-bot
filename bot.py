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
CHECK_INTERVAL = 300
RISK_PERCENT = 1.0

balance = 150
SL_PERCENT = 1.5

# РЕЖИМЫ TP
MODES = {
    "safe": {"tp1": 1.2, "tp2": 2.5, "tp3": 4.0},
    "profit": {"tp1": 3.0, "tp2": 6.0, "tp3": 10.0}
}
current_mode = "profit"

# DCA
DCA_ENABLED = True
DCA_PERCENT = -0.5
DCA_MAX_TIMES = 2

# ❌ ТАЙМАУТ УБРАН
# MAX_HOLD_TIME = 7200  # УДАЛЕНО

# ✅ ТРЕЙЛИНГ С +1.5%
TRAILING_START = 1.5      # Трейлинг включается при +1.5%
TRAILING_DISTANCE = 1.0   # Отступ от максимума 1%

# ✅ ФИЛЬТР ПИКА
MAX_HOURLY_GROWTH = 2.0   # Не покупать, если +2% за час

# Ранний выход при сильном минусе
EARLY_EXIT_PERCENT = -1.0

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
position_open_time = 0
highest_price = 0
trailing_active = False
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
    global position, last_signal, current_mode
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
🎯 Режим: {current_mode.upper()}
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
    return jsonify({"status": "running", "mode": current_mode, "balance": balance})

@app.route("/status")
def status():
    return jsonify({
        "symbol": SYMBOL, "price": current_price, "balance": balance,
        "position": position, "last_signal": last_signal,
        "mode": current_mode, "trailing": trailing_active
    })

@app.route("/mode/<mode>")
def set_mode(mode):
    global current_mode
    if mode in MODES:
        current_mode = mode
        send_telegram(f"🎯 Режим изменён: <b>{mode.upper()}</b>")
        return jsonify({"status": "ok", "mode": mode})
    return jsonify({"error": "Неверный режим"}), 400

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
# ФИЛЬТРЫ
# ============================================================

def check_trend(klines):
    """Не покупать, если падает >1% за час"""
    if len(klines) < 12: return True
    old = klines[-12]["close"]
    new = klines[-1]["close"]
    change = (new - old) / old * 100
    logger.info(f"📉 Тренд за час: {change:+.2f}%")
    if change < -1.0:
        return False
    return True

def check_pump(klines):
    """✅ НЕ ПОКУПАТЬ НА ПИКЕ: если +2% за час"""
    if len(klines) < 12: return True
    old = klines[-12]["close"]
    new = klines[-1]["close"]
    change = (new - old) / old * 100
    logger.info(f"📈 Рост за час: {change:+.2f}%")
    if change > MAX_HOURLY_GROWTH:
        logger.info(f"⚠️ ПИК! Не покупаем (рост {change:+.2f}%)")
        return False
    return True

# ============================================================
# АНАЛИЗ
# ============================================================

def analyze_market():
    global current_price
    klines = get_klines(100)
    if not klines or len(klines) < 30:
        return {"signal": "NO", "reason": "Нет данных", "score": 0}
    closes = [k["close"] for k in klines]
    current_price = closes[-1]

    e9 = ema(closes, 9); e21 = ema(closes, 21)
    pe9 = ema(closes[:-1], 9); pe21 = ema(closes[:-1], 21)
    r = rsi(closes); a = atr(klines)
    ub, mb, lb = bb(closes)
    m, ms, mh = macd(closes)
    sup, piv, res = sr(klines)

    score = 0; sigs = []
    if pe9 <= pe21 and e9 > e21: score += 2; sigs.append("BUY_EMA")
    elif pe9 >= pe21 and e9 < e21: score -= 2; sigs.append("SELL_EMA")
    if r < 35: score += 2; sigs.append("BUY_RSI")
    elif r > 65: score -= 2; sigs.append("SELL_RSI")
    if lb and current_price < lb: score += 1.5; sigs.append("BUY_BB")
    elif ub and current_price > ub: score -= 1.5; sigs.append("SELL_BB")
    if m and ms:
        if m > ms and mh > 0: score += 1.5; sigs.append("BUY_MACD")
        elif m < ms and mh < 0: score -= 1.5; sigs.append("SELL_MACD")
    if current_price <= sup * 1.01: score += 1; sigs.append("BUY_SUPPORT")
    elif current_price >= res * 0.99: score -= 1; sigs.append("SELL_RESISTANCE")

    signal = "NO"; reason = f"Слабый ({score:.1f})"
    if score >= 2.5:
        if check_trend(klines) and check_pump(klines):
            signal = "BUY"; reason = f"BUY ({score:.1f}): {', '.join(sigs)}"
        else:
            reason = f"BUY отменён (тренд/пик)"
    elif score <= -2.5:
        signal = "SELL"; reason = f"SELL ({score:.1f}): {', '.join(sigs)}"

    return {"signal": signal, "reason": reason, "score": round(score, 1), "atr": round(a, 2)}

# ============================================================
# УМНАЯ ПРОДАЖА + ТРЕЙЛИНГ С +1.5%
# ============================================================

def smart_sell():
    global position, balance, highest_price, last_signal, trailing_active
    if not position: return

    try:
        p = current_price
        ep = position["entry_price"]
        q = position["quantity"]
        mode = MODES[current_mode]

        if p > highest_price: highest_price = p
        gain = (p - ep) / ep * 100

        # DCA
        if DCA_ENABLED and gain <= DCA_PERCENT and position.get("dca_count", 0) < DCA_MAX_TIMES:
            dca_qty = 0.001
            cost = dca_qty * p
            if balance >= cost:
                balance -= cost
                total_qty = position["quantity"] + dca_qty
                avg_price = (position["entry_price"] * position["quantity"] + p * dca_qty) / total_qty
                position["quantity"] = total_qty
                position["entry_price"] = avg_price
                position["dca_count"] = position.get("dca_count", 0) + 1
                send_telegram(f"🔄 <b>DCA #{position['dca_count']}</b>\n💵 {p:.2f}\n📊 Средняя: {avg_price:.2f}")
                logger.info(f"🔄 DCA #{position['dca_count']}")

        # ✅ ТРЕЙЛИНГ-СТОП С +1.5%
        if gain >= TRAILING_START:
            if not trailing_active:
                trailing_active = True
                logger.info(f"✅ Трейлинг активирован при +{gain:.2f}%")
                send_telegram(f"✅ <b>ТРЕЙЛИНГ АКТИВЕН</b>\nЦена: {p:.2f}\nМаксимум: {highest_price:.2f}")

            trail_price = highest_price * (1 - TRAILING_DISTANCE / 100)
            if p <= trail_price:
                sq = position["quantity"]
                balance += sq * p
                pnl = (p - ep) * sq
                send_telegram(f"🔴 <b>ТРЕЙЛИНГ-СТОП</b>\n💵 {p:.2f}\n📈 +{pnl:.2f} USDT")
                logger.info(f"🔴 Трейлинг: +{pnl:.2f}")
                position = None
                last_signal = "SELL"
                trailing_active = False
                return

        # ФИКСАЦИЯ 20%
        if gain >= mode["tp1"] and not position.get("tp1"):
            sq = q * 0.2
            balance += sq * p
            position["quantity"] -= sq
            position["tp1"] = True
            send_telegram(f"📈 <b>ФИКСАЦИЯ 20%</b>\n💵 {p:.2f}\n📊 +{gain:.2f}%")
            logger.info(f"✅ 20% при +{gain:.2f}%")

        # ФИКСАЦИЯ 30%
        elif gain >= mode["tp2"] and not position.get("tp2"):
            sq = position["quantity"] * 0.3
            balance += sq * p
            position["quantity"] -= sq
            position["tp2"] = True
            send_telegram(f"📈 <b>ФИКСАЦИЯ 30%</b>\n💵 {p:.2f}\n📊 +{gain:.2f}%")
            logger.info(f"✅ 30% при +{gain:.2f}%")

    except Exception as e:
        logger.error(f"smart_sell: {e}")

# ============================================================
# ТОРГОВЛЯ
# ============================================================

def execute_trade(side):
    global balance, position, last_signal, highest_price, position_open_time, trailing_active
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
            position_open_time = time.time()
            trailing_active = False
            position = {
                "side": "LONG", "quantity": q, "entry_price": p,
                "sl_price": p - sld, "tp1": False, "tp2": False, "dca_count": 0
            }
            last_signal = "BUY"
            mode = MODES[current_mode]
            send_telegram(f"""
🟢 <b>ПОКУПКА</b>
💰 {q:.3f} BTC
💵 {p:.2f} USDT
📊 Баланс: {balance:.2f}
🛑 SL: {position['sl_price']:.2f}
🎯 Режим: {current_mode.upper()}
📈 TP: +{mode['tp1']}%, +{mode['tp2']}%, +{mode['tp3']}%
🔄 Трейлинг с +{TRAILING_START}%
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
            position_open_time = 0
            trailing_active = False
            emoji = "📈" if pnl > 0 else "📉"
            txt = f"ПРИБЫЛЬ: +{pnl:.2f} ✅" if pnl > 0 else f"УБЫТОК: {pnl:.2f} ❌"
            send_telegram(f"🔴 <b>ПРОДАЖА</b>\n{emoji} {txt}\n💵 {p:.2f}\n📊 {balance:.2f}")
            return {"status": "ok"}

        return {"error": "Неверная сторона"}
    except Exception as e:
        logger.error(f"trade: {e}")
        return {"error": str(e)}

# ============================================================
# ОСНОВНОЙ ЦИКЛ (БЕЗ ТАЙМАУТА!)
# ============================================================

def check_market():
    global last_signal, signal_history, position, balance, highest_price
    global last_report_time, last_15min_report, current_price, position_open_time, trailing_active

    send_telegram(f"🚀 <b>Бот v6.1 запущен!</b>\n🎯 Режим: {current_mode.upper()}\n✅ Трейлинг с +{TRAILING_START}%\n❌ Таймаут УБРАН")

    while True:
        try:
            now = time.time()
            if int(now // 900) != last_15min_report:
                last_15min_report = int(now // 900)
                send_15min_report()
            if int(now // 3600) != last_report_time:
                last_report_time = int(now // 3600)
                send_hourly_report()

            try:
                r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
                current_price = float(r.json()["price"])
                logger.info(f"💰 BTC: {current_price:.2f}")
            except Exception as e:
                logger.error(f"Цена: {e}")
                time.sleep(60)
                continue

            if position:
                ep = position["entry_price"]
                gain = (current_price - ep) / ep * 100
                hold_time = now - position_open_time if position_open_time else 0
                logger.info(f"📊 Позиция: +{gain:.2f}% | Держим {hold_time/60:.0f} мин | Трейлинг: {trailing_active}")

                # 1. STOP-LOSS
                if current_price <= position["sl_price"]:
                    logger.info("🔴 STOP-LOSS")
                    execute_trade("SELL")
                    time.sleep(5)
                    continue

                # 2. УМНАЯ ПРОДАЖА + ТРЕЙЛИНГ
                smart_sell()
                if not position:
                    time.sleep(2)
                    continue

                # 3. Ранний выход при сильном минусе
                if gain <= EARLY_EXIT_PERCENT:
                    logger.info(f"⚠️ Ранний выход: {gain:.2f}%")
                    send_telegram(f"⚠️ <b>РАННИЙ ВЫХОД</b>\nPnL: {gain:.2f}%")
                    execute_trade("SELL")
                    time.sleep(5)
                    continue

                # ❌ ТАЙМАУТ УБРАН — держим до TP/SL/трейлинга
                time.sleep(2)
                continue

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

        except Exception as e:
            logger.error(f"❌ {e}")
            send_telegram(f"⚠️ Ошибка: {e}")

        time.sleep(CHECK_INTERVAL)

# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    logger.info("🤖 БОТ v6.1 ЗАПУЩЕН")
    logger.info(f"💰 Баланс: {balance} USDT | Риск: {RISK_PERCENT}%")
    logger.info(f"🎯 Режим: {current_mode.upper()}")
    logger.info(f"✅ Трейлинг с +{TRAILING_START}% (отступ {TRAILING_DISTANCE}%)")
    logger.info(f"❌ Таймаут УБРАН")
    logger.info(f"⚠️ Фильтр пика: не покупать при +{MAX_HOURLY_GROWTH}%/час")

    thread = threading.Thread(target=check_market, daemon=True)
    thread.start()

    app.run(host="0.0.0.0", port=5000)
