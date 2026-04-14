import requests
import websocket
import json
import time
import threading
from datetime import datetime, date
import atexit

# ==================== НАСТРОЙКИ ====================
TELEGRAM_BOT_TOKEN = '8514584009:AAFmnFff-9avc9mm-B9ZpR0AQcosUIaDb9g'

# ========== НАСТРОЙКИ СКРИНЕРА ==========
PUMP_THRESHOLD = 2.0               # Памп от 2% за 5 минут
LIQUIDATION_THRESHOLD = 50000      # Минимальная сумма ликвидации $50k

# Общие настройки
MIN_PRICE = 0.01
MAX_ALERTS_PER_DAY = 10
MIN_TIME_BETWEEN_SIGNALS = 300

# Настройки запросов
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 2

# ==================== БАЗА ДАННЫХ ====================
users = {
    '5296533274': {
        'active': True,
        'daily_alerts': {
            'date': date.today(),
            'counts': {}
        }
    }
}

# Кеш для цен
price_cache = {}
last_alert_time = {}


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def make_request_with_retry(url, params=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                return response
        except Exception as e:
            print(f"Попытка {attempt + 1}: {e}")

        if attempt < max_retries - 1:
            time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def generate_links(symbol):
    clean_symbol = symbol.replace('USDT', '').replace('1000', '')
    return {
        'coinglass': f"https://www.coinglass.com/tv/Binance_{symbol}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}",
        'binance': f"https://www.binance.com/ru/trade/{symbol}",
        'bybit': f"https://www.bybit.com/trade/usdt/{symbol}"
    }


def send_telegram_notification(chat_id, message, symbol):
    monospace_symbol = f"<code>{symbol}</code>"
    message = message.replace(symbol, monospace_symbol)
    links = generate_links(symbol)

    message_with_links = (
        f"{message}\n\n"
        f"🔗 <b>Быстрый анализ:</b>\n"
        f"• 📊 <a href='{links['coinglass']}'>Coinglass TV</a>\n"
        f"• 📈 <a href='{links['tradingview']}'>TradingView</a>\n"
        f"• 💰 <a href='{links['binance']}'>Binance</a>"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message_with_links,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }
    try:
        requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        return True
    except Exception as e:
        print(f"Ошибка отправки: {e}")
        return False


# ==================== ПРОВЕРКА ПАМПА ====================
def check_pump(symbol, current_price):
    """Проверяет был ли памп за последние 5 минут"""
    try:
        # Получаем цену 5 минут назад
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': '5m', 'limit': 2}
        response = make_request_with_retry(url, params)
        
        if response:
            data = response.json()
            if data and len(data) >= 2:
                price_5min_ago = float(data[0][4])  # Close price 5 минут назад
                pump_pct = ((current_price - price_5min_ago) / price_5min_ago) * 100
                return pump_pct >= PUMP_THRESHOLD, pump_pct
    except Exception as e:
        print(f"Ошибка проверки пампа {symbol}: {e}")
    
    return False, 0


# ==================== WEBSOCKET ДЛЯ ЛИКВИДАЦИЙ (BinStream) ====================
def on_message(ws, message):
    try:
        data = json.loads(message)
        
        if 'e' in data and data['e'] == 'forceOrder':
            liq = data['o']
            symbol = liq.get('s', 'UNKNOWN')
            side = liq.get('S')  # 'BUY' = ликвидация SHORT, 'SELL' = ликвидация LONG
            quantity = float(liq.get('q', 0))
            price = float(liq.get('ap', 0))
            usd_value = quantity * price
            
            # Нас интересуют только ликвидации SHORT (когда цена растёт)
            if side == 'BUY' and usd_value >= LIQUIDATION_THRESHOLD:
                # Проверяем памп
                current_price = fetch_current_price(symbol)
                if current_price is None:
                    return
                
                is_pump, pump_pct = check_pump(symbol, current_price)
                
                if is_pump:
                    # Проверяем не было ли недавнего сигнала
                    current_time = time.time()
                    if symbol in last_alert_time and current_time - last_alert_time[symbol] < MIN_TIME_BETWEEN_SIGNALS:
                        return
                    
                    last_alert_time[symbol] = current_time
                    
                    # Рассчитываем цели
                    target1 = current_price * 0.991  # -0.9%
                    target2 = current_price * 0.985  # -1.5%
                    stop_loss = current_price * 1.005  # +0.5%
                    
                    msg = (
                        f"🔴 <b>ПАМП + ЛИКВИДАЦИЯ SHORT</b> 🔴\n\n"
                        f"Монета: <code>{symbol}</code>\n"
                        f"💰 Текущая цена: {current_price:.8f}\n\n"
                        f"📊 <b>Сигнал:</b>\n"
                        f"• Памп за 5 мин: +{pump_pct:.2f}%\n"
                        f"• Ликвидация SHORT: ${usd_value:,.0f}\n\n"
                        f"🎯 <b>Цели для шорта:</b>\n"
                        f"• TP1 (-0.9%): {target1:.8f}\n"
                        f"• TP2 (-1.5%): {target2:.8f}\n\n"
                        f"⛔ Стоп-лосс (+0.5%): {stop_loss:.8f}"
                    )
                    
                    for chat_id in list(users.keys()):
                        if users[chat_id]['active']:
                            send_telegram_notification(chat_id, msg, symbol)
                            
    except Exception as e:
        print(f"Ошибка обработки: {e}")


def on_error(ws, error):
    print(f"Ошибка WebSocket: {error}")
    time.sleep(5)


def on_close(ws, close_status_code, close_msg):
    print(f"Соединение закрыто. Переподключение...")
    time.sleep(2)
    connect_websocket()


def on_open(ws):
    print("✅ WebSocket подключен к Binance")
    send_startup_message()


def send_startup_message():
    msg = "🔍 <b>Скринер «Памп + Ликвидации» запущен!</b>\n\n📊 Условия:\n• Памп от 2% за 5 минут\n• Ликвидация SHORT от $50,000\n\n🎯 Шорт 0.9-1.5%"
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            send_telegram_notification(chat_id, msg, "SYSTEM")


def connect_websocket():
    try:
        ws = websocket.WebSocketApp(
            "wss://fstream.binance.com/ws/!forceOrder@arr",
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        ws.run_forever()
    except Exception as e:
        print(f"Ошибка подключения: {e}")
        time.sleep(5)
        connect_websocket()


def fetch_current_price(symbol):
    """Получает текущую цену с Binance"""
    try:
        url = "https://api.binance.com/api/v3/ticker/price"
        params = {"symbol": symbol}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            return float(data['price'])
    except Exception as e:
        print(f"Ошибка получения цены {symbol}: {e}")
    return None


# ==================== ОБРАБОТКА TELEGRAM ====================
def handle_telegram_updates():
    last_update_id = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {'timeout': 30, 'offset': last_update_id + 1}
            response = requests.get(url, params=params, timeout=35)
            data = response.json()

            if data.get('ok'):
                for update in data.get('result', []):
                    last_update_id = update['update_id']

                    if 'message' not in update:
                        continue

                    message = update['message']
                    chat_id = str(message['chat']['id'])
                    text = message.get('text', '').strip().lower()

                    if text == '/start':
                        if chat_id not in users:
                            users[chat_id] = {
                                'active': True,
                                'daily_alerts': {
                                    'date': date.today(),
                                    'counts': {}
                                }
                            }
                            print(f"✅ Новый пользователь: {chat_id}")

                            url_send = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            payload = {
                                'chat_id': chat_id,
                                'text': "✅ Вы подписались на сигналы «Памп + Ликвидации»!\n\n📊 Условия:\n• Памп от 2% за 5 минут\n• Ликвидация SHORT от $50,000\n\n🎯 Шорт 0.9-1.5%",
                                'parse_mode': 'HTML'
                            }
                            requests.post(url_send, json=payload, timeout=REQUEST_TIMEOUT)

                    elif text == '/stop':
                        if chat_id in users:
                            del users[chat_id]
                            print(f"❌ Пользователь {chat_id} удален")

                    elif text == '/help':
                        url_send = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': "🤖 <b>Памп + Ликвидации</b>\n\n/start - подписаться\n/stop - отписаться\n/help - справка\n\n📈 Логика:\n1. Ликвидация SHORT (цена растёт)\n2. Памп от 2% за 5 минут\n3. → Шорт 0.9-1.5%",
                            'parse_mode': 'HTML'
                        }
                        requests.post(url_send, json=payload, timeout=REQUEST_TIMEOUT)

            time.sleep(1)
        except Exception as e:
            print(f"Ошибка Telegram: {e}")
            time.sleep(5)


def send_shutdown_message():
    shutdown_msg = "🛑 <b>Скринер остановлен</b>"
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {'chat_id': chat_id, 'text': shutdown_msg, 'parse_mode': 'HTML'}
                requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except Exception:
                pass


# ==================== MAIN ====================
def main():
    print("=" * 60)
    print("СКРИНЕР «ПАМП + ЛИКВИДАЦИИ»")
    print("Биржа: Binance (WebSocket - один поток)")
    print("=" * 60)

    atexit.register(send_shutdown_message)

    # Запускаем обработчик Telegram
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    time.sleep(2)

    # Запускаем WebSocket
    connect_websocket()


if __name__ == "__main__":
    main()
