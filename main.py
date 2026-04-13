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
RSI_TIME_WINDOW = 60 * 3            # 3 минуты данных

# ========== НАСТРОЙКИ TEMA + MACD ==========
TEMA_PERIOD = 9                     # Период TEMA
MACD_FAST = 12                      # Быстрая EMA
MACD_SLOW = 26                      # Медленная EMA
MACD_SIGNAL = 9                     # Сигнальная линия
TEMA_MACD_TIME_WINDOW = 60 * 5      # 5 минут данных для анализа

# ========== ОБЩИЕ НАСТРОЙКИ ==========
MIN_PRICE = 0.01
MAX_ALERTS_PER_DAY = 10
SCAN_INTERVAL = 3

# Защита от частых сигналов
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
historical_data_rsi = {}
historical_data_tema = {}


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


def can_send_alert(chat_id, symbol, indicator):
    if chat_id not in users or not users[chat_id]['active']:
        return False

    reset_daily_counters(chat_id)
    key = f"{symbol}_{indicator}"
    count = users[chat_id]['daily_alerts']['counts'].get(key, 0)
    if count >= MAX_ALERTS_PER_DAY:
        return False
    users[chat_id]['daily_alerts']['counts'][key] = count + 1
    return True


def send_telegram_notification(chat_id, message, symbol, exchange, indicator):
    if not can_send_alert(chat_id, symbol, indicator):
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


# ==================== RSI ФУНКЦИИ ====================
def calculate_rsi(prices):
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
    if len(price_values) < RSI_PERIOD + 1:
        return False, None, None
    
    if current_price < MIN_PRICE:
        return False, None, None
    
    rsi = calculate_rsi(price_values)
    if rsi is None or rsi < RSI_OVERBOUGHT_THRESHOLD:
        return False, None, None
    
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


# ==================== TEMA + MACD ФУНКЦИИ ====================
def calculate_ema(prices, period):
    """Расчёт EMA"""
    if len(prices) < period:
        return None
    
    multiplier = 2 / (period + 1)
    ema = prices[0]
    
    for price in prices[1:]:
        ema = (price - ema) * multiplier + ema
    
    return ema


def calculate_ema_series(prices, period):
    """Расчёт серии EMA для всех цен"""
    if len(prices) < period:
        return None
    
    multiplier = 2 / (period + 1)
    ema_values = [prices[0]]
    
    for price in prices[1:]:
        ema = (price - ema_values[-1]) * multiplier + ema_values[-1]
        ema_values.append(ema)
    
    return ema_values


def calculate_tema(prices, period=TEMA_PERIOD):
    """Triple Exponential Moving Average"""
    if len(prices) < period * 3:
        return None
    
    # EMA1
    ema1 = calculate_ema_series(prices, period)
    if ema1 is None:
        return None
    
    # EMA2 (от EMA1)
    ema2 = calculate_ema_series(ema1, period)
    if ema2 is None:
        return None
    
    # EMA3 (от EMA2)
    ema3 = calculate_ema_series(ema2, period)
    if ema3 is None:
        return None
    
    # TEMA = 3*EMA1 - 3*EMA2 + EMA3
    tema = [3*ema1[i] - 3*ema2[i] + ema3[i] for i in range(len(prices))]
    
    return tema


def calculate_macd(prices):
    """Расчёт MACD и сигнальной линии"""
    if len(prices) < MACD_SLOW + MACD_SIGNAL:
        return None, None, None
    
    # Быстрая EMA (12)
    fast_ema = calculate_ema_series(prices, MACD_FAST)
    # Медленная EMA (26)
    slow_ema = calculate_ema_series(prices, MACD_SLOW)
    
    if fast_ema is None or slow_ema is None:
        return None, None, None
    
    # MACD линия = Fast - Slow
    macd_line = [fast_ema[i] - slow_ema[i] for i in range(len(prices))]
    
    # Сигнальная линия (EMA от MACD, период 9)
    signal_line = calculate_ema_series(macd_line, MACD_SIGNAL)
    
    if signal_line is None:
        return None, None, None
    
    # Гистограмма
    histogram = [macd_line[i] - signal_line[i] for i in range(len(prices))]
    
    return macd_line, signal_line, histogram


