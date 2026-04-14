import requests
import time
from datetime import datetime, date
import threading
import atexit

# ==================== НАСТРОЙКИ ====================
TELEGRAM_BOT_TOKEN = '8514584009:AAFmnFff-9avc9mm-B9ZpR0AQcosUIaDb9g'

# Настройки скринера
PUMP_THRESHOLD = 2.0               # Памп от 2% за 5 минут
LONG_SHORT_THRESHOLD = 5           # Net Longs должны быть >= +5% (лонгов на 5% больше)
LONG_SHORT_CACHE_TIME = 300        # Кеш на 5 минут

MAX_ALERTS_PER_DAY = 10
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 2
SCAN_INTERVAL = 5                  # Проверка каждые 5 секунд

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

# Кеш для Long/Short данных
long_short_cache = {}
last_alert_time = {}


# ==================== ФУНКЦИИ ====================
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
    coinglass_symbol = f"Binance_{symbol}"
    return {
        'coinglass': f"https://www.coinglass.com/tv/{coinglass_symbol}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}",
        'binance': f"https://www.binance.com/ru/trade/{symbol}",
        'bybit': f"https://www.bybit.com/trade/usdt/{symbol}"
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
        print(f"Лимит для {symbol}: {users[chat_id]['daily_alerts']['counts'].get(symbol, 0)}/{MAX_ALERTS_PER_DAY}")
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
        'disable_web_page_preview': False
    }
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Ошибка отправки: {e}")
        return False


# ==================== LONG/SHORT RATIO (Bybit) ====================
def fetch_long_short_info(symbol):
    """Получает Net Longs/Shorts с Bybit (с кешем)"""
    try:
        current_time = time.time()
        clean_symbol = symbol.replace('USDT', '').replace('1000', '')
        
        # Проверяем кеш
        if clean_symbol in long_short_cache:
            cache_time, cached_info = long_short_cache[clean_symbol]
            if current_time - cache_time < LONG_SHORT_CACHE_TIME:
                return cached_info
        
        url = "https://api.bybit.com/v5/market/account-ratio"
        params = {
            "category": "linear",
            "symbol": clean_symbol,
            "period": "5min",
            "limit": 1
        }
        
        response = make_request_with_retry(url, params)
        if response and response.status_code == 200:
            data = response.json()
            if data.get('retCode') == 0 and data.get('result', {}).get('list'):
                item = data['result']['list'][0]
                buy_ratio = float(item.get('buyRatio', 0.5))
                sell_ratio = float(item.get('sellRatio', 0.5))
                net_delta = (buy_ratio - sell_ratio) * 100
                
                info = {
                    'long_pct': round(buy_ratio * 100, 1),
                    'short_pct': round(sell_ratio * 100, 1),
                    'net_delta': round(net_delta, 1),
                    'dominant': 'LONGS' if net_delta > 0 else 'SHORTS'
                }
                
                long_short_cache[clean_symbol] = (current_time, info)
                return info
    except Exception as e:
        print(f"Ошибка Long/Short для {symbol}: {e}")
    
    return None


# ==================== ПРОВЕРКА ПАМПА ====================
def check_pump(symbol, exchange):
    """Проверяет памп за 5 минут (без хранения истории)"""
    try:
        if exchange == "Binance":
            url = "https://api.binance.com/api/v3/klines"
        else:
            return False, 0  # Bybit не используем для пампа
        
        params = {'symbol': symbol, 'interval': '5m', 'limit': 2}
        response = make_request_with_retry(url, params)
        
        if response:
            data = response.json()
            if data and len(data) >= 2:
                price_5min_ago = float(data[0][4])  # Close 5 минут назад
                current_price = float(data[1][4])   # Текущая цена
                pump_pct = ((current_price - price_5min_ago) / price_5min_ago) * 100
                return pump_pct >= PUMP_THRESHOLD, pump_pct, current_price
    except Exception as e:
        print(f"Ошибка проверки пампа {symbol}: {e}")
    
    return False, 0, 0


# ==================== ПОЛУЧЕНИЕ СПИСКА СИМВОЛОВ ====================
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


