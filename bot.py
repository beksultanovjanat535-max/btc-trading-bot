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
RISK_PERCENT = 1.0    # 1% риск
LEVERAGE = 1

# НАСТРОЙКИ УМНОЙ ТОРГОВЛИ (уменьшенные пороги)
balance = 150
SL_PERCENT = 1.5      # Стоп-лосс 1.5%

# НОВЫЕ ПОРОГИ ДЛЯ ЧАСТИЧНОЙ ПРОДАЖИ
TP1 = 1.0             # Продаём 20% при +1% (было 2%)
TP2 = 2.5             # Продаём 30% при +2.5% (было 4%)
TP3 = 4.0             # Трейлинг-стоп с +4% (было 6%)

# ============================================================
# TELEGRAM НАСТРОЙКИ (ВСТАВЬТЕ СВОИ ДАННЫЕ!)
# ============================================================

TELEGRAM_BOT_TOKEN = "8930303145:AAEI-SoKhSg5nH_PcMqwyHSiLoNw5QibQC8"
TELEGRAM_CHAT_ID = "6867317571"

# ============================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ============================================================

current_price = 0
last_signal = "Нет сигнала"
signal_history = []
position = None
highest_price = 0
last_report_time = 0
last_15min_report = 0

# ============================================================
# ФУНКЦИЯ ОТПРАВКИ В TELEGRAM
# ============================================================

def send_telegram(message):
    try:
        import requests as telegram_requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = telegram_requests.post(url, json=payload)
        if response.status_code == 200:
            logger.info("✅ Сообщение отправлено в Telegram")
        else:
            logger.error(f"❌ Ошибка отправки: {response.text}")
    except Exception as e:
        logger.error(f"❌ Ошибка Telegram: {e}")

# ============================================================
# ОТЧЕТЫ
# ============================================================

def send_hourly_report():
    """Ежечасный отчет"""
    global current_price, position, last_signal
    
    try:
        response = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
        data = response.json()
        price = float(data["price"])
        
        klines = get_klines(2)
        if klines and len(klines) >= 2:
            old_price = klines[0]["close"]
            change_1h = ((price - old_price) / old_price) * 100
        else:
            change_1h = 0
        
        pos_info = "Нет позиции"
        pnl_info = "0.00"
        entry_info = "—"
        if position:
            pnl = (price - position["entry_price"]) * position["quantity"]
            pos_info = f"Есть ({position['quantity']:.3f} BTC)"
            pnl_info = f"{pnl:+.2f}"
            entry_info = f"{position['entry_price']:.2f}"
        
        msg = f"""
📊 <b>ЕЖЕЧАСНЫЙ ОТЧЕТ</b>
⏰ Время: {datetime.now().strftime('%H:%M')}

💰 <b>Цена BTC:</b> {price:.2f} USDT
📈 <b>Изменение за час:</b> {change_1h:+.2f}%

📊 <b>Позиция:</b> {pos_info}
📈 <b>PnL:</b> {pnl_info} USDT
📉 <b>Цена входа:</b> {entry_info}

📱 <b>Последний сигнал:</b> {last_signal}
⏳ <b>Следующая проверка:</b> через 5 минут
        """
        send_telegram(msg)
        
    except Exception as e:
        logger.error(f"Ошибка отправки отчета: {e}")

def send_15min_report():
    """Отчет каждые 15 минут с реальной ценой"""
    global current_price, position, last_signal
    
    try:
        response = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
        data = response.json()
        price = float(data["price"])
        
        pos_info = "Нет позиции"
        pnl_info = "0.00"
        if position:
            pnl = (price - position["entry_price"]) * position["quantity"]
            pos_info = f"Есть ({position['quantity']:.3f} BTC)"
            pnl_info = f"{pnl:+.2f}"
        
        analysis = analyze_market()
        score = analysis.get("score", 0)
        
        msg = f"""
🔄 <b>ОТЧЕТ (15 минут)</b>
⏰ Время: {datetime.now().strftime('%H:%M:%S')}

💰 <b>BTC:</b> {price:.2f} USDT
📊 <b>Сигнал:</b> {last_signal}
📊 <b>Сила:</b> {score:.1f}
📊 <b>Позиция:</b> {pos_info}
📈 <b>PnL:</b> {pnl_info} USDT
        """
        send_telegram(msg)
        
    except Exception as e:
        logger.error(f"Ошибка отправки 15-минутного отчета: {e}")