def check_tema_macd_signal(prices, current_price):
    """
    Сигнал для шорта:
    - Цена ниже TEMA (медвежий тренд)
    - MACD пересекает сигнальную линию сверху вниз
    """
    if len(prices) < 50:
        return False, None
    
    if current_price < MIN_PRICE:
        return False, None
    
    # Рассчитываем TEMA
    tema_values = calculate_tema(prices)
    if tema_values is None or len(tema_values) < 10:
        return False, None
    
    # Рассчитываем MACD
    macd_line, signal_line, histogram = calculate_macd(prices)
    if macd_line is None or signal_line is None:
        return False, None
    
    current_tema = tema_values[-1]
    current_macd = macd_line[-1]
    current_signal = signal_line[-1]
    current_histogram = histogram[-1]
    prev_histogram = histogram[-2] if len(histogram) > 1 else 0
    
    # Условия для шорта:
    # 1. Цена ниже TEMA
    price_below_tema = current_price < current_tema
    
    # 2. MACD пересекает сигнал сверху вниз (медвежий крест)
    macd_cross_bearish = (macd_line[-2] > signal_line[-2] and 
                          current_macd < current_signal)
    
    # 3. Гистограмма отрицательная
    histogram_negative = current_histogram < 0
    
    if price_below_tema and macd_cross_bearish and histogram_negative:
        # Оценка силы сигнала
        if current_histogram < prev_histogram * 1.2:
            strength_emoji = "🔴🔴"
            strength_text = "СИЛЬНЫЙ"
        else:
            strength_emoji = "🟠"
            strength_text = "СРЕДНИЙ"
        
        details = {
            'current_price': current_price,
            'current_tema': current_tema,
            'macd': current_macd,
            'signal': current_signal,
            'histogram': current_histogram,
            'price_vs_tema': ((current_price - current_tema) / current_tema) * 100,
            'strength_emoji': strength_emoji,
            'strength_text': strength_text
        }
        
        return True, details
    
    return False, None


# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С БИРЖАМИ ====================
def fetch_binance_klines(symbol, interval='1m', limit=60):
    """Получение свечей с Binance"""
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': interval, 'limit': limit}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            if data and len(data) > 0:
                prices = [float(candle[4]) for candle in data]
                return prices
    except Exception as e:
        print(f"Ошибка Binance {symbol}: {e}")
    return None


def fetch_bybit_klines(symbol, interval='1', limit=60):
    """Получение свечей с Bybit"""
    try:
        url = "https://api.bybit.com/v5/market/kline"
        params = {'category': 'linear', 'symbol': symbol, 'interval': interval, 'limit': limit}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            if data.get('retCode') == 0 and data.get('result', {}).get('list'):
                klines = data['result']['list']
                prices = [float(kline[3]) for kline in klines]
                return prices
    except Exception as e:
        print(f"Ошибка Bybit {symbol}: {e}")
    return None


def fetch_binance_symbols():
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


