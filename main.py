import requests
import time
from datetime import datetime, date
import urllib.parse
import threading
import atexit

# Настройки
TELEGRAM_BOT_TOKEN = '7446722367:AAFfl-bNGvYiU6_GpNsFeRmo2ZNZMJRx47I'
PRICE_INCREASE_THRESHOLD = 2  # Порог для роста цены
PRICE_DECREASE_THRESHOLD = -50  # Порог для падения цены
TIME_WINDOW = 60 * 5
MAX_ALERTS_PER_DAY = 10

# Настройки запросов
REQUEST_TIMEOUT = 10  # Таймаут для всех запросов
MAX_RETRIES = 3  # Максимальное количество попыток
RETRY_DELAY = 2  # Задержка между попытками

# База данных пользователей (в памяти)
users = {
    '5296533274': {  # Пример пользователя
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
            time.sleep(RETRY_DELAY * (attempt + 1))  # Увеличиваем задержку с каждой попыткой

    return None


def generate_links(symbol):
    """Генерация ссылок на аналитические ресурсы"""
    clean_symbol = symbol.replace('USDT', '').replace('1000', '')
    # Для Coinglass TV используем формат Binance_НАЗВАНИЕUSDT
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

    # Используем моноширинный шрифт для символа
    monospace_symbol = f"<code>{symbol}</code>"
    
    # Заменяем символ в сообщении
    message = message.replace(symbol, monospace_symbol)

    links = generate_links(symbol)
    
    # Формируем сообщение с кликабельными ссылками (без лишних пробелов)
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
        'disable_web_page_preview': False,  # True - скрыть превью, False - показать
        'link_preview_options': {
            'is_disabled': False  # Явно разрешаем превью ссылок
        }
    }
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Ошибка отправки пользователю {chat_id}: {repr(e)}")
        return False


def calculate_change(old, new):
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


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
            # Проверяем, существует ли символ
            if 'code' in data and data['code'] == -1121:
                print(f"Символ {symbol} не найден на Binance")
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

        # Отправляем приветственное сообщение
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': "✅ Вы успешно подписались на уведомления о торговых сигналах!",
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
    shutdown_msg = "🛑 <b>Бот остановлен</b>\n\nМониторинг приостановлен. Для возобновления работы перезапустите бота."
    broadcast_message(shutdown_msg)
    print("Сообщение о выключении отправлено всем пользователям")


def handle_telegram_updates():
    """Обработка входящих сообщений от пользователей"""
    last_update_id = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {'timeout': 30, 'offset': last_update_id + 1}
            response = requests.get(url, params=params, timeout=35)  # Таймаут больше чем polling timeout
            data = response.json()

            if data['ok']:
                for update in data['result']:
                    last_update_id = update['update_id']

                    if 'message' not in update:
                        continue

                    message = update['message']
                    chat_id = str(message['chat']['id'])
                    text = message.get('text', '').strip().lower()

                    # Обработка команд
                    if text == '/start':
                        add_user(chat_id)
                    elif text == '/stop':
                        remove_user(chat_id)
                        # Отправляем сообщение о отписке
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
                        # Отправляем справку
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': "🤖 <b>Команды бота:</b>\n/start - подписаться на уведомления\n/stop - отписаться от уведомлений\n/help - показать эту справку",
                            'parse_mode': 'HTML'
                        }
                        try:
                            requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
                        except Exception as e:
                            print(f"Ошибка отправки справки: {e}")

            time.sleep(1)
        except requests.exceptions.Timeout:
            print("Таймаут при опросе Telegram API (это нормально)")
            continue
        except Exception as e:
            print(f"Ошибка обработки обновлений: {e}")
            time.sleep(5)


