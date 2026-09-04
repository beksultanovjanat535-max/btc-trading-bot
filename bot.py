from flask import Flask, jsonify
import requests
from datetime import datetime

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "bot": "BTC Price Bot",
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
