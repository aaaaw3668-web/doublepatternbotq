import requests
import time
from datetime import datetime, timedelta
import urllib.parse
import threading
import os
import re
from flask import Flask  # Добавили Flask для обхода спящего режима Render

# Инициализация веб-сервера
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running 24/7!", 200

def run_flask():
    # Render автоматически передает порт в переменную окружения PORT
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Настройки бота
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
if not TELEGRAM_BOT_TOKEN:
    print("✗ Ошибка: TELEGRAM_BOT_TOKEN не найден в переменных окружения!")
    exit(1)

OI_THRESHOLD = 500
PRICE_INCREASE_THRESHOLD = 2.5
PRICE_DECREASE_THRESHOLD = -31
TIME_WINDOW = 60 * 5
DAILY_ALERT_LIMIT = 10

session = requests.Session()
users = {'5296533274': {'active': True, 'alert_counts': {}}}
historical_data = {}

def get_ye_time():
    return datetime.utcnow() + timedelta(hours=5)

def get_alert_count(chat_id, symbol):
    if chat_id not in users: return 0
    return users[chat_id]['alert_counts'].get(symbol, 0)

def increment_alert_count(chat_id, symbol):
    if chat_id in users:
        users[chat_id]['alert_counts'][symbol] = get_alert_count(chat_id, symbol) + 1

def can_send_alert(chat_id, symbol):
    if chat_id not in users or not users[chat_id]['active']: return False
    if get_alert_count(chat_id, symbol) >= DAILY_ALERT_LIMIT: return False
    return True

def send_telegram_notification(chat_id, message, symbol):
    if not can_send_alert(chat_id, symbol): return False
    increment_alert_count(chat_id, symbol)
    current_count = get_alert_count(chat_id, symbol)
    monospace_symbol = f"<code>{symbol}</code>"
    
    def wrap_numbers(text):
        return re.sub(r'(-?\d+(?:\.\d+)?%)', r'<code>\1</code>', text)
    
    message = wrap_numbers(message)
    links = generate_links(symbol)
    message_with_links = (
        f"{message}\n\n"
        f"🔗 <b>Быстрый анализ:</b>\n"
        f"• 📊 <a href='{links['coinglass']}'>Coinglass TV</a>\n"
        f"• 📈 <a href='{links['tradingview']}'>TradingView</a>\n\n"
        f"📊 <b>Уведомлений по {monospace_symbol} за сегодня:</b> <code>{current_count}/{DAILY_ALERT_LIMIT}</code>"
    )
    message_with_links = message_with_links.replace(symbol, monospace_symbol)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': message_with_links, 'parse_mode': 'HTML', 'disable_web_page_preview': False}
    try:
        response = session.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"✗ Ошибка отправки: {repr(e)}")
        return False

def check_and_reset_at_midnight():
    last_reset_date = get_ye_time().date()
    while True:
        try:
            time.sleep(5)
            current_date = get_ye_time().date()
            if current_date > last_reset_date:
                for chat_id in users:
                    users[chat_id]['alert_counts'] = {}
                last_reset_date = current_date
        except Exception as e:
            print(f"✗ Ошибка сброса лимитов: {e}")
            time.sleep(10)

def calculate_change(old, new):
    if old == 0: return 0.0
    return ((new - old) / old) * 100

def fetch_perpetual_symbols():
    url = "https://api.bybit.com/v5/market/instruments-info"
    try:
        response = session.get(url, params={"category": "linear"}, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data['retCode'] == 0:
                return [item['symbol'] for item in data['result']['list'] if item['symbol'].endswith('USDT')]
    except Exception as e:
        print(f"✗ Ошибка символов: {e}")
    return []

def fetch_all_bybit_tickers():
    url = "https://api.bybit.com/v5/market/tickers"
    try:
        response = session.get(url, params={"category": "linear"}, timeout=15)
        if response.status_code == 200 and response.json()['retCode'] == 0:
            return response.json()['result']['list']
    except Exception as e:
        print(f"✗ Ошибка тикеров: {e}")
    return []

def generate_links(symbol):
    encoded_symbol = urllib.parse.quote(symbol)
    return {
        'coinglass': f"https://www.coinglass.com/tv/Binance_{encoded_symbol}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BYBIT%3A{encoded_symbol}"
    }

def handle_telegram_updates():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            response = session.get(url, params={'timeout': 20, 'offset': last_update_id + 1}, timeout=25)
            data = response.json()
            if data.get('ok'):
                for update in data['result']:
                    last_update_id = update['update_id']
                    if 'message' not in update: continue
                    chat_id = str(update['message']['chat']['id'])
                    text = update['message'].get('text', '').strip().lower()

                    if chat_id not in users and text == '/start':
                        users[chat_id] = {'active': True, 'alert_counts': {}}
            time.sleep(1)
        except Exception as e:
            time.sleep(5)

def main_loop():
    symbols = fetch_perpetual_symbols()
    if not symbols: return
    for symbol in symbols:
        historical_data[symbol] = {'oi': [], 'price': []}

    while True:
        try:
            tickers = fetch_all_bybit_tickers()
            if not tickers:
                time.sleep(10)
                continue
            timestamp = int(datetime.now().timestamp())

            for ticker in tickers:
                symbol = ticker['symbol']
                if symbol not in historical_data: continue
                try:
                    current_oi = float(ticker['openInterest'])
                    current_price = float(ticker['lastPrice'])
                except (ValueError, KeyError): continue

                # Проверка OI
                historical_data[symbol]['oi'].append({'value': current_oi, 'timestamp': timestamp})
                historical_data[symbol]['oi'] = [x for x in historical_data[symbol]['oi'] if timestamp - x['timestamp'] <= TIME_WINDOW]
                if len(historical_data[symbol]['oi']) > 1:
                    oi_change = calculate_change(historical_data[symbol]['oi'][0]['value'], current_oi)
                    if oi_change >= OI_THRESHOLD:
                        for chat_id in list(users.keys()):
                            send_telegram_notification(chat_id, f"📈 <b>{symbol}</b>\n📊 <b>Рост OI:</b> <code>+{oi_change:.2f}%</code>", symbol)

                # Проверка Цены
                historical_data[symbol]['price'].append({'value': current_price, 'timestamp': timestamp})
                historical_data[symbol]['price'] = [x for x in historical_data[symbol]['price'] if timestamp - x['timestamp'] <= TIME_WINDOW]
                if len(historical_data[symbol]['price']) > 1:
                    price_change = calculate_change(historical_data[symbol]['price'][0]['value'], current_price)
                    if price_change >= PRICE_INCREASE_THRESHOLD:
                        for chat_id in list(users.keys()):
                            send_telegram_notification(chat_id, f"🚨 <b>{symbol}</b>\n📈 <b>Рост цены:</b> <code>+{price_change:.2f}%</code>", symbol)
                    elif price_change <= PRICE_DECREASE_THRESHOLD:
                        for chat_id in list(users.keys()):
                            send_telegram_notification(chat_id, f"🔻 <b>{symbol}</b>\n📉 <b>Падение цены:</b> <code>{price_change:.2f}%</code>", symbol)
            time.sleep(15)
        except Exception as e:
            time.sleep(10)

if __name__ == "__main__":
    # 1. Запуск Flask в отдельном потоке (для Render)
    threading.Thread(target=run_flask, daemon=True).start()
    # 2. Запуск потоков бота
    threading.Thread(target=handle_telegram_updates, daemon=True).start()
    threading.Thread(target=check_and_reset_at_midnight, daemon=True).start()
    # 3. Основной цикл
    main_loop()