# ============================================================
# СТРАНИЦЫ (ЭНДПОИНТЫ)
# ============================================================

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "bot": "BTC Smart Bot",
        "version": "4.1",
        "balance": balance,
        "price": current_price,
        "last_signal": last_signal,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/status")
def status():
    return jsonify({
        "symbol": SYMBOL,
        "price": current_price,
        "balance": balance,
        "position": position,
        "last_signal": last_signal,
        "signal_history": signal_history[-10:],
        "highest_price": highest_price,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/signals")
def get_signals():
    analysis = analyze_market()
    return jsonify({
        "symbol": SYMBOL,
        "price": current_price,
        "signals": analysis,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/price")
def get_price():
    try:
        response = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
        data = response.json()
        return jsonify({
            "symbol": data["symbol"],
            "price": float(data["price"]),
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)})

# ============================================================
# ИНДИКАТОРЫ
# ============================================================

def get_klines(limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval=5m&limit={limit}"
        response = requests.get(url)
        data = response.json()
        
        klines = []
        for candle in data:
            klines.append({
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5])
            })
        return klines
    except Exception as e:
        logger.error(f"Ошибка получения свечей: {e}")
        return []

def calculate_ema(data, period):
    if len(data) < period:
        return data[-1] if data else 0
    multiplier = 2 / (period + 1)
    ema = data[0]
    for price in data[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_rsi(data, period=14):
    if len(data) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(1, len(data)):
        change = data[i] - data[i-1]
        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_atr(klines, period=14):
    if len(klines) < period + 1:
        return 0
    tr_values = []
    for i in range(1, len(klines)):
        high = klines[i]["high"]
        low = klines[i]["low"]
        prev_close = klines[i-1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)
    if not tr_values:
        return 0
    return sum(tr_values[-period:]) / period

def calculate_bollinger_bands(data, period=20, std=2):
    if len(data) < period:
        return None, None, None
    sma = sum(data[-period:]) / period
    variance = sum((x - sma) ** 2 for x in data[-period:]) / period
    std_dev = variance ** 0.5
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    return upper, sma, lower

def calculate_macd(data):
    if len(data) < 26:
        return None, None, None
    ema12 = calculate_ema(data, 12)
    ema26 = calculate_ema(data, 26)
    macd = ema12 - ema26
    macd_values = []
    for i in range(26, len(data)):
        ema12_i = calculate_ema(data[:i+1], 12)
        ema26_i = calculate_ema(data[:i+1], 26)
        macd_values.append(ema12_i - ema26_i)
    signal = calculate_ema(macd_values, 9) if len(macd_values) >= 9 else 0
    histogram = macd - signal
    return macd, signal, histogram

def calculate_support_resistance(klines, lookback=20):
    highs = [k["high"] for k in klines[-lookback:]]
    lows = [k["low"] for k in klines[-lookback:]]
    closes = [k["close"] for k in klines[-lookback:]]
    resistance = max(highs)
    support = min(lows)
    pivot = sum(closes) / len(closes)
    return support, pivot, resistance

# ============================================================
# ВЗВЕШЕННЫЙ АНАЛИЗ РЫНКА
# ============================================================

def analyze_market():
    global current_price
    
    klines = get_klines(100)
    if not klines or len(klines) < 30:
        return {
            "signal": "NO",
            "reason": "Недостаточно данных",
            "indicators": {}
        }
    
    closes = [k["close"] for k in klines]
    current_price = closes[-1]
    
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    prev_ema9 = calculate_ema(closes[:-1], 9)
    prev_ema21 = calculate_ema(closes[:-1], 21)
    rsi = calculate_rsi(closes, 14)
    atr = calculate_atr(klines, 14)
    upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(closes, 20, 2)
    macd, signal_line, histogram = calculate_macd(closes)
    support, pivot, resistance = calculate_support_resistance(klines, 20)
    
    score = 0
    signals = []
    
    if prev_ema9 <= prev_ema21 and ema9 > ema21:
        score += 2
        signals.append("BUY_EMA")
    elif prev_ema9 >= prev_ema21 and ema9 < ema21:
        score -= 2
        signals.append("SELL_EMA")
    
    if rsi < 30:
        score += 2
        signals.append("BUY_RSI")
        if rsi < 25:
            score += 1
            signals.append("BUY_RSI_STRONG")
    elif rsi > 70:
        score -= 2
        signals.append("SELL_RSI")
        if rsi > 75:
            score -= 1
            signals.append("SELL_RSI_STRONG")
    
    if lower_bb and current_price < lower_bb:
        score += 1.5
        signals.append("BUY_BB")
    elif upper_bb and current_price > upper_bb:
        score -= 1.5
        signals.append("SELL_BB")
    
    if macd and signal_line:
        if macd > signal_line and histogram > 0:
            score += 1.5
            signals.append("BUY_MACD")
        elif macd < signal_line and histogram < 0:
            score -= 1.5
            signals.append("SELL_MACD")
    
    if current_price <= support * 1.01:
        score += 1
        signals.append("BUY_SUPPORT")
    elif current_price >= resistance * 0.99:
        score -= 1
        signals.append("SELL_RESISTANCE")
    
    signal = "NO"
    reason = "Нет сигнала"
    
    if score >= 3:
        signal = "BUY"
        reason = f"Сила сигнала: {score:.1f}, {', '.join(signals)}"
    elif score <= -3:
        signal = "SELL"
        reason = f"Сила сигнала: {score:.1f}, {', '.join(signals)}"
    else:
        reason = f"Слабый сигнал ({score:.1f})"
    
    return {
        "signal": signal,
        "reason": reason,
        "score": round(score, 1),
        "signals_count": len(signals),
        "all_signals": signals,
        "indicators": {
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "rsi": round(rsi, 2),
            "atr": round(atr, 2),
            "bb_upper": round(upper_bb, 2) if upper_bb else None,
            "bb_middle": round(middle_bb, 2) if middle_bb else None,
            "bb_lower": round(lower_bb, 2) if lower_bb else None,
            "macd": round(macd, 4) if macd else None,
            "macd_signal": round(signal_line, 4) if signal_line else None,
            "macd_hist": round(histogram, 4) if histogram else None,
            "support": round(support, 2),
            "pivot": round(pivot, 2),
            "resistance": round(resistance, 2)
        }
    }

# ============================================================
# УМНАЯ ТОРГОВЛЯ (С НОВЫМИ ПОРОГАМИ)
# ============================================================

def smart_sell():
    global position, balance, highest_price, last_signal
    
    if not position:
        return
    
    try:
        price = current_price
        entry_price = position["entry_price"]
        quantity = position["quantity"]
        
        if price > highest_price:
            highest_price = price
        
        gain = (price - entry_price) / entry_price * 100
        
        # Частичная продажа при +1% (20%)
        if gain >= TP1 and not position.get("tp1_done", False):
            sell_qty = quantity * 0.2
            balance += sell_qty * price
            position["quantity"] -= sell_qty
            position["tp1_done"] = True
            
            msg = f"""
📈 <b>ЧАСТИЧНАЯ ПРОДАЖА (20%)</b>
💵 Цена: {price:.2f} USDT
📊 Рост: +{gain:.2f}%
💰 Зафиксировано: {sell_qty:.4f} BTC
            """
            send_telegram(msg)
            logger.info(f"✅ Частичная продажа 20% при +{gain:.2f}%")
        
        # Частичная продажа при +2.5% (30%)
        elif gain >= TP2 and not position.get("tp2_done", False):
            sell_qty = position["quantity"] * 0.3
            balance += sell_qty * price
            position["quantity"] -= sell_qty
            position["tp2_done"] = True
            
            msg = f"""
📈 <b>ЧАСТИЧНАЯ ПРОДАЖА (30%)</b>
💵 Цена: {price:.2f} USDT
📊 Рост: +{gain:.2f}%
💰 Зафиксировано: {sell_qty:.4f} BTC
            """
            send_telegram(msg)
            logger.info(f"✅ Частичная продажа 30% при +{gain:.2f}%")
        
        # Трейлинг-стоп с +4%
        elif gain >= TP3:
            trailing_stop = highest_price * 0.985  # 1.5% от максимума
            if price <= trailing_stop:
                sell_qty = position["quantity"]
                balance += sell_qty * price
                total_pnl = (price - entry_price) * sell_qty
                
                msg = f"""
🔴 <b>ТРЕЙЛИНГ-СТОП АКТИВИРОВАН</b>
💵 Цена продажи: {price:.2f} USDT
📊 Максимум: {highest_price:.2f} USDT
💰 Остаток: {sell_qty:.4f} BTC
📈 Прибыль: {total_pnl:.2f} USDT
                """
                send_telegram(msg)
                logger.info(f"🔴 Трейлинг-стоп продажа по {price:.2f}")
                
                position = None
                last_signal = "SELL"
        
    except Exception as e:
        logger.error(f"Ошибка smart_sell: {e}")

# ============================================================
# ТОРГОВЛЯ
# ============================================================

def execute_trade(side):
    global balance, position, last_signal, highest_price
    
    try:
        price = current_price
        if price == 0:
            return {"error": "Цена не доступна"}
        
        sl_distance = price * (SL_PERCENT / 100)
        risk_money = balance * (RISK_PERCENT / 100)
        quantity = risk_money / sl_distance
        quantity = round(quantity, 3)
        
        if quantity < 0.001:
            return {"error": "Объем слишком мал"}
        
        if side == "BUY":
            cost = quantity * price
            if balance < cost:
                return {"error": "Недостаточно баланса"}
            
            balance -= cost
            highest_price = price
            position = {
                "side": "LONG",
                "quantity": quantity,
                "entry_price": price,
                "current_price": price,
                "pnl": 0,
                "sl_price": price - sl_distance,
                "tp1_done": False,
                "tp2_done": False
            }
            last_signal = "BUY"
            
            msg = f"""
🟢 <b>НОВАЯ ПОКУПКА</b>
💰 Сумма: {quantity:.3f} BTC
💵 Цена: {price:.2f} USDT
📊 Баланс: {balance:.2f} USDT
🛑 Стоп-лосс: {position['sl_price']:.2f} USDT (-{SL_PERCENT}%)
🎯 Частичная продажа: +{TP1}%, +{TP2}%, трейлинг с +{TP3}%
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            """
            send_telegram(msg)
            
            return {"status": "success", "action": "BUY"}
        
        elif side == "SELL":
            if not position:
                return {"error": "Нет позиции для продажи"}
            
            sell_qty = position["quantity"]
            pnl = (price - position["entry_price"]) * sell_qty
            balance += sell_qty * price
            
            position = None
            last_signal = "SELL"
            highest_price = 0
            
            profit_emoji = "📈" if pnl > 0 else "📉"
            profit_text = f"ПРИБЫЛЬ: +{pnl:.2f} USDT ✅" if pnl > 0 else f"УБЫТОК: {pnl:.2f} USDT ❌"
            
            msg = f"""
🔴 <b>ПРОДАЖА</b>
{profit_emoji} <b>{profit_text}</b>
💵 Цена продажи: {price:.2f} USDT
📊 Баланс: {balance:.2f} USDT
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            """
            send_telegram(msg)
            
            return {"status": "success", "action": "SELL"}
        
        return {"error": "Неверная сторона"}
        
    except Exception as e:
        logger.error(f"Ошибка торговли: {e}")
        return {"error": str(e)}

# ============================================================
# ОСНОВНАЯ ФУНКЦИЯ
# ============================================================

def check_market():
    global last_signal, signal_history, position, balance, highest_price, last_report_time, last_15min_report
    
    send_telegram("🚀 <b>Умный бот запущен!</b> Частичная фиксация + трейлинг-стоп активны.")
    
    while True:
        try:
            # ОТЧЕТ КАЖДЫЕ 15 МИНУТ
            current_15min = int(time.time() // 900)
            if current_15min != last_15min_report:
                last_15min_report = current_15min
                send_15min_report()
            
            # ЕЖЕЧАСНЫЙ ОТЧЕТ
            current_hour = int(time.time() // 3600)
            if current_hour != last_report_time:
                last_report_time = current_hour
                send_hourly_report()
            
            logger.info("=" * 60)
            logger.info("🔍 ПРОВЕРКА РЫНКА")
            
            try:
                response = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
                data = response.json()
                current_price = float(data["price"])
                logger.info(f"💰 Цена BTC: {current_price:.2f}")
            except Exception as e:
                logger.error(f"Ошибка получения цены: {e}")
                time.sleep(60)
                continue
            
            if position:
                entry = position["entry_price"]
                gain = (current_price - entry) / entry * 100
                logger.info(f"📊 Позиция: +{gain:.2f}% от входа")
                
                # Стоп-лосс
                if current_price <= position["sl_price"]:
                    logger.info("🔴 СТОП-ЛОСС АКТИВИРОВАН!")
                    result = execute_trade("SELL")
                    if result and "error" not in result:
                        logger.info("✅ Продажа по стоп-лоссу")
                    time.sleep(5)
                    continue
                
                # УМНАЯ ПРОДАЖА
                smart_sell()
                time.sleep(2)
                continue
            
            # АНАЛИЗ РЫНКА
            analysis = analyze_market()
            signal = analysis["signal"]
            reason = analysis["reason"]
            indicators = analysis["indicators"]
            score = analysis.get("score", 0)
            
            last_signal = signal
            
            signal_entry = {
                "time": datetime.now().isoformat(),
                "signal": signal,
                "price": current_price,
                "reason": reason,
                "indicators": indicators,
                "score": score
            }
            signal_history.append(signal_entry)
            if len(signal_history) > 100:
                signal_history.pop(0)
            
            logger.info(f"📊 EMA9: {indicators['ema9']:.2f}")
            logger.info(f"📊 EMA21: {indicators['ema21']:.2f}")
            logger.info(f"📊 RSI: {indicators['rsi']:.2f}")
            logger.info(f"📊 ATR: {indicators['atr']:.2f}")
            logger.info(f"📊 Сила сигнала: {score:.1f}")
            logger.info(f"📊 {reason}")
            
            # СИГНАЛ BUY
            if signal == "BUY" and not position:
                logger.info(f"🟢 BUY СИГНАЛ! {reason}")
                
                msg = f"""
🟢 <b>СИГНАЛ BUY</b>
📊 {reason}
💵 Цена: {current_price:.2f} USDT
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                """
                send_telegram(msg)
                
                logger.info("🚀 АВТОМАТИЧЕСКАЯ ПОКУПКА...")
                result = execute_trade("BUY")
                if result and "error" not in result:
                    logger.info(f"✅ Сделка выполнена! Баланс: {balance:.2f}")
                else:
                    logger.error(f"❌ Ошибка: {result.get('error')}")
            
            # СИГНАЛ SELL
            elif signal == "SELL" and position:
                logger.info(f"🔴 SELL СИГНАЛ! {reason}")
                
                msg = f"""
🔴 <b>СИГНАЛ SELL</b>
📊 {reason}
💵 Цена: {current_price:.2f} USDT
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                """
                send_telegram(msg)
                
                logger.info("🚀 АВТОМАТИЧЕСКАЯ ПРОДАЖА...")
                result = execute_trade("SELL")
                if result and "error" not in result:
                    logger.info(f"✅ Сделка выполнена! Баланс: {balance:.2f}")
                else:
                    logger.error(f"❌ Ошибка: {result.get('error')}")
            
            # ➕ НОВОЕ: ПРИНУДИТЕЛЬНАЯ ПРОДАЖА ПРИ СЛАБОМ СИГНАЛЕ
            elif position and score < 1 and gain < 0.5:
                logger.info(f"⚠️ СЛАБЫЙ СИГНАЛ ({score:.1f}), ПРОДАЖА ПОЗИЦИИ")
                
                msg = f"""
⚠️ <b>ПРОДАЖА ПО СЛАБОМУ СИГНАЛУ</b>
📊 Сила сигнала: {score:.1f}
💵 Цена: {current_price:.2f} USDT
📊 Причина: рынок теряет импульс
                """
                send_telegram(msg)
                
                result = execute_trade("SELL")
                if result and "error" not in result:
                    logger.info(f"✅ Продажа по слабому сигналу! Баланс: {balance:.2f}")
                else:
                    logger.error(f"❌ Ошибка: {result.get('error')}")
            
            else:
                logger.info(f"⏸️ {reason}")
            
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            send_telegram(f"⚠️ <b>Ошибка в боте:</b> {str(e)}")
        
        time.sleep(CHECK_INTERVAL)

# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🤖 УМНЫЙ БОТ С НОВЫМИ ПОРОГАМИ ЗАПУЩЕН")
    logger.info(f"📊 Символ: {SYMBOL}")
    logger.info(f"💰 Баланс: {balance:.2f} USDT")
    logger.info(f"📉 Риск: {RISK_PERCENT}% (МАКС {balance * (RISK_PERCENT / 100):.2f} USDT)")
    logger.info(f"📈 Частичная фиксация: {TP1}%, {TP2}%, трейлинг с {TP3}%")
    logger.info("📱 Telegram уведомления: ВКЛЮЧЕНЫ")
    logger.info("📊 Ежечасный отчет: ВКЛЮЧЕН")
    logger.info("📊 Отчет каждые 15 минут: ВКЛЮЧЕН")
    logger.info("🔄 Принудительная продажа при слабом сигнале: ВКЛЮЧЕНА")
    logger.info("=" * 60)
    
    thread = threading.Thread(target=check_market, daemon=True)
    thread.start()
    
    app.run(host="0.0.0.0", port=5000)
