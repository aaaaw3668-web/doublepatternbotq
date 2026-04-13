import requests
import time
from datetime import datetime, date
import threading
import atexit

# ==================== НАСТРОЙКИ ====================
TELEGRAM_BOT_TOKEN = '7572230525:AAFzAQsMe4DlTYAA8G5UgGnYH598ZxgZOjs'

# Настройки для Double Top Scanner
DOUBLE_TOP_TOLERANCE = 0.003  # Допустимое отклонение между вершинами (0.3%)
DOUBLE_TOP_MIN_DROP = 0.005  # Минимальный спад между вершинами (0.5%)
DOUBLE_TOP_CONFIRMATION = 0.998  # Подтверждение пробоя (цена ниже второй вершины на 0.2%)
DOUBLE_TOP_LOOKBACK = 20  # Количество свечей для анализа (20 минут)
MAX_ALERTS_PER_DAY = 10
SCAN_INTERVAL = 5  # Интервал между сканированиями (секунды)

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


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def make_request_with_retry(url, params=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES):
    """Универсальная функция для запросов с повторными попытками"""
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
    """Генерация ссылок на аналитические ресурсы"""
    clean_symbol = symbol.replace('USDT', '').replace('1000', '')

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
        'exchange': exchange_link,
        'bybit': f"https://www.bybit.com/trade/usdt/{symbol}"
    }


def reset_daily_counters(chat_id):
    """Сброс ежедневных счётчиков"""
    today = date.today()
    if users[chat_id]['daily_alerts']['date'] != today:
        users[chat_id]['daily_alerts']['date'] = today
        users[chat_id]['daily_alerts']['counts'] = {}


def can_send_alert(chat_id, symbol):
    """Проверка лимита уведомлений"""
    if chat_id not in users or not users[chat_id]['active']:
        return False

    reset_daily_counters(chat_id)
    count = users[chat_id]['daily_alerts']['counts'].get(symbol, 0)
    if count >= MAX_ALERTS_PER_DAY:
        return False
    users[chat_id]['daily_alerts']['counts'][symbol] = count + 1
    return True


def send_telegram_notification(chat_id, message, symbol, exchange):
    """Отправка уведомления в Telegram"""
    if not can_send_alert(chat_id, symbol):
        print(f"Лимит уведомлений достигнут для {symbol} ({exchange})")
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
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Ошибка отправки: {e}")
        return False


# ==================== ПОИСК ДВОЙНОЙ ВЕРШИНЫ ====================
def find_double_top(price_values, tolerance=DOUBLE_TOP_TOLERANCE,
                    min_drop=DOUBLE_TOP_MIN_DROP,
                    confirmation=DOUBLE_TOP_CONFIRMATION):
    """
    Ищет паттерн "Двойная вершина" в ценовых данных.
    """
    if len(price_values) < 10:
        return False, None

    # Находим все локальные максимумы (пики)
    peaks = []
    for i in range(1, len(price_values) - 1):
        if price_values[i] > price_values[i - 1] and price_values[i] > price_values[i + 1]:
            if i > 1 and i < len(price_values) - 2:
                avg_neighbors = (price_values[i - 2] + price_values[i + 2]) / 2
                if avg_neighbors > 0 and abs(price_values[i] - avg_neighbors) / avg_neighbors > 0.001:
                    peaks.append((i, price_values[i]))

    if len(peaks) < 2:
        return False, None

    # Берём два последних пика
    peak2_idx, peak2_price = peaks[-1]
    peak1_idx, peak1_price = peaks[-2]

    if peak2_idx - peak1_idx < 3:
        return False, None

    # Пики близки по цене
    if peak1_price == 0:
        return False, None
    price_diff_pct = abs(peak1_price - peak2_price) / peak1_price
    if price_diff_pct > tolerance:
        return False, None

    # Ищем минимум между пиками
    valley_prices = price_values[peak1_idx:peak2_idx + 1]
    valley_price = min(valley_prices)

    # Глубина ложбины
    drop_pct = (peak1_price - valley_price) / peak1_price
    if drop_pct < min_drop:
        return False, None

    # Проверяем пробой
    current_price = price_values[-1]
    neckline = valley_price

    if current_price < neckline * confirmation:
        target_distance = (peak1_price - neckline) / neckline
        target_price = current_price - (current_price * target_distance)

        details = {
            'peak1_price': peak1_price,
            'peak2_price': peak2_price,
            'valley_price': valley_price,
            'current_price': current_price,
            'target_price': target_price,
            'drop_pct': drop_pct * 100,
            'pattern_strength': 'strong' if drop_pct > 0.01 else 'normal'
        }
        return True, details

    return False, None


# ==================== ФУНКЦИИ ДЛЯ BINANCE ====================
def fetch_binance_klines(symbol, interval='1m', limit=20):
    """Получение свечей с Binance"""
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': interval, 'limit': limit}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            if data and len(data) > 0:
                prices = [float(candle[4]) for candle in data]
                return prices, None
    except Exception as e:
        print(f"Ошибка Binance {symbol}: {e}")
    return None, None


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


# ==================== ФУНКЦИИ ДЛЯ BYBIT ====================
def fetch_bybit_klines(symbol, interval='1', limit=20):
    """Получение свечей с Bybit (interval: '1' = 1 минута)"""
    try:
        url = "https://api.bybit.com/v5/market/kline"
        params = {'category': 'linear', 'symbol': symbol, 'interval': interval, 'limit': limit}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            if data.get('retCode') == 0 and data.get('result', {}).get('list'):
                klines = data['result']['list']
                prices = [float(kline[3]) for kline in klines]  # Close price
                return prices, None
    except Exception as e:
        print(f"Ошибка Bybit {symbol}: {e}")
    return None, None


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