# ==================== RSI МОНИТОРИНГ ====================
def monitor_rsi(exchange_name, fetch_symbols_func, fetch_ticker_func):
    print(f"🚀 Запуск RSI на {exchange_name}...")

    symbols = fetch_symbols_func()
    if not symbols:
        print(f"{exchange_name} RSI: нет символов")
        return

    for symbol in symbols:
        key = f"{exchange_name}_{symbol}"
        if key not in historical_data_rsi:
            historical_data_rsi[key] = {'price': []}

    print(f"{exchange_name} RSI: мониторинг {len(symbols)} символов")

    last_alert_time = {}

    while True:
        try:
            for symbol in symbols:
                try:
                    current_price = fetch_ticker_func(symbol)
                    if current_price is None:
                        continue
                    
                    timestamp = int(datetime.now().timestamp())
                    key = f"{exchange_name}_{symbol}"
                    
                    historical_data_rsi[key]['price'].append({'value': current_price, 'timestamp': timestamp})
                    historical_data_rsi[key]['price'] = [
                        x for x in historical_data_rsi[key]['price']
                        if timestamp - x['timestamp'] <= RSI_TIME_WINDOW
                    ]
                    
                    price_entries = sorted(historical_data_rsi[key]['price'], key=lambda x: x['timestamp'])
                    price_values = [entry['value'] for entry in price_entries]
                    
                    is_signal, rsi, strength = check_rsi_signal(price_values, current_price)
                    
                    if is_signal and rsi is not None:
                        last_time = last_alert_time.get(symbol, 0)
                        if time.time() - last_time < MIN_TIME_BETWEEN_SIGNALS:
                            continue
                        
                        last_alert_time[symbol] = time.time()
                        
                        target1 = current_price * 0.991
                        target2 = current_price * 0.985
                        stop_loss = current_price * 1.005
                        
                        msg = (
                            f"{strength['strength_emoji']} <b>RSI ПЕРЕКУПЛЕННОСТЬ</b> {strength['strength_emoji']}\n\n"
                            f"Монета: <code>{symbol}</code> ({exchange_name})\n"
                            f"💰 Цена: {current_price:.8f}\n\n"
                            f"📊 RSI: {rsi:.1f} | {strength['strength_text']}\n\n"
                            f"🎯 TP1 (-0.9%): {target1:.8f}\n"
                            f"🎯 TP2 (-1.5%): {target2:.8f}\n"
                            f"⛔ Стоп: {stop_loss:.8f}"
                        )
                        
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
                                send_telegram_notification(chat_id, msg, symbol, exchange_name, "RSI")
                    
                except Exception as e:
                    print(f"{exchange_name} RSI ошибка {symbol}: {e}")
                    continue

            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"{exchange_name} RSI ошибка: {e}")
            time.sleep(10)


