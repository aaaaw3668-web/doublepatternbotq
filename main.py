import requests
import time
from datetime import datetime, date
import threading
import atexit

# ==================== НАСТРОЙКИ ====================
TELEGRAM_BOT_TOKEN = '7572230525:AAFzAQsMe4DlTYAA8G5UgGnYH598ZxgZOjs'

# ========== НАСТРОЙКИ RSI (ПРОСТАЯ ЛОГИКА) ==========
RSI_PERIOD = 14
RSI_OVERBOUGHT_THRESHOLD = 75       # Порог перекупленности (75+)
RSI_EXTREME_THRESHOLD = 85          # Экстремальная перекупленность (85+)
TIME_WINDOW = 60 * 3                # 3 минуты данных

# Дополнительные фильтры
MIN_PRICE = 0.01                    # Минимальная цена монеты
MAX_ALERTS_PER_DAY = 10             # Максимум сигналов на монету в день
SCAN_INTERVAL = 3                   # Проверка каждые 3 секунды

# Защита от частых сигналов (не чаще 1 раза в 5 минут)
MIN_TIME_BETWEEN_SIGNALS = 300      # 5 минут

# Настройки запросов
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 2

# База данных пользователей
users = {
    '5296533274': {
        'active': True,
        'daily_alerts': {
            'date': date.today(),
            'counts': {}
        }
    }
}

# Глобальные структуры
historical_data = {}


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


def generate_links(symbol, exchange="Binance"):
    if exchange == "Binance":
        coinglass_symbol = f"Binance_{symbol}"
        tradingview_symbol = f"BINANCE%3A{symbol}"
        exchange_link = f"https://www.binance.com/ru/trade/{symbol}"
    else:
        coinglass_symbol = f"Bybit_{symbol}"
        tradingview_symbol = f"BYBIT%3A{symbol}"
        exchange_link = f"https://www.bybit.com/trade/usdt/{symbol}"

    return {
        'coinglass': f"https://www.coinglass.com/tv/{coinglass_symbol}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol={tradingview_symbol}",
        'exchange': exchange_link
    }


def reset_daily_counters(chat_id):
    today = date.today()
    if users[chat_id]['daily_alerts']['date'] != today:
        users[chat_id]['daily_alerts']['date'] = today
        users[chat_id]['daily_alerts']['counts'] = {}


def can_send_alert(chat_id, symbol):
    if chat_id not in users or not users[chat_id]['active']:
        return False

    reset_daily_counters(chat_id)
    count = users[chat_id]['daily_alerts']['counts'].get(symbol, 0)
    if count >= MAX_ALERTS_PER_DAY:
        return False
    users[chat_id]['daily_alerts']['counts'][symbol] = count + 1
    return True


def send_telegram_notification(chat_id, message, symbol, exchange):
    if not can_send_alert(chat_id, symbol):
        return False

    monospace_symbol = f"<code>{symbol}</code>"
    message = message.replace(symbol, monospace_symbol)
    links = generate_links(symbol, exchange)

    message_with_links = (
        f"{message}\n\n"
        f"🔗 <b>Быстрый анализ:</b>\n"
        f"• 📊 <a href='{links['coinglass']}'>Coinglass TV</a>\n"
        f"• 📈 <a href='{links['tradingview']}'>TradingView</a>\n"
        f"• 💰 <a href='{links['exchange']}'>{exchange}</a>"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message_with_links,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        return True
    except Exception as e:
        print(f"Ошибка отправки: {e}")
        return False


def calculate_rsi(prices):
    """Расчёт RSI"""
    if len(prices) < RSI_PERIOD + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:RSI_PERIOD]) / RSI_PERIOD
    avg_loss = sum(losses[:RSI_PERIOD]) / RSI_PERIOD

    for i in range(RSI_PERIOD, len(gains)):
        avg_gain = (avg_gain * (RSI_PERIOD - 1) + gains[i]) / RSI_PERIOD
        avg_loss = (avg_loss * (RSI_PERIOD - 1) + losses[i]) / RSI_PERIOD

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def check_rsi_signal(price_values, current_price):
    """
    ПРОСТАЯ ЛОГИКА:
    - RSI перекуплен (>=75)
    - ВСЁ! Сигнал на шорт
    """
    if len(price_values) < RSI_PERIOD + 1:
        return False, None, None
    
    # Проверка минимальной цены
    if current_price < MIN_PRICE:
        return False, None, None
    
    # Расчёт RSI
    rsi = calculate_rsi(price_values)
    if rsi is None:
        return False, None, None
    
    # Простая проверка: RSI перекуплен?
    if rsi < RSI_OVERBOUGHT_THRESHOLD:
        return False, None, None
    
    # Определяем силу сигнала
    if rsi >= RSI_EXTREME_THRESHOLD:
        strength_emoji = "🔴🔴🔴"
        strength_text = "ЭКСТРЕМАЛЬНО СИЛЬНЫЙ"
    elif rsi >= RSI_OVERBOUGHT_THRESHOLD:
        strength_emoji = "🔴🔴"
        strength_text = "СИЛЬНЫЙ"
    else:
        strength_emoji = "🟠"
        strength_text = "СРЕДНИЙ"
    
    return True, rsi, {'strength_emoji': strength_emoji, 'strength_text': strength_text}


