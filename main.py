import requests
import time
from datetime import datetime, date
import urllib.parse
import threading
import atexit

# ==================== НАСТРОЙКИ RSI (ОПТИМАЛЬНЫЕ) ====================
TELEGRAM_BOT_TOKEN = '7572230525:AAFzAQsMe4DlTYAA8G5UgGnYH598ZxgZOjs'

# Основные настройки RSI
RSI_PERIOD = 14                     # Период для расчёта RSI
RSI_OVERBOUGHT_THRESHOLD = 75       # 75 - только сильная перекупленность
RSI_EXTREME_THRESHOLD = 85          # Экстремальная перекупленность (для сильных сигналов)
TIME_WINDOW = 60 * 3                # 3 минуты данных (быстрее реакция)

# Дополнительные фильтры
REQUIRE_RSI_DECLINE = True          # Требовать падение RSI
RSI_DECLINE_THRESHOLD = 2           # RSI должен упасть на 2+ пункта
REQUIRE_PRICE_DECLINE = True        # Требовать падение цены
MIN_PRICE = 0.01                    # Минимальная цена монеты
MIN_RSI_FOR_SIGNAL = 70             # Минимальный RSI для сигнала (мягкий порог)

# Лимиты
MAX_ALERTS_PER_DAY = 5              # Не более 5 сигналов на монету в день
SCAN_INTERVAL = 3                   # Проверка каждые 3 секунды

# Настройки запросов
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 2

# База данных пользователей (в памяти)
users = {
    '5296533274': {
        'active': True,
        'daily_alerts': {
            'date': date.today(),
            'counts': {}
        }
    }
}

# Глобальные структуры данных
historical_data = {}


def make_request_with_retry(url, params=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES):
    """Универсальная функция для запросов с повторными попытками"""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                return response
            else:
                print(f"Попытка {attempt + 1}: Ошибка HTTP {response.status_code} для {url}")
        except requests.exceptions.Timeout:
            print(f"Попытка {attempt + 1}: Таймаут подключения к {url}")
        except requests.exceptions.ConnectionError as e:
            print(f"Попытка {attempt + 1}: Ошибка подключения к {url}: {e}")
        except Exception as e:
            print(f"Попытка {attempt + 1}: Неожиданная ошибка для {url}: {e}")

        if attempt < max_retries - 1:
            time.sleep(RETRY_DELAY * (attempt + 1))

    return None


def generate_links(symbol):
    """Генерация ссылок на аналитические ресурсы"""
    clean_symbol = symbol.replace('USDT', '').replace('1000', '')
    coinglass_symbol = f"Binance_{symbol}"
    return {
        'coinglass': f"https://www.coinglass.com/tv/{coinglass_symbol}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}",
        'dextools': f"https://www.dextools.io/app/en/ether/pair-explorer/{clean_symbol}",
        'binance': f"https://www.binance.com/ru/trade/{symbol}",
        'bybit': f"https://www.bybit.com/trade/usdt/{symbol}"
    }


def reset_daily_counters(chat_id):
    today = date.today()
    if users[chat_id]['daily_alerts']['date'] != today:
        users[chat_id]['daily_alerts']['date'] = today
        users[chat_id]['daily_alerts']['counts'] = {}
        print(f"Счетчики уведомлений сброшены для пользователя {chat_id}")


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
        print(f"Лимит уведомлений достигнут для {symbol} ({exchange}) у пользователя {chat_id}")
        return False

    monospace_symbol = f"<code>{symbol}</code>"
    message = message.replace(symbol, monospace_symbol)

    links = generate_links(symbol)

    message_with_links = (
        f"{message}\n\n"
        f"🔗 <b>Быстрый анализ:</b>\n"
        f"• 📊 <a href='{links['coinglass']}'>Coinglass TV</a>\n"
        f"• 📈 <a href='{links['tradingview']}'>TradingView</a>\n"
        f"• 💰 <a href='{links['binance']}'>Binance</a>\n"
        f"• ⚡ <a href='{links['bybit']}'>Bybit</a>"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message_with_links,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False,
        'link_preview_options': {'is_disabled': False}
    }
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Ошибка отправки пользователю {chat_id}: {repr(e)}")
        return False