# ==================== TEMA+MACD МОНИТОРИНГ ====================
def monitor_tema_macd(exchange_name, fetch_symbols_func, fetch_klines_func, fetch_ticker_func):
    print(f"🚀 Запуск TEMA+MACD на {exchange_name}...")

    symbols = fetch_symbols_func()
    if not symbols:
        print(f"{exchange_name} TEMA+MACD: нет символов")
        return

    print(f"{exchange_name} TEMA+MACD: мониторинг {len(symbols)} символов")

    last_alert_time = {}

    while True:
        try:
            for symbol in symbols:
                try:
                    current_price = fetch_ticker_func(symbol)
                    if current_price is None:
                        continue
                    
                    prices = fetch_klines_func(symbol, limit=60)
                    if prices is None or len(prices) < 50:
                        continue
                    
                    is_signal, details = check_tema_macd_signal(prices, current_price)
                    
                    if is_signal and details:
                        last_time = last_alert_time.get(symbol, 0)
                        if time.time() - last_time < MIN_TIME_BETWEEN_SIGNALS:
                            continue
                        
                        last_alert_time[symbol] = time.time()
                        
                        target1 = current_price * 0.991
                        target2 = current_price * 0.985
                        stop_loss = current_price * 1.005
                        
                        msg = (
                            f"{details['strength_emoji']} <b>TEMA + MACD МЕДВЕЖИЙ КРОСС</b> {details['strength_emoji']}\n\n"
                            f"Монета: <code>{symbol}</code> ({exchange_name})\n"
                            f"💰 Цена: {current_price:.8f}\n\n"
                            f"📊 <b>Детали:</b>\n"
                            f"• TEMA: {details['current_tema']:.8f}\n"
                            f"• Цена vs TEMA: {details['price_vs_tema']:.2f}%\n"
                            f"• MACD: {details['macd']:.2f}\n"
                            f"• Сигнал: {details['signal']:.2f}\n"
                            f"• Качество: {details['strength_text']}\n\n"
                            f"🎯 TP1 (-0.9%): {target1:.8f}\n"
                            f"🎯 TP2 (-1.5%): {target2:.8f}\n"
                            f"⛔ Стоп: {stop_loss:.8f}"
                        )
                        
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
                                send_telegram_notification(chat_id, msg, symbol, exchange_name, "TEMA_MACD")
                    
                except Exception as e:
                    print(f"{exchange_name} TEMA+MACD ошибка {symbol}: {e}")
                    continue

            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"{exchange_name} TEMA+MACD ошибка: {e}")
            time.sleep(10)


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
                                'text': "✅ Вы подписались на сигналы!\n\n📊 <b>Доступные индикаторы:</b>\n\n1️⃣ <b>RSI</b>\n• Перекупленность 75+\n• Сразу сигнал на шорт\n\n2️⃣ <b>TEMA + MACD</b>\n• Цена ниже TEMA\n• MACD пересекает сигнал вниз\n\n📍 Биржи: Binance + Bybit\n🎯 Тейк: 0.9-1.5%",
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
                            'text': "🤖 <b>Команды бота:</b>\n\n/start - подписаться\n/stop - отписаться\n/help - справка\n\n📊 <b>Индикаторы:</b>\n• RSI (перекупленность 75+)\n• TEMA + MACD (медвежий крест)\n\n📍 Биржи: Binance, Bybit\n🎯 Тейк: 0.9-1.5%",
                            'parse_mode': 'HTML'
                        }
                        requests.post(url_send, json=payload, timeout=REQUEST_TIMEOUT)

            time.sleep(1)
        except Exception as e:
            print(f"Ошибка Telegram: {e}")
            time.sleep(5)


def send_shutdown_message():
    shutdown_msg = "🛑 <b>Бот остановлен</b>"
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {'chat_id': chat_id, 'text': shutdown_msg, 'parse_mode': 'HTML'}
                requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except Exception:
                pass


def main():
    print("=" * 60)
    print("КОМБИНИРОВАННЫЙ СКРИНЕР")
    print("RSI + TEMA + MACD")
    print("Биржи: Binance + Bybit")
    print("=" * 60)

    atexit.register(send_shutdown_message)

    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    time.sleep(2)

    startup_msg = (
        "🔍 <b>Бот запущен!</b>\n\n"
        "📊 <b>Активные индикаторы:</b>\n"
        "1️⃣ RSI (перекупленность 75+)\n"
        "2️⃣ TEMA + MACD (медвежий крест)\n\n"
        "📍 Биржи: Binance + Bybit\n"
        "🎯 Тейк: 0.9-1.5%"
    )
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {'chat_id': chat_id, 'text': startup_msg, 'parse_mode': 'HTML'}
                requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except Exception:
                pass

    # RSI потоки
    rsi_binance = threading.Thread(target=monitor_rsi, args=("Binance", fetch_binance_symbols, fetch_binance_ticker), daemon=True)
    rsi_bybit = threading.Thread(target=monitor_rsi, args=("Bybit", fetch_bybit_symbols, fetch_bybit_ticker), daemon=True)
    
    # TEMA+MACD потоки
    tema_binance = threading.Thread(target=monitor_tema_macd, args=("Binance", fetch_binance_symbols, fetch_binance_klines, fetch_binance_ticker), daemon=True)
    tema_bybit = threading.Thread(target=monitor_tema_macd, args=("Bybit", fetch_bybit_symbols, fetch_bybit_klines, fetch_bybit_ticker), daemon=True)

    rsi_binance.start()
    rsi_bybit.start()
    tema_binance.start()
    tema_bybit.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Остановка бота...")


if __name__ == "__main__":
    main()
