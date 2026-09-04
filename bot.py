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
RISK_PERCENT = 1.0  # 1% риск
LEVERAGE = 1  # Без плеча

# Глобальные переменные
current_price = 0
last_signal = "Нет сигнала"
signal_history = []
balance = 100  # Баланс 100 USDT
position = None

# ============================================================
# СТРАНИЦЫ (ЭНДПОИНТЫ)
# ============================================================

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "bot": "BTC Trading Bot",
        "version": "3.0",
        "balance": balance,
        "price": current_price,
        "last_signal": last_signal,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/status")
def status():
    """Полный статус бота"""
    return jsonify({
        "symbol": SYMBOL,
        "price": current_price,
        "balance": balance,
        "position": position,
        "last_signal": last_signal,
        "signal_history": signal_history[-10:],
        "timestamp": datetime.now().isoformat()
    })

@app.route("/signals")
def get_signals():
    """Текущие сигналы и индикаторы"""
    analysis = analyze_market()
    return jsonify({
        "symbol": SYMBOL,
        "price": current_price,
        "signals": analysis,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/price")
def get_price():
    """Текущая цена BTC"""
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
    """Получить свечи"""
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
    """Расчет EMA"""
    if len(data) < period:
        return data[-1] if data else 0
    
    multiplier = 2 / (period + 1)
    ema = data[0]
    for price in data[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_rsi(data, period=14):
    """Расчет RSI"""
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
    """Расчет ATR"""
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
    """Расчет Bollinger Bands"""
    if len(data) < period:
        return None, None, None
    
    sma = sum(data[-period:]) / period
    variance = sum((x - sma) ** 2 for x in data[-period:]) / period
    std_dev = variance ** 0.5
    
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    
    return upper, sma, lower

def calculate_macd(data):
    """Расчет MACD"""
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
    """Расчет уровней поддержки и сопротивления"""
    highs = [k["high"] for k in klines[-lookback:]]
    lows = [k["low"] for k in klines[-lookback:]]
    closes = [k["close"] for k in klines[-lookback:]]
    
    resistance = max(highs)
    support = min(lows)
    pivot = sum(closes) / len(closes)
    
    return support, pivot, resistance

# ============================================================
# АНАЛИЗ РЫНКА
# ============================================================

def analyze_market():
    """Полный анализ рынка со всеми индикаторами"""
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
    
    # 1. EMA
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    prev_ema9 = calculate_ema(closes[:-1], 9)
    prev_ema21 = calculate_ema(closes[:-1], 21)
    
    # 2. RSI
    rsi = calculate_rsi(closes, 14)
    
    # 3. ATR
    atr = calculate_atr(klines, 14)
    
    # 4. Bollinger Bands
    upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(closes, 20, 2)
    
    # 5. MACD
    macd, signal_line, histogram = calculate_macd(closes)
    
    # 6. Support/Resistance
    support, pivot, resistance = calculate_support_resistance(klines, 20)
    
    # ============================================================
    # ГЕНЕРАЦИЯ СИГНАЛОВ
    # ============================================================
    
    signals = []
    signal = "NO"
    reason = "Нет сигнала"
    
    # Сигнал 1: EMA пересечение
    if prev_ema9 <= prev_ema21 and ema9 > ema21:
        if rsi > 40:
            signals.append("BUY_EMA")
    elif prev_ema9 >= prev_ema21 and ema9 < ema21:
        if rsi < 60:
            signals.append("SELL_EMA")
    
    # Сигнал 2: RSI
    if rsi < 30:
        signals.append("BUY_RSI")
        if rsi < 25:
            signals.append("BUY_RSI_STRONG")
    elif rsi > 70:
        signals.append("SELL_RSI")
        if rsi > 75:
            signals.append("SELL_RSI_STRONG")
    
    # Сигнал 3: Bollinger Bands
    if lower_bb and current_price < lower_bb:
        signals.append("BUY_BB")
    elif upper_bb and current_price > upper_bb:
        signals.append("SELL_BB")
    
    # Сигнал 4: MACD
    if macd and signal_line:
        if macd > signal_line and histogram > 0:
            signals.append("BUY_MACD")
        elif macd < signal_line and histogram < 0:
            signals.append("SELL_MACD")
    
    # Сигнал 5: Support/Resistance
    if current_price <= support * 1.01:
        signals.append("BUY_SUPPORT")
    elif current_price >= resistance * 0.99:
        signals.append("SELL_RESISTANCE")
    
    # Определяем основной сигнал
    buy_signals = [s for s in signals if s.startswith("BUY")]
    sell_signals = [s for s in signals if s.startswith("SELL")]
    
    if len(buy_signals) > 0 and len(sell_signals) == 0:
        signal = "BUY"
        reason = f"Сигналы: {', '.join(buy_signals)}"
    elif len(sell_signals) > 0 and len(buy_signals) == 0:
        signal = "SELL"
        reason = f"Сигналы: {', '.join(sell_signals)}"
    elif len(buy_signals) > 0 and len(sell_signals) > 0:
        if "BUY_RSI_STRONG" in buy_signals or "BUY_SUPPORT" in buy_signals:
            signal = "BUY"
            reason = f"СИЛЬНЫЙ сигнал: {', '.join(buy_signals)}"
        elif "SELL_RSI_STRONG" in sell_signals or "SELL_RESISTANCE" in sell_signals:
            signal = "SELL"
            reason = f"СИЛЬНЫЙ сигнал: {', '.join(sell_signals)}"
    
    return {
        "signal": signal,
        "reason": reason,
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
# ТОРГОВЛЯ
# ============================================================

def execute_trade(side):
    """Выполнение сделки (симуляция)"""
    global balance, position, last_signal
    
    try:
        price = current_price
        if price == 0:
            return {"error": "Цена не доступна"}
        
        risk_money = balance * (RISK_PERCENT / 100)
        sl_distance = price * 0.02
        quantity = risk_money / sl_distance
        quantity = round(quantity, 3)
        
        if quantity < 0.001:
            return {"error": "Объем слишком мал"}
        
        if side == "BUY":
            cost = quantity * price
            if balance < cost:
                return {"error": "Недостаточно баланса"}
            
            balance -= cost
            position = {
                "side": "LONG",
                "quantity": quantity,
                "entry_price": price,
                "current_price": price,
                "pnl": 0
            }
            last_signal = "BUY"
            
            logger.info(f"🟢 ПОКУПКА: {quantity:.3f} BTC по {price:.2f}")
            return {
                "status": "success",
                "action": "BUY",
                "quantity": quantity,
                "price": price,
                "balance": balance,
                "position": position
            }
            
        elif side == "SELL":
            if not position:
                return {"error": "Нет позиции для продажи"}
            
            pnl = (price - position["entry_price"]) * position["quantity"]
            balance += position["quantity"] * price
            position = None
            last_signal = "SELL"
            
            logger.info(f"🔴 ПРОДАЖА: по {price:.2f}, PnL: {pnl:.2f}")
            return {
                "status": "success",
                "action": "SELL",
                "price": price,
                "pnl": pnl,
                "balance": balance,
                "position": position
            }
        
        return {"error": "Неверная сторона"}
        
    except Exception as e:
        logger.error(f"Ошибка торговли: {e}")
        return {"error": str(e)}

# ============================================================
# ОСНОВНАЯ ФУНКЦИЯ
# ============================================================

def check_market():
    """Основная функция проверки рынка"""
    global last_signal, signal_history, position, balance
    
    while True:
        try:
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
            
            analysis = analyze_market()
            signal = analysis["signal"]
            reason = analysis["reason"]
            indicators = analysis["indicators"]
            
            last_signal = signal
            
            signal_entry = {
                "time": datetime.now().isoformat(),
                "signal": signal,
                "price": current_price,
                "reason": reason,
                "indicators": indicators
            }
            signal_history.append(signal_entry)
            if len(signal_history) > 100:
                signal_history.pop(0)
            
            logger.info(f"📊 EMA9: {indicators['ema9']:.2f}")
            logger.info(f"📊 EMA21: {indicators['ema21']:.2f}")
            logger.info(f"📊 RSI: {indicators['rsi']:.2f}")
            logger.info(f"📊 ATR: {indicators['atr']:.2f}")
            if indicators.get('bb_upper'):
                logger.info(f"📊 BB: {indicators['bb_lower']:.2f} - {indicators['bb_middle']:.2f} - {indicators['bb_upper']:.2f}")
            if indicators.get('support'):
                logger.info(f"📊 S/R: S={indicators['support']:.2f}, R={indicators['resistance']:.2f}")
            
            if signal == "BUY":
                logger.info(f"🟢 BUY СИГНАЛ! {reason}")
                if not position:
                    logger.info("🚀 АВТОМАТИЧЕСКАЯ ПОКУПКА...")
                    result = execute_trade("BUY")
                    if result and "error" not in result:
                        logger.info(f"✅ Сделка выполнена! Баланс: {balance:.2f}")
                    else:
                        logger.error(f"❌ Ошибка: {result.get('error')}")
                
            elif signal == "SELL":
                logger.info(f"🔴 SELL СИГНАЛ! {reason}")
                if position:
                    logger.info("🚀 АВТОМАТИЧЕСКАЯ ПРОДАЖА...")
                    result = execute_trade("SELL")
                    if result and "error" not in result:
                        logger.info(f"✅ Сделка выполнена! Баланс: {balance:.2f}")
                    else:
                        logger.error(f"❌ Ошибка: {result.get('error')}")
                
            else:
                logger.info(f"⏸️ Нет сигнала: {reason}")
            
            if position:
                pnl = (current_price - position["entry_price"]) * position["quantity"]
                logger.info(f"📈 Позиция: {position['quantity']:.3f} BTC, PnL: {pnl:.2f} USDT")
            else:
                logger.info(f"💰 Баланс: {balance:.2f} USDT")
            
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
        
        time.sleep(CHECK_INTERVAL)

# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🤖 БОТ ЗАПУЩЕН")
    logger.info(f"📊 Символ: {SYMBOL}")
    logger.info(f"⏰ Проверка: каждые {CHECK_INTERVAL//60} минут")
    logger.info(f"💰 Баланс: {balance:.2f} USDT (ВИРТУАЛЬНЫЙ)")
    logger.info(f"📉 Риск: {RISK_PERCENT}% (МАКС {balance * (RISK_PERCENT / 100):.2f} USDT)")
    logger.info("=" * 60)
    
    thread = threading.Thread(target=check_market, daemon=True)
    thread.start()
    
    app.run(host="0.0.0.0", port=5000)