def fetch_binance_symbols():
    """Получение списка символов с Binance"""
    try:
        url = "https://api.binance.com/api/v3/exchangeInfo"
        response = make_request_with_retry(url, timeout=15)
        if response:
            data = response.json()
            symbols = []
            for symbol_info in data['symbols']:
                if symbol_info['quoteAsset'] == 'USDT' and symbol_info['status'] == 'TRADING':
                    symbols.append(symbol_info['symbol'])
            print(f"Binance: получено {len(symbols)} символов")
            return symbols
    except Exception as e:
        print(f"Ошибка Binance symbols: {e}")
    return []


def fetch_bybit_symbols():
    """Получение списка символов с Bybit"""
    try:
        url = "https://api.bybit.com/v5/market/instruments-info"
        params = {"category": "linear"}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            if data.get('retCode') == 0:
                symbols = [item['symbol'] for item in data['result']['list']]
                print(f"Bybit: получено {len(symbols)} символов")
                return symbols
    except Exception as e:
        print(f"Ошибка Bybit symbols: {e}")
    return []


def fetch_binance_ticker(symbol):
    """Получение цены с Binance"""
    try:
        url = "https://api.binance.com/api/v3/ticker/price"
        params = {"symbol": symbol}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            return float(data['price'])
    except Exception as e:
        print(f"Ошибка Binance ticker {symbol}: {e}")
    return None


def fetch_bybit_ticker(symbol):
    """Получение цены с Bybit"""
    try:
        url = "https://api.bybit.com/v5/market/tickers"
        params = {"category": "linear", "symbol": symbol}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            if data.get('retCode') == 0 and data.get('result', {}).get('list'):
                return float(data['result']['list'][0]['lastPrice'])
    except Exception as e:
        print(f"Ошибка Bybit ticker {symbol}: {e}")
    return None


def monitor_exchange(exchange_name, fetch_symbols_func, fetch_ticker_func):
    """Мониторинг RSI перекупленности"""
    print(f"🚀 Запуск RSI мониторинга на {exchange_name}...")
    print(f"📊 Логика: RSI >= {RSI_OVERBOUGHT_THRESHOLD} → ШОРТ")

    symbols = fetch_symbols_func()
    if not symbols:
        print(f"{exchange_name}: не удалось получить символы")
        time.sleep(30)
        return

    # Инициализация
    for symbol in symbols:
        key = f"{exchange_name}_{symbol}"
        if key not in historical_data:
            historical_data[key] = {'price': []}

    print(f"{exchange_name}: мониторинг {len(symbols)} символов")

    error_count = 0
    max_errors_before_reload = 10
    last_alert_time = {}

    while True:
        try:
            successful_requests = 0
            
            for symbol in symbols:
                try:
                    current_price = fetch_ticker_func(symbol)
                    if current_price is None:
                        error_count += 1
                        continue
                    
                    successful_requests += 1
                    error_count = 0
                    
                    timestamp = int(datetime.now().timestamp())
                    key = f"{exchange_name}_{symbol}"
                    
                    # Обновляем историю цен
                    historical_data[key]['price'].append({'value': current_price, 'timestamp': timestamp})
                    historical_data[key]['price'] = [
                        x for x in historical_data[key]['price']
                        if timestamp - x['timestamp'] <= TIME_WINDOW
                    ]
                    
                    # Получаем цены по порядку
                    price_entries = sorted(historical_data[key]['price'], key=lambda x: x['timestamp'])
                    price_values = [entry['value'] for entry in price_entries]
                    
                    # Проверяем сигнал
                    is_signal, rsi, strength = check_rsi_signal(price_values, current_price)
                    
                    if is_signal and rsi is not None:
                        # Защита от частых сигналов
                        last_time = last_alert_time.get(symbol, 0)
                        if time.time() - last_time < MIN_TIME_BETWEEN_SIGNALS:
                            continue
                        
                        last_alert_time[symbol] = time.time()
                        
                        # Рассчитываем цели
                        target1 = current_price * 0.991  # -0.9%
                        target2 = current_price * 0.985  # -1.5%
                        stop_loss = current_price * 1.005  # +0.5%
                        
                        msg = (
                            f"{strength['strength_emoji']} <b>RSI ПЕРЕКУПЛЕННОСТЬ</b> {strength['strength_emoji']}\n\n"
                            f"Монета: <code>{symbol}</code> ({exchange_name})\n"
                            f"💰 Текущая цена: {current_price:.8f}\n\n"
                            f"📊 <b>Детали:</b>\n"
                            f"• RSI: {rsi:.1f}\n"
                            f"• Порог: {RSI_OVERBOUGHT_THRESHOLD}\n"
                            f"• Качество: {strength['strength_text']}\n\n"
                            f"🎯 <b>Цели для шорта:</b>\n"
                            f"• TP1 (-0.9%): {target1:.8f}\n"
                            f"• TP2 (-1.5%): {target2:.8f}\n\n"
                            f"⛔ Стоп-лосс (+0.5%): {stop_loss:.8f}\n\n"
                            f"💡 <i>RSI перекуплен → ожидайте коррекцию вниз</i>"
                        )
                        
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
                                send_telegram_notification(chat_id, msg, symbol, exchange_name)
                    
                except Exception as e:
                    print(f"{exchange_name} ошибка {symbol}: {e}")
                    error_count += 1
                    continue
            
            # Логирование
            success_rate = (successful_requests / len(symbols)) * 100 if symbols else 0
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {exchange_name}: {successful_requests}/{len(symbols)} ({success_rate:.1f}%)")
            
            # Перезагрузка при ошибках
            if error_count >= max_errors_before_reload:
                print(f"{exchange_name}: перезагружаем символы...")
                new_symbols = fetch_symbols_func()
                if new_symbols:
                    symbols = new_symbols
                error_count = 0
            
            time.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            print(f"{exchange_name} критическая ошибка: {e}")
            time.sleep(10)