def calculate_rsi(prices):
    """
    Рассчитывает RSI по списку цен (закрытий).
    Возвращает значение RSI или None, если данных недостаточно.
    """
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

    # Первое сглаженное среднее
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
    Проверяет условия для сигнала с дополнительными фильтрами
    """
    if len(price_values) < RSI_PERIOD + 2:
        return False, None, None
    
    # Проверка минимальной цены
    if current_price < MIN_PRICE:
        return False, None, None
    
    # Рассчитываем RSI
    rsi = calculate_rsi(price_values)
    if rsi is None:
        return False, None, None
    
    # Проверка порога перекупленности
    if rsi < MIN_RSI_FOR_SIGNAL:
        return False, None, None
    
    # Проверка падения RSI (если включено)
    if REQUIRE_RSI_DECLINE and len(price_values) >= RSI_PERIOD + 3:
        rsi_prev = calculate_rsi(price_values[:-1])
        if rsi_prev is not None:
            rsi_decline = rsi_prev - rsi
            if rsi_decline < RSI_DECLINE_THRESHOLD:
                return False, None, None
    
    # Проверка падения цены (если включено)
    if REQUIRE_PRICE_DECLINE and len(price_values) >= 3:
        # Последняя цена должна быть ниже предпоследней
        if price_values[-1] >= price_values[-2]:
            return False, None, None
    
    # Определяем силу сигнала
    if rsi >= RSI_EXTREME_THRESHOLD:
        signal_strength = "extremely_strong"
        strength_emoji = "🔴🔴🔴"
        strength_text = "ЭКСТРЕМАЛЬНО СИЛЬНЫЙ"
    elif rsi >= RSI_OVERBOUGHT_THRESHOLD:
        signal_strength = "strong"
        strength_emoji = "🔴🔴"
        strength_text = "СИЛЬНЫЙ"
    else:
        signal_strength = "normal"
        strength_emoji = "🟠"
        strength_text = "СРЕДНИЙ"
    
    return True, rsi, {'strength': signal_strength, 'strength_emoji': strength_emoji, 'strength_text': strength_text}


def fetch_binance_symbols():
    """Получение списка символов с Binance"""
    url = "https://api.binance.com/api/v3/exchangeInfo"
    response = make_request_with_retry(url, timeout=15)
    if response:
        try:
            data = response.json()
            symbols = []
            for symbol_info in data['symbols']:
                if symbol_info['quoteAsset'] == 'USDT' and symbol_info['status'] == 'TRADING':
                    symbols.append(symbol_info['symbol'])
            print(f"Binance: получено {len(symbols)} символов")
            return symbols
        except Exception as e:
            print(f"Ошибка парсинга данных Binance: {e}")
    else:
        print("Не удалось получить символы с Binance после всех попыток")
    return []


def fetch_bybit_symbols():
    """Получение списка символов с Bybit"""
    url = "https://api.bybit.com/v5/market/instruments-info"
    params = {"category": "linear"}
    response = make_request_with_retry(url, params)
    if response:
        try:
            data = response.json()
            if data['retCode'] == 0:
                symbols = [item['symbol'] for item in data['result']['list']]
                print(f"Bybit: получено {len(symbols)} символов")
                return symbols
        except Exception as e:
            print(f"Ошибка парсинга данных Bybit: {e}")
    else:
        print("Не удалось получить символы с Bybit после всех попыток")
    return []


def fetch_binance_ticker(symbol):
    """Получение данных тикера с Binance"""
    url = "https://api.binance.com/api/v3/ticker/24hr"
    params = {"symbol": symbol}
    response = make_request_with_retry(url, params)
    if response:
        try:
            data = response.json()
            if 'code' in data and data['code'] == -1121:
                return None
            return {
                'symbol': data['symbol'],
                'lastPrice': float(data['lastPrice']),
                'priceChangePercent': float(data['priceChangePercent'])
            }
        except Exception as e:
            print(f"Ошибка парсинга тикера {symbol} с Binance: {e}")
    return None


def fetch_bybit_ticker(symbol):
    """Получение данных тикера с Bybit"""
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    response = make_request_with_retry(url, params)
    if response:
        try:
            data = response.json()
            if data['retCode'] == 0 and data['result']['list']:
                ticker = data['result']['list'][0]
                return {
                    'symbol': ticker['symbol'],
                    'lastPrice': float(ticker['lastPrice']),
                    'priceChangePercent': float(ticker['price24hPcnt']) * 100
                }
            else:
                print(f"Символ {symbol} не найден на Bybit: {data.get('retMsg', 'Unknown error')}")
        except Exception as e:
            print(f"Ошибка парсинга тикера {symbol} с Bybit: {e}")
    return None


def add_user(chat_id):
    """Добавление нового пользователя"""
    if chat_id not in users:
        users[chat_id] = {
            'active': True,
            'daily_alerts': {
                'date': date.today(),
                'counts': {}
            }
        }
        print(f"Добавлен новый пользователь: {chat_id}")
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': "✅ Вы успешно подписались на уведомления о перекупленности по RSI!\n\n📊 Настройки:\n• Порог RSI: 75+\n• Фильтр падения RSI\n• Фильтр падения цены\n• Только качественные сигналы",
            'parse_mode': 'HTML'
        }
        try:
            requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print(f"Ошибка отправки приветствия: {e}")
        return True
    return False


def remove_user(chat_id):
    """Удаление пользователя"""
    if chat_id in users:
        del users[chat_id]
        print(f"Пользователь {chat_id} удален")
        return True
    return False


def broadcast_message(message):
    """Отправка сообщения всем активным пользователям"""
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            try:
                requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except Exception as e:
                print(f"Ошибка отправки сообщения пользователю {chat_id}: {e}")


def send_shutdown_message():
    """Отправка сообщения о выключении бота"""
    shutdown_msg = "🛑 <b>Бот остановлен</b>\n\nМониторинг перекупленности RSI приостановлен."
    broadcast_message(shutdown_msg)
    print("Сообщение о выключении отправлено всем пользователям")


def handle_telegram_updates():
    """Обработка входящих сообщений от пользователей"""
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {'timeout': 30, 'offset': last_update_id + 1}
            response = requests.get(url, params=params, timeout=35)
            data = response.json()

            if data['ok']:
                for update in data['result']:
                    last_update_id = update['update_id']
                    if 'message' not in update:
                        continue
                    message = update['message']
                    chat_id = str(message['chat']['id'])
                    text = message.get('text', '').strip().lower()

                    if text == '/start':
                        add_user(chat_id)
                    elif text == '/stop':
                        remove_user(chat_id)
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': "❌ Вы отписались от уведомлений.",
                            'parse_mode': 'HTML'
                        }
                        try:
                            requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
                        except Exception as e:
                            print(f"Ошибка отправки сообщения: {e}")
                    elif text == '/help':
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': "🤖 <b>Команды бота RSI:</b>\n\n/start - подписаться на уведомления\n/stop - отписаться от уведомлений\n/help - показать справку\n\n📊 <b>Настройки:</b>\n• RSI период: 14\n• Порог перекупленности: 75\n• Фильтр падения RSI и цены\n• Только качественные сигналы",
                            'parse_mode': 'HTML'
                        }
                        try:
                            requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
                        except Exception as e:
                            print(f"Ошибка отправки справки: {e}")
            time.sleep(1)
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            print(f"Ошибка обработки обновлений: {e}")
            time.sleep(5)


def monitor_exchange(exchange_name, fetch_symbols_func, fetch_ticker_func):
    """Мониторинг конкретной биржи – расчёт RSI и сигналы перекупленности"""
    print(f"Запуск мониторинга {exchange_name}...")
    symbols = fetch_symbols_func()
    if not symbols:
        print(f"Не удалось получить список символов с {exchange_name}")
        time.sleep(30)
        return

    # Инициализация исторических данных
    for symbol in symbols:
        key = f"{exchange_name}_{symbol}"
        if key not in historical_data:
            historical_data[key] = {'price': []}

    print(f"Мониторинг {exchange_name}: {len(symbols)} символов")

    error_count = 0
    max_errors_before_reload = 10
    last_alert_time = {}

    while True:
        try:
            successful_requests = 0
            for symbol in symbols:
                ticker_data = fetch_ticker_func(symbol)
                if ticker_data:
                    successful_requests += 1
                    error_count = 0

                    current_price = ticker_data['lastPrice']
                    timestamp = int(datetime.now().timestamp())
                    key = f"{exchange_name}_{symbol}"

                    # Обновляем историю цен
                    historical_data[key]['price'].append({'value': current_price, 'timestamp': timestamp})
                    historical_data[key]['price'] = [
                        x for x in historical_data[key]['price']
                        if timestamp - x['timestamp'] <= TIME_WINDOW
                    ]

                    # Получаем список цен
                    price_entries = sorted(historical_data[key]['price'], key=lambda x: x['timestamp'])
                    price_values = [entry['value'] for entry in price_entries]

                    # Проверяем сигнал
                    is_signal, rsi, strength = check_rsi_signal(price_values, current_price)
                    
                    if is_signal and rsi is not None:
                        # Защита от частых сигналов (не чаще 1 раза в 5 минут)
                        last_time = last_alert_time.get(symbol, 0)
                        if time.time() - last_time < 300:
                            continue
                        
                        last_alert_time[symbol] = time.time()
                        
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
                                alert_count = users[chat_id]['daily_alerts']['counts'].get(symbol, 0)
                                
                                # Рассчитываем цели для шорта
                                target1 = current_price * 0.991  # -0.9%
                                target2 = current_price * 0.985  # -1.5%
                                stop_loss = current_price * 1.005  # +0.5%
                                
                                msg = (
                                    f"{strength['strength_emoji']} <b>RSI ПЕРЕКУПЛЕННОСТЬ</b> {strength['strength_emoji']}\n\n"
                                    f"Монета: <code>{symbol}</code> ({exchange_name})\n"
                                    f"💰 Текущая цена: {current_price:.8f}\n\n"
                                    f"📊 <b>Детали:</b>\n"
                                    f"• RSI: {rsi:.1f}\n"
                                    f"• Качество сигнала: {strength['strength_text']}\n"
                                    f"• Уведомлений сегодня: {alert_count}/{MAX_ALERTS_PER_DAY}\n\n"
                                    f"🎯 <b>Цели для шорта:</b>\n"
                                    f"• TP1 (-0.9%): {target1:.8f}\n"
                                    f"• TP2 (-1.5%): {target2:.8f}\n\n"
                                    f"⛔ Стоп-лосс (+0.5%): {stop_loss:.8f}\n\n"
                                    f"💡 <i>RSI указывает на перекупленность, ожидайте коррекцию</i>"
                                )
                                send_telegram_notification(chat_id, msg, symbol, exchange_name)
                else:
                    error_count += 1
                    if error_count >= max_errors_before_reload:
                        print(f"Слишком много ошибок на {exchange_name}, перезагружаем список символов...")
                        new_symbols = fetch_symbols_func()
                        if new_symbols:
                            symbols = new_symbols
                            print(f"Обновлен список символов: {len(symbols)} символов")
                        error_count = 0
                        break

            success_rate = (successful_requests / len(symbols)) * 100 if symbols else 0
            print(f"{exchange_name}: успешных запросов {successful_requests}/{len(symbols)} ({success_rate:.1f}%)")
            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"Критическая ошибка мониторинга {exchange_name}: {repr(e)}")
            time.sleep(10)


def main():
    print("=" * 50)
    print("RSI СКРИНЕР ПЕРЕКУПЛЕННОСТИ")
    print(f"Порог RSI: {RSI_OVERBOUGHT_THRESHOLD}")
    print("Фильтры: падение RSI + падение цены")
    print("Биржи: Binance + Bybit")
    print("=" * 50)
    
    atexit.register(send_shutdown_message)

    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    broadcast_message(
        "🔍 <b>RSI скринер перекупленности запущен!</b>\n\n"
        f"📊 Настройки:\n"
        f"• RSI период: {RSI_PERIOD}\n"
        f"• Порог перекупленности: {RSI_OVERBOUGHT_THRESHOLD}\n"
        f"• Фильтр падения RSI: {RSI_DECLINE_THRESHOLD} пункта\n"
        f"• Фильтр падения цены: включен\n\n"
        "🎯 Сигнал → шорт 0.9-1.5%"
    )
    print("Бот успешно запущен")

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
        print("\nОстановка бота...")


if __name__ == "__main__":
    main()