# ==================== УНИВЕРСАЛЬНЫЙ МОНИТОРИНГ ====================
def monitor_double_top(exchange_name, fetch_symbols_func, fetch_klines_func):
    """Мониторинг паттерна Двойная Вершина на бирже"""
    print(f"🚀 Запуск сканера Двойная Вершина на {exchange_name}...")

    symbols = fetch_symbols_func()
    if not symbols:
        print(f"{exchange_name}: не удалось получить символы")
        time.sleep(30)
        return

    print(f"{exchange_name}: сканирование {len(symbols)} символов")

    error_count = 0
    max_errors_before_reload = 20
    last_check_time = {}

    while True:
        try:
            successful_scans = 0
            pattern_count = 0

            for idx, symbol in enumerate(symbols):
                try:
                    current_time = time.time()
                    if symbol in last_check_time and current_time - last_check_time[symbol] < SCAN_INTERVAL:
                        continue

                    prices, _ = fetch_klines_func(symbol, limit=DOUBLE_TOP_LOOKBACK)

                    if prices and len(prices) >= 10:
                        successful_scans += 1
                        error_count = 0

                        is_pattern, details = find_double_top(prices)

                        if is_pattern and details:
                            pattern_count += 1

                            strength_emoji = "🔴" if details['pattern_strength'] == 'strong' else "🟠"
                            current_price = details['current_price']
                            target1 = current_price * 0.991
                            target2 = current_price * 0.985
                            stop_loss = details['peak2_price'] * 1.005

                            msg = (
                                f"{strength_emoji} <b>ДВОЙНАЯ ВЕРШИНА</b> {strength_emoji}\n\n"
                                f"Монета: <code>{symbol}</code> ({exchange_name})\n"
                                f"💰 Текущая цена: {current_price:.8f}\n\n"
                                f"📊 <b>Детали паттерна:</b>\n"
                                f"• Вершина 1: {details['peak1_price']:.8f}\n"
                                f"• Вершина 2: {details['peak2_price']:.8f}\n"
                                f"• Уровень шеи: {details['valley_price']:.8f}\n"
                                f"• Глубина спада: {details['drop_pct']:.2f}%\n\n"
                                f"🎯 <b>Цели для шорта:</b>\n"
                                f"• TP1 (-0.9%): {target1:.8f}\n"
                                f"• TP2 (-1.5%): {target2:.8f}\n\n"
                                f"⛔ Стоп-лосс: {stop_loss:.8f} (выше вершины 2)"
                            )

                            for chat_id in list(users.keys()):
                                if users[chat_id]['active']:
                                    send_telegram_notification(chat_id, msg, symbol, exchange_name)

                        last_check_time[symbol] = current_time
                    else:
                        error_count += 1

                    time.sleep(0.05)

                    if idx % 50 == 0 and idx > 0:
                        print(f"{exchange_name} прогресс: {idx}/{len(symbols)}")

                except Exception as e:
                    print(f"{exchange_name} ошибка {symbol}: {e}")
                    error_count += 1
                    continue

            scan_rate = (successful_scans / len(symbols)) * 100 if symbols else 0
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] {exchange_name}: {successful_scans}/{len(symbols)} ({scan_rate:.1f}%) | Двойных вершин: {pattern_count}")

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


# ==================== ОБРАБОТКА TELEGRAM ====================
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
                                'text': "✅ Вы подписались на сигналы «Двойная Вершина»!\n\n📊 Что отслеживается:\n• Паттерн разворота Двойная Вершина\n• Пробой уровня шеи\n\n📍 Биржи: Binance + Bybit\n🎯 Сигнал → шорт 0.9-1.5%",
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
                            'text': "🤖 <b>Двойная Вершина Бот</b>\n\n/start - подписаться\n/stop - отписаться\n/help - справка\n\n📈 Что ищет:\nПаттерн Двойная Вершина с пробоем уровня шеи.\n\n📍 Биржи: Binance, Bybit\n🎯 Тейк: 0.9-1.5%",
                            'parse_mode': 'HTML'
                        }
                        requests.post(url_send, json=payload, timeout=REQUEST_TIMEOUT)

            time.sleep(1)
        except Exception as e:
            print(f"Ошибка Telegram: {e}")
            time.sleep(5)


def send_shutdown_message():
    """Отправка сообщения о выключении"""
    shutdown_msg = "🛑 <b>Сканер Двойная Вершина остановлен</b>"
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
    print("=" * 50)
    print("СКАНЕР ДВОЙНАЯ ВЕРШИНА")
    print("Ищет: паттерн разворота")
    print("Биржи: Binance + Bybit")
    print("=" * 50)

    atexit.register(send_shutdown_message)

    # Запускаем обработчик Telegram
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    time.sleep(2)

    # Отправляем приветствие
    startup_msg = (
        "🔍 <b>Сканер Двойная Вершина запущен!</b>\n\n"
        "📊 Отслеживается паттерн разворота Двойная Вершина\n"
        "📍 Биржи: Binance + Bybit\n"
        "🎯 Сигнал → шорт 0.9-1.5%"
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
        target=monitor_double_top,
        args=("Binance", fetch_binance_symbols, fetch_binance_klines),
        daemon=True
    )

    bybit_thread = threading.Thread(
        target=monitor_double_top,
        args=("Bybit", fetch_bybit_symbols, fetch_bybit_klines),
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