def handle_telegram_updates():
    """Обработка команд Telegram"""
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
                                'text': f"✅ Вы подписались на RSI сигналы!\n\n📊 <b>Простая логика:</b>\n• RSI >= {RSI_OVERBOUGHT_THRESHOLD}\n• Сразу сигнал на шорт\n\n📍 Биржи: Binance + Bybit\n🎯 Тейк: 0.9-1.5%\n⛔ Стоп: +0.5%",
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
                            'text': "🤖 <b>RSI Бот</b>\n\n/start - подписаться\n/stop - отписаться\n/help - справка\n\n📈 <b>Логика:</b>\nRSI достигает перекупленности → сигнал на шорт\n\n📍 Биржи: Binance, Bybit\n🎯 Тейк: 0.9-1.5%",
                            'parse_mode': 'HTML'
                        }
                        requests.post(url_send, json=payload, timeout=REQUEST_TIMEOUT)

            time.sleep(1)
        except Exception as e:
            print(f"Ошибка Telegram: {e}")
            time.sleep(5)


def send_shutdown_message():
    """Отправка сообщения о выключении"""
    shutdown_msg = "🛑 <b>RSI бот остановлен</b>"
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {'chat_id': chat_id, 'text': shutdown_msg, 'parse_mode': 'HTML'}
                requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except Exception:
                pass


def main():
    print("=" * 50)
    print("RSI СКРИНЕР ПЕРЕКУПЛЕННОСТИ")
    print(f"Логика: RSI >= {RSI_OVERBOUGHT_THRESHOLD} → ШОРТ")
    print("Биржи: Binance + Bybit")
    print("=" * 50)

    atexit.register(send_shutdown_message)

    # Запускаем обработчик Telegram
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    time.sleep(2)

    # Отправляем приветствие
    startup_msg = (
        f"🔍 <b>RSI бот запущен!</b>\n\n"
        f"📊 <b>Логика:</b>\n"
        f"• RSI >= {RSI_OVERBOUGHT_THRESHOLD}\n"
        f"• Сразу сигнал на шорт\n\n"
        f"📍 Биржи: Binance + Bybit\n"
        f"🎯 Тейк: 0.9-1.5%"
    )
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {'chat_id': chat_id, 'text': startup_msg, 'parse_mode': 'HTML'}
                requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except Exception:
                pass

    # Запускаем мониторинг для Binance и Bybit
    binance_thread = threading.Thread(
        target=monitor_exchange,
        args=("Binance", fetch_binance_symbols, fetch_binance_ticker),
        daemon=True
    )

    bybit_thread = threading.Thread(
        target=monitor_exchange,
        args=("Bybit", fetch_bybit_symbols, fetch_bybit_ticker),
        daemon=True
    )

    binance_thread.start()
    bybit_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Остановка бота...")


if __name__ == "__main__":
    main()