def monitor_exchange(exchange_name, fetch_symbols_func, fetch_ticker_func):
    """Мониторинг конкретной биржи"""
    print(f"Запуск мониторинга {exchange_name}...")

    symbols = fetch_symbols_func()
    if not symbols:
        print(f"Не удалось получить список символов с {exchange_name}")
        time.sleep(30)
        return

    # Ограничиваем количество символов для тестирования

    # Инициализация исторических данных для символов этой биржи
    for symbol in symbols:
        key = f"{exchange_name}_{symbol}"
        if key not in historical_data:
            historical_data[key] = {'price': []}

    print(f"Мониторинг {exchange_name}: {len(symbols)} символов")

    error_count = 0
    max_errors_before_reload = 10

    while True:
        try:
            successful_requests = 0
            for symbol in symbols:
                ticker_data = fetch_ticker_func(symbol)
                if ticker_data:
                    successful_requests += 1
                    error_count = 0  # Сбрасываем счетчик ошибок при успешном запросе

                    current_price = ticker_data['lastPrice']
                    timestamp = int(datetime.now().timestamp())
                    key = f"{exchange_name}_{symbol}"

                    # Обновляем данные цены
                    historical_data[key]['price'].append({'value': current_price, 'timestamp': timestamp})
                    historical_data[key]['price'] = [x for x in historical_data[key]['price']
                                                     if timestamp - x['timestamp'] <= TIME_WINDOW]

                    # Проверка изменения цены
                    if len(historical_data[key]['price']) > 1:
                        old_price = historical_data[key]['price'][0]['value']
                        price_change = calculate_change(old_price, current_price)

                        # Сигнал на рост цены
                        if price_change >= PRICE_INCREASE_THRESHOLD:
                            for chat_id in list(users.keys()):
                                if users[chat_id]['active']:
                                    alert_count = users[chat_id]['daily_alerts']['counts'].get(symbol, 0)
                                    msg = (f"🚨 <b>{symbol}</b> ({exchange_name})\n"
                                           f"📈 Рост цены: +{price_change:.2f}%\n"
                                           f"Было: {old_price:.4f}\n"
                                           f"Стало: {current_price:.4f}\n"
                                           f"Уведомлений: {alert_count}/{MAX_ALERTS_PER_DAY}")
                                    send_telegram_notification(chat_id, msg, symbol, exchange_name)

                        # Сигнал на падение цены
                        elif price_change <= PRICE_DECREASE_THRESHOLD:
                            for chat_id in list(users.keys()):
                                if users[chat_id]['active']:
                                    alert_count = users[chat_id]['daily_alerts']['counts'].get(symbol, 0)
                                    msg = (f"🔻 <b>{symbol}</b> ({exchange_name})\n"
                                           f"📉 Падение цены: {price_change:.2f}%\n"
                                           f"Было: {old_price:.4f}\n"
                                           f"Стало: {current_price:.4f}\n"
                                           f"Уведомлений: {alert_count}/{MAX_ALERTS_PER_DAY}")
                                    send_telegram_notification(chat_id, msg, symbol, exchange_name)
                else:
                    error_count += 1
                    if error_count >= max_errors_before_reload:
                        print(f"Слишком много ошибок на {exchange_name}, перезагружаем список символов...")
                        new_symbols = fetch_symbols_func()
                        if new_symbols:
                            symbols = new_symbols
                            if len(symbols) > 100:
                                symbols = symbols[:100]
                            print(f"Обновлен список символов: {len(symbols)} символов")
                        error_count = 0
                        break

            success_rate = (successful_requests / len(symbols)) * 100
            print(f"{exchange_name}: успешных запросов {successful_requests}/{len(symbols)} ({success_rate:.1f}%)")

            time.sleep(5)

        except Exception as e:
            print(f"Критическая ошибка мониторинга {exchange_name}: {repr(e)}")
            time.sleep(10)


def main():
    print("Запуск мониторинга цен с Binance и Bybit...")

    # Регистрируем функцию для отправки сообщения при выключении
    atexit.register(send_shutdown_message)

    # Запускаем обработчик сообщений в отдельном потоке
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    # Уведомление о запуске всем пользователям
    broadcast_message(
        "🔍 <b>Бот начал работу!</b>\n\nМониторинг цен активирован для Binance и Bybit с аналитическими ссылками!")
    print("Бот успешно запущен и отправил уведомление")

    # Запускаем мониторинг обеих бирж в отдельных потоках
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
        # Главный поток просто ждет завершения
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nОстановка бота...")
        # Сообщение о выключении отправится автоматически через atexit


if __name__ == "__main__":
    main()
