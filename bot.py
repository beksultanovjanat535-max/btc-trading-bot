import os
import time
import logging
import json
import threading
from datetime import datetime

from flask import Flask, jsonify
from binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
SYMBOL = "BTCUSDT"
current_price = 0
balance = 0

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "bot": "BTC WebSocket Bot",
        "price": current_price,
        "balance": balance,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/status")
def status():
    return jsonify({
        "balance": balance,
        "price": current_price,
        "symbol": SYMBOL,
        "timestamp": datetime.now().isoformat()
    })

def message_handler(_, message):
    """Обработчик сообщений от WebSocket"""
    global current_price, balance
    try:
        data = json.loads(message)
        if 'p' in data:  # Обновление цены
            current_price = float(data['p'])
            logger.info(f"💰 BTC: {current_price:.2f}")
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")

def start_websocket():
    """Запуск WebSocket"""
    try:
        client = UMFuturesWebsocketClient(
            on_message=message_handler,
            is_combined=False
        )
        client.agg_trade(symbol=SYMBOL.lower())
        logger.info("✅ WebSocket подключен")
        
        # Держим соединение открытым
        while True:
            time.sleep(1)
            
    except Exception as e:
        logger.error(f"❌ WebSocket ошибка: {e}")
        time.sleep(5)

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🤖 БОТ С WEBSOCKET ЗАПУЩЕН")
    logger.info("📊 Подключение к Binance...")
    logger.info("=" * 50)
    
    # Запускаем WebSocket в отдельном потоке
    ws_thread = threading.Thread(target=start_websocket, daemon=True)
    ws_thread.start()
    
    # Запускаем Flask
    app.run(host="0.0.0.0", port=5000)