# ==================== ОСНОВНОЙ МОНИТОРИНГ ====================
def monitor_pump_and_ls():
    """Мониторинг: памп 2% + Net Longs > Net Shorts"""
    print(f"🚀 Запуск скринера «Памп + Net Longs/Shorts»")
    print(f"📊 Условия: Памп от {PUMP_THRESHOLD}% за 5 минут + Net Longs >= {LONG_SHORT_THRESHOLD}%")
    
    symbols = fetch_binance_symbols()
    if not symbols:
        print("Не удалось получить символы")
        return
    
    print(f"Мониторинг {len(symbols)} символов")
    
    while True:
        try:
            for symbol in symbols:
                try:
                    current_time = time.time()
                    
                    # Защита от частых сигналов
                    if symbol in last_alert_time and current_time - last_alert_time[symbol] < 300:
                        continue
                    
                    # 1. Проверяем памп
                    is_pump, pump_pct, current_price = check_pump(symbol, "Binance")
                    
                    if not is_pump:
                        continue
                    
                    print(f"📈 Найден памп для {symbol}: +{pump_pct:.2f}%")
                    
                    # 2. Проверяем Long/Short Ratio
                    ls_info = fetch_long_short_info(symbol)
                    
                    if ls_info is None:
                        print(f"⚠️ Нет Long/Short данных для {symbol}, пропускаем")
                        continue
                    
                    print(f"📊 Long/Short {symbol}: {ls_info['long_pct']}% / {ls_info['short_pct']}% (Net: {ls_info['net_delta']:+.1f}%)")
                    
                    # 3. Проверяем условие: лонгов больше чем шортов на порог
                    if ls_info['net_delta'] < LONG_SHORT_THRESHOLD:
                        print(f"❌ Net Longs {ls_info['net_delta']:+.1f}% < {LONG_SHORT_THRESHOLD}%, пропускаем")
                        continue
                    
                    # ✅ ВСЕ УСЛОВИЯ ВЫПОЛНЕНЫ!
                    last_alert_time[symbol] = current_time
                    
                    # Рассчитываем цели для шорта
                    target1 = current_price * 0.991  # -0.9%
                    target2 = current_price * 0.985  # -1.5%
                    stop_loss = current_price * 1.005  # +0.5%
                    
                    net_emoji = "🟢" if ls_info['net_delta'] > 0 else "🔴"
                    
                    msg = (
                        f"🔴 <b>ПАМП + ДИСБАЛАНС</b> 🔴\n\n"
                        f"Монета: <code>{symbol}</code> (Binance)\n"
                        f"💰 Текущая цена: {current_price:.8f}\n\n"
                        f"📊 <b>Сигнал:</b>\n"
                        f"• Памп за 5 мин: +{pump_pct:.2f}%\n"
                        f"• {net_emoji} Net {ls_info['dominant']}: {ls_info['net_delta']:+.1f}%\n"
                        f"• Long/Short: {ls_info['long_pct']}% / {ls_info['short_pct']}%\n\n"
                        f"🎯 <b>Цели для шорта:</b>\n"
                        f"• TP1 (-0.9%): {target1:.8f}\n"
                        f"• TP2 (-1.5%): {target2:.8f}\n\n"
                        f"⛔ Стоп-лосс (+0.5%): {stop_loss:.8f}\n\n"
                        f"💡 <i>Лонгов больше чем шортов на {ls_info['net_delta']:+.1f}%, памп подтверждён</i>"
                    )
                    
                    for chat_id in list(users.keys()):
                        if users[chat_id]['active']:
                            send_telegram_notification(chat_id, msg, symbol, "Binance")
                    
                    print(f"✅ Сигнал отправлен для {symbol}")
                    
                except Exception as e:
                    print(f"Ошибка {symbol}: {e}")
                    continue
            
            time.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            print(f"Критическая ошибка: {e}")
            time.sleep(10)


# ==================== TELEGRAM ====================
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
                                'text': f"✅ Вы подписались на сигналы «Памп + Net Longs/Shorts»!\n\n📊 Условия:\n• Памп от {PUMP_THRESHOLD}% за 5 минут\n• Net Longs >= {LONG_SHORT_THRESHOLD}%\n\n🎯 Шорт 0.9-1.5%",
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
                            'text': "🤖 <b>Команды:</b>\n/start - подписаться\n/stop - отписаться\n/help - справка\n\n📈 Логика:\n1. Памп от 2% за 5 минут\n2. Net Longs > Net Shorts\n3. → Шорт 0.9-1.5%",
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
    print("СКРИНЕР «ПАМП + NET LONGS/SHORTS»")
    print(f"Условия: Памп {PUMP_THRESHOLD}% за 5 минут + Net Longs >= {LONG_SHORT_THRESHOLD}%")
    print("Без хранения истории, без ликвидаций")
    print("=" * 60)

    atexit.register(send_shutdown_message)

    # Запускаем обработчик Telegram
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    time.sleep(2)

    # Отправляем приветствие
    startup_msg = (
        f"🔍 <b>Скринер «Памп + Net Longs/Shorts» запущен!</b>\n\n"
        f"📊 Условия для шорта:\n"
        f"• Памп от {PUMP_THRESHOLD}% за 5 минут\n"
        f"• Net Longs >= {LONG_SHORT_THRESHOLD}%\n\n"
        f"🎯 Шорт 0.9-1.5%"
    )
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {'chat_id': chat_id, 'text': startup_msg, 'parse_mode': 'HTML'}
                requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except Exception:
                pass

    # Запускаем мониторинг
    monitor_pump_and_ls()


if __name__ == "__main__":
    main()
