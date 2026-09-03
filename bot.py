import os
import time
import logging
import random
from datetime import datetime
import threading

from flask import Flask, jsonify
from binance.client import Client

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
SYMBOL = "BTCUSDT"
RISK_PERCENT = 0.25
LEVERAGE = 3
CHECK_INTERVAL = 300  # 5 минут

# Flask приложение
app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "bot": f"{SYMBOL} Trading Bot",
        "version": "1.0",
        "timestamp": datetime.now().isoformat()
    })

@app.route("/status")
def status():
    return jsonify({
        "balance": get_balance(),
        "position": get_position(),
        "price": get_price(),
        "symbol": SYMBOL,
        "timestamp": datetime.now().isoformat()
    })

# API ключи
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

if not API_KEY or not API_SECRET:
    logger.error("❌ API ключи не найдены!")
    client = None
else:
    try:
        client = Client(API_KEY, API_SECRET)
        logger.info("✅ Binance client создан")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        client = None

def get_balance():
    """Получить баланс USDT"""
    try:
        if not client:
            return 0.0
        account = client.futures_account()
        for asset in account['assets']:
            if asset['asset'] == 'USDT':
                return float(asset['walletBalance'])
    except Exception as e:
        logger.error(f"Ошибка баланса: {e}")
    return 0.0

def get_price():
    """Получить текущую цену"""
    try:
        if not client:
            return None
        ticker = client.futures_symbol_ticker(symbol=SYMBOL)
        return float(ticker['price'])
    except Exception as e:
        logger.error(f"Ошибка цены: {e}")
        return None

def get_position():
    """Проверить открытую позицию"""
    try:
        if not client:
            return None
        positions = client.futures_position_information(symbol=SYMBOL)
        for pos in positions:
            if abs(float(pos['positionAmt'])) > 0:
                return {
                    "side": "LONG" if float(pos['positionAmt']) > 0 else "SHORT",
                    "size": float(pos['positionAmt']),
                    "entry_price": float(pos['entryPrice']),
                    "pnl": float(pos['unRealizedProfit'])
                }
    except Exception as e:
        logger.error(f"Ошибка позиции: {e}")
    return None

def set_leverage():
    """Установить плечо"""
    try:
        if not client:
            return False
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        logger.info(f"✅ Плечо {LEVERAGE}x установлено")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Не удалось установить плечо: {e}")
        return False

def open_trade(side):
    """Открыть сделку"""
    try:
        if not client:
            return False
        
        balance = get_balance()
        if balance <= 10:
            logger.error(f"❌ Баланс слишком мал: {balance:.2f} USDT")
            return False
        
        price = get_price()
        if not price:
            logger.error("❌ Не удалось получить цену")
            return False
        
        # Расчет размера позиции
        risk_money = balance * (RISK_PERCENT / 100)
        sl_distance = price * 0.02  # 2% стоп-лосс
        quantity = risk_money / sl_distance
        quantity = round(quantity, 3)
        
        if quantity < 0.001:
            logger.warning(f"⚠️ Объем {quantity} слишком мал")
            return False
        
        logger.info("=" * 50)
        logger.info(f"🚀 ОТКРЫТИЕ {side}")
        logger.info(f"💰 Баланс: {balance:.2f} USDT")
        logger.info(f"📊 Объем: {quantity:.3f} BTC")
        logger.info(f"💵 Цена: {price:.2f}")
        logger.info(f"📉 Риск: {risk_money:.2f} USDT ({RISK_PERCENT}%)")
        logger.info("=" * 50)
        
        # Открытие позиции
        order = client.futures_create_order(
            symbol=SYMBOL,
            side=side,
            type='MARKET',
            quantity=quantity
        )
        
        logger.info("✅ Ордер отправлен")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка открытия: {e}")
        return False

def check_market():
    """Проверка рынка"""
    try:
        logger.info("")
        logger.info("=" * 50)
        logger.info("🔍 ПРОВЕРКА РЫНКА")
        
        if not client:
            logger.error("❌ Client не инициализирован")
            return
        
        # Проверка баланса
        balance = get_balance()
        logger.info(f"💰 Баланс: {balance:.2f} USDT")
        
        if balance <= 10:
            logger.warning("⚠️ Баланс слишком мал")
            return
        
        # Проверка позиции
        position = get_position()
        if position:
            logger.info(f"📌 Позиция: {position['size']:.3f} BTC")
            logger.info(f"📊 Вход: {position['entry_price']:.2f}")
            logger.info(f"📈 PnL: {position['pnl']:.2f} USDT")
            return
        
        # Получаем цену
        price = get_price()
        if not price:
            logger.error("❌ Не удалось получить цену")
            return
        
        logger.info(f"📊 BTC: {price:.2f}")
        
        # ВРЕМЕННЫЙ СИГНАЛ (для теста)
        # В следующих шагах заменим на реальные индикаторы
        signal = random.choice(["BUY", "SELL", "NO"])
        
        if signal == "BUY":
            logger.info("🟢 СИГНАЛ BUY")
            open_trade("BUY")
        elif signal == "SELL":
            logger.info("🔴 СИГНАЛ SELL")
            open_trade("SELL")
        else:
            logger.info("⏸️ Нет сигнала")
            
        logger.info("=" * 50)
            
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")

# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    if not API_KEY or not API_SECRET:
        logger.error("=" * 50)
        logger.error("❌ API КЛЮЧИ НЕ УСТАНОВЛЕНЫ!")
        logger.error("")
        logger.error("Установите переменные окружения:")
        logger.error("  BINANCE_API_KEY = ваш_ключ")
        logger.error("  BINANCE_API_SECRET = ваш_секрет")
        logger.error("=" * 50)
    else:
        # Установка плеча
        set_leverage()
        
        logger.info("=" * 50)
        logger.info("🤖 БОТ ЗАПУЩЕН")
        logger.info(f"📊 Символ: {SYMBOL}")
        logger.info(f"⚡ Риск: {RISK_PERCENT}%")
        logger.info(f"⚡ Плечо: {LEVERAGE}x")
        logger.info(f"⏰ Проверка: каждые {CHECK_INTERVAL//60} минут")
        logger.info("=" * 50)
        
        # Запускаем Flask в отдельном потоке
        threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5000, debug=False), daemon=True).start()
        
        # Основной цикл
        while True:
            try:
                check_market()
                time.sleep(CHECK_INTERVAL)
            except KeyboardInterrupt:
                logger.info("🛑 Остановка бота")
                break
            except Exception as e:
                logger.error(f"💥 Критическая ошибка: {e}")
                time.sleep(60)
