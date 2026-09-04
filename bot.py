import os
import time
import logging
import requests
import threading
import random
from datetime import datetime
from flask import Flask, jsonify

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Конфигурация
SYMBOL = "BTCUSDT"
CHECK_INTERVAL = 300  # 5 минут
RISK_PERCENT = 0.25

# Глобальные переменные
current_price = 0
current_balance = 0
last_signal = "Нет сигнала"
signal_history = []

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "bot": "BTC Trading Bot",
        "version": "2.0",
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

@app.route("/status")
def status():
    return jsonify({
        "symbol": SYMBOL,
        "price": current_price,
        "balance": current_balance,
        "last_signal": last_signal,
        "signal_history": signal_history[-5:],  # Последние 5 сигналов
        "timestamp": datetime.now().isoformat()
    })

@app.route("/signals")
def get_signals():
    """Получить текущие сигналы"""
    signals = analyze_market()
    return jsonify({
        "symbol": SYMBOL,
        "price": current_price,
        "signals": signals,
        "timestamp": datetime.now().isoformat()
    })

def get_klines(limit=50):
    """Получить свечи"""
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval=5m&limit={limit}"
        response = requests.get(url)
        data = response.json()
        
        # Преобразуем в удобный формат
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

def analyze_market():
    """Анализ рынка"""
    global current_price
    
    # Получаем свечи
    klines = get_klines(50)
    if not klines or len(klines) < 30:
        return {"signal": "NO", "reason": "Недостаточно данных"}
    
    # Извлекаем цены закрытия
    closes = [c["close"] for c in klines]
    current_price = closes[-1]
    
    # Расчет индикаторов
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    rsi = calculate_rsi(closes, 14)
    atr = calculate_atr(klines, 14)
    
    # Анализ сигналов
    signal = "NO"
    reason = "Нет сигнала"
    
    # EMA сигналы
    prev_ema9 = calculate_ema(closes[:-1], 9)
    prev_ema21 = calculate_ema(closes[:-1], 21)
    
    # Проверка пересечения EMA
    if prev_ema9 <= prev_ema21 and ema9 > ema21 and rsi > 50:
        signal = "BUY"
        reason = f"EMA9 пересекла EMA21 вверх, RSI={rsi:.1f}"
    elif prev_ema9 >= prev_ema21 and ema9 < ema21 and rsi < 50:
        signal = "SELL"
        reason = f"EMA9 пересекла EMA21 вниз, RSI={rsi:.1f}"
    
    # Проверка RSI экстремумов
    if rsi > 70 and signal == "NO":
        signal = "SELL"
        reason = f"RSI перекупленность: {rsi:.1f}"
    elif rsi < 30 and signal == "NO":
        signal = "BUY"
        reason = f"RSI перепроданность: {rsi:.1f}"
    
    return {
        "signal": signal,
        "reason": reason,
        "indicators": {
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "rsi": round(rsi, 2),
            "atr": round(atr, 2)
        }
    }

def check_market():
    """Основная функция проверки рынка"""
    global last_signal, signal_history
    
    while True:
        try:
            logger.info("=" * 50)
            logger.info("🔍 ПРОВЕРКА РЫНКА")
            
            # Получаем цену
            try:
                response = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
                data = response.json()
                current_price = float(data["price"])
                logger.info(f"💰 Цена BTC: {current_price:.2f}")
            except Exception as e:
                logger.error(f"Ошибка получения цены: {e}")
                time.sleep(60)
                continue
            
            # Анализ рынка
            analysis = analyze_market()
            signal = analysis["signal"]
            reason = analysis["reason"]
            
            # Обновляем глобальные переменные
            last_signal = signal
            
            # Добавляем в историю
            signal_entry = {
                "time": datetime.now().isoformat(),
                "signal": signal,
                "price": current_price,
                "reason": reason
            }
            signal_history.append(signal_entry)
            if len(signal_history) > 100:
                signal_history.pop(0)
            
            # Вывод сигнала
            if signal == "BUY":
                logger.info(f"🟢 BUY СИГНАЛ! {reason}")
            elif signal == "SELL":
                logger.info(f"🔴 SELL СИГНАЛ! {reason}")
            else:
                logger.info(f"⏸️ Нет сигнала: {reason}")
            
            logger.info(f"📊 Индикаторы: EMA9={analysis['indicators']['ema9']}, EMA21={analysis['indicators']['ema21']}, RSI={analysis['indicators']['rsi']}")
            logger.info("=" * 50)
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🤖 БОТ С ТОРГОВЫМИ СИГНАЛАМИ ЗАПУЩЕН")
    logger.info(f"📊 Символ: {SYMBOL}")
    logger.info(f"⏰ Проверка: каждые {CHECK_INTERVAL//60} минут")
    logger.info("=" * 50)
    
    # Запускаем проверку в отдельном потоке
    thread = threading.Thread(target=check_market, daemon=True)
    thread.start()
    
    # Запускаем Flask
    app.run(host="0.0.0.0", port=5000)
