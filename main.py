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
LIQUIDATION_THRESHOLD = 1000       # Минимальная сумма ликвидации $1000

# ========== НАСТРОЙКИ LONG/SHORT RATIO ==========
USE_LONG_SHORT_RATIO = True        # Включить Long/Short фильтр
LONG_SHORT_RATIO_THRESHOLD = 1.3   # Лонгов в 1.3 раза больше
LONG_SHORT_CACHE_TIME = 300        # Кешировать на 5 минут

# ========== НАСТРОЙКИ АДАПТИВНЫХ ЦЕЛЕЙ ==========
MIN_TP = 0.6
MAX_TP = 2.5
MIN_SL = 0.4
MAX_SL = 1.2

# Общие настройки
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

# Кеши и статистика
long_short_cache = {}
price_history_cache = {}
last_alert_time = {}

# Статистика для логов
stats = {
    'total_liquidations': 0,
    'short_liquidations': 0,
    'short_above_threshold': 0,
    'pump_checks': 0,
    'pump_found': 0,
    'ls_checks': 0,
    'ls_passed': 0,
    'signals_sent': 0,
    'last_heartbeat': time.time()
}


# ==================== ФУНКЦИЯ ЛОГИРОВАНИЯ ====================
def log(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if level == "ERROR":
        print(f"\033[91m[{timestamp}] ❌ {message}\033[0m")
    elif level == "SUCCESS":
        print(f"\033[92m[{timestamp}] ✅ {message}\033[0m")
    elif level == "WARNING":
        print(f"\033[93m[{timestamp}] ⚠️ {message}\033[0m")
    elif level == "LIQUIDATION":
        print(f"\033[94m[{timestamp}] 💀 {message}\033[0m")
    elif level == "SIGNAL":
        print(f"\033[95m[{timestamp}] 🔔 {message}\033[0m")
    elif level == "HEARTBEAT":
        print(f"\033[96m[{timestamp}] 💓 {message}\033[0m")
    else:
        print(f"[{timestamp}] 📢 {message}")


def log_stats():
    log(f"СТАТИСТИКА: Ликвидаций всего: {stats['total_liquidations']}, SHORT: {stats['short_liquidations']}, Выше ${LIQUIDATION_THRESHOLD}: {stats['short_above_threshold']}, Пампов найдено: {stats['pump_found']}/{stats['pump_checks']}, Long/Short пройдено: {stats['ls_passed']}/{stats['ls_checks']}, Сигналов: {stats['signals_sent']}", "HEARTBEAT")


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def make_request_with_retry(url, params=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                return response
        except Exception as e:
            log(f"Попытка {attempt + 1}: {e}", "WARNING")

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
        log(f"Лимит для {symbol}: {count}/{MAX_ALERTS_PER_DAY}", "WARNING")
        return False
    users[chat_id]['daily_alerts']['counts'][symbol] = count + 1
    return True


def send_telegram_notification(chat_id, message, symbol):
    if not can_send_alert(chat_id, symbol):
        return False

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
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            log(f"Уведомление отправлено в Telegram для {symbol}", "SUCCESS")
            stats['signals_sent'] += 1
            return True
        else:
            log(f"Ошибка Telegram: {response.status_code}", "ERROR")
    except Exception as e:
        log(f"Ошибка отправки: {e}", "ERROR")
    return False


# ==================== LONG/SHORT RATIO (Binance) ====================
def fetch_binance_long_short_ratio(symbol):
    try:
        current_time = time.time()
        clean_symbol = symbol.replace('USDT', '').replace('1000', '')
        
        if clean_symbol in long_short_cache:
            cache_time, cached_ratio, cached_long, cached_short = long_short_cache[clean_symbol]
            if current_time - cache_time < LONG_SHORT_CACHE_TIME:
                return cached_ratio, cached_long, cached_short
        
        url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
        params = {
            "symbol": clean_symbol,
            "period": "5m",
            "limit": 1
        }
        
        response = make_request_with_retry(url, params)
        if response and response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                ratio = float(data[0].get('longShortRatio', 1.0))
                long_account = float(data[0].get('longAccount', 50.0))
                short_account = float(data[0].get('shortAccount', 50.0))
                
                long_short_cache[clean_symbol] = (current_time, ratio, long_account, short_account)
                log(f"Long/Short {symbol}: {long_account:.0f}%/{short_account:.0f}% (ratio={ratio:.2f})")
                return ratio, long_account, short_account
    except Exception as e:
        log(f"Ошибка Long/Short для {symbol}: {e}", "ERROR")
    
    return None, None, None


# ==================== ПРОВЕРКА ПАМПА ====================
def check_pump(symbol, current_price):
    try:
        stats['pump_checks'] += 1
        
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': '5m', 'limit': 2}
        response = make_request_with_retry(url, params)
        
        if response:
            data = response.json()
            if data and len(data) >= 2:
                price_5min_ago = float(data[0][4])
                pump_pct = ((current_price - price_5min_ago) / price_5min_ago) * 100
                log(f"Памп {symbol}: +{pump_pct:.2f}% (нужно {PUMP_THRESHOLD}%)")
                
                if pump_pct >= PUMP_THRESHOLD:
                    stats['pump_found'] += 1
                    log(f"✅ Памп подтверждён для {symbol}: +{pump_pct:.2f}%", "SUCCESS")
                    return True, pump_pct
    except Exception as e:
        log(f"Ошибка проверки пампа {symbol}: {e}", "ERROR")
    
    return False, 0


def fetch_current_price(symbol):
    try:
        url = "https://api.binance.com/api/v3/ticker/price"
        params = {"symbol": symbol}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            return float(data['price'])
    except Exception as e:
        log(f"Ошибка получения цены {symbol}: {e}", "ERROR")
    return None


# ==================== РАСЧЁТ ЦЕЛЕЙ ====================
def calculate_adaptive_targets(pump_pct, liquidation_value, ls_ratio, current_price):
    pump_factor = min(1.5, pump_pct / 2.0)
    liq_factor = min(1.5, liquidation_value / 1000)  # Адаптируем под порог $1000
    ls_factor = min(1.5, 1.0 + (ls_ratio - 1.0) * 0.5) if ls_ratio else 1.0
    
    total_factor = pump_factor * liq_factor * ls_factor
    
    tp1_pct = max(MIN_TP, min(MAX_TP * 0.8, 0.8 * total_factor))
    tp2_pct = max(tp1_pct + 0.3, min(MAX_TP, 1.2 * total_factor))
    sl_pct = max(MIN_SL, min(MAX_SL, 0.5 + (pump_pct / 10)))
    
    log(f"Цели: TP1={tp1_pct:.2f}%, TP2={tp2_pct:.2f}%, SL={sl_pct:.2f}%")
    
    return {
        'tp1_pct': round(tp1_pct, 2),
        'tp2_pct': round(tp2_pct, 2),
        'sl_pct': round(sl_pct, 2),
        'tp1_price': current_price * (1 - tp1_pct / 100),
        'tp2_price': current_price * (1 - tp2_pct / 100),
        'sl_price': current_price * (1 + sl_pct / 100)
    }


# ==================== ОБРАБОТКА ЛИКВИДАЦИЙ ====================
def on_websocket_message(ws, message):
    try:
        data = json.loads(message)
        
        if 'e' in data and data['e'] == 'forceOrder':
            stats['total_liquidations'] += 1
            
            liq = data['o']
            symbol = liq.get('s', 'UNKNOWN')
            side = liq.get('S')
            quantity = float(liq.get('q', 0))
            price = float(liq.get('ap', 0))
            usd_value = quantity * price
            
            log(f"Ликвидация: {symbol} | Сторона: {side} | Сумма: ${usd_value:,.0f}", "LIQUIDATION")
            
            if side == 'BUY':
                stats['short_liquidations'] += 1
                
                if usd_value < LIQUIDATION_THRESHOLD:
                    log(f"Пропуск: сумма ${usd_value:,.0f} < порога ${LIQUIDATION_THRESHOLD}", "WARNING")
                    return
                
                stats['short_above_threshold'] += 1
                log(f"✅ Ликвидация SHORT на ${usd_value:,.0f} прошла фильтр!", "SUCCESS")
                
                current_time = time.time()
                if symbol in last_alert_time and current_time - last_alert_time[symbol] < MIN_TIME_BETWEEN_SIGNALS:
                    log(f"Пропуск: недавний сигнал для {symbol}", "WARNING")
                    return
                
                current_price = fetch_current_price(symbol)
                if current_price is None:
                    log(f"Не удалось получить цену для {symbol}", "ERROR")
                    return
                
                is_pump, pump_pct = check_pump(symbol, current_price)
                if not is_pump:
                    log(f"Памп не подтверждён для {symbol}", "WARNING")
                    return
                
                if USE_LONG_SHORT_RATIO:
                    stats['ls_checks'] += 1
                    ls_ratio, long_pct, short_pct = fetch_binance_long_short_ratio(symbol)
                    if ls_ratio is None or ls_ratio < LONG_SHORT_RATIO_THRESHOLD:
                        log(f"Long/Short не пройден: {ls_ratio:.2f} < {LONG_SHORT_RATIO_THRESHOLD}", "WARNING")
                        return
                    stats['ls_passed'] += 1
                    log(f"✅ Long/Short пройден: {long_pct:.0f}%/{short_pct:.0f}%", "SUCCESS")
                else:
                    ls_ratio, long_pct, short_pct = 1.5, 60, 40
                
                log(f"🎯 ВСЕ УСЛОВИЯ ВЫПОЛНЕНЫ! Отправляем сигнал для {symbol}", "SIGNAL")
                
                last_alert_time[symbol] = current_time
                targets = calculate_adaptive_targets(pump_pct, usd_value, ls_ratio, current_price)
                
                volume_ratio = usd_value / LIQUIDATION_THRESHOLD
                if volume_ratio >= 5:
                    strength = "🔴🔴🔴 ЭКСТРЕМАЛЬНО СИЛЬНЫЙ"
                elif volume_ratio >= 3:
                    strength = "🔴🔴 ОЧЕНЬ СИЛЬНЫЙ"
                elif volume_ratio >= 1.5:
                    strength = "🔴 СИЛЬНЫЙ"
                else:
                    strength = "🟠 СРЕДНИЙ"
                
                msg = (
                    f"{strength}\n\n"
                    f"Монета: <code>{symbol}</code>\n"
                    f"💰 Цена: {current_price:.8f}\n\n"
                    f"📊 <b>Сигнал:</b>\n"
                    f"• Памп за 5 мин: +{pump_pct:.2f}%\n"
                    f"• Ликвидация SHORT: ${usd_value:,.0f}\n"
                    f"• Long/Short: {long_pct:.0f}% / {short_pct:.0f}% (лонгов больше в {ls_ratio:.2f}x)\n\n"
                    f"🎯 <b>Цели для шорта:</b>\n"
                    f"• TP1 (-{targets['tp1_pct']}%): {targets['tp1_price']:.8f}\n"
                    f"• TP2 (-{targets['tp2_pct']}%): {targets['tp2_price']:.8f}\n\n"
                    f"⛔ Стоп-лосс (+{targets['sl_pct']}%): {targets['sl_price']:.8f}"
                )
                
                for chat_id in list(users.keys()):
                    if users[chat_id]['active']:
                        send_telegram_notification(chat_id, msg, symbol)
                    
    except Exception as e:
        log(f"Ошибка обработки: {e}", "ERROR")


def on_websocket_error(ws, error):
    log(f"Ошибка WebSocket: {error}", "ERROR")
    time.sleep(5)


def on_websocket_close(ws, close_status_code, close_msg):
    log(f"Соединение закрыто. Переподключение...", "WARNING")
    time.sleep(2)
    connect_websocket()


def on_websocket_open(ws):
    log("WebSocket подключен к Binance! Ожидание ликвидаций...", "SUCCESS")
    send_startup_message()


def send_startup_message():
    msg = (
        "🔍 <b>Скринер «Памп + Ликвидации + Дисбаланс» запущен!</b>\n\n"
        f"📊 <b>ТЕКУЩИЕ НАСТРОЙКИ:</b>\n"
        f"• Памп от {PUMP_THRESHOLD}% за 5 минут\n"
        f"• Ликвидация SHORT от ${LIQUIDATION_THRESHOLD:,}\n"
        f"• Long/Short фильтр: {'ВКЛЮЧЕН' if USE_LONG_SHORT_RATIO else 'ВЫКЛЮЧЕН'}\n\n"
        f"🎯 Шорт 0.6-2.5%"
    )
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            send_telegram_notification(chat_id, msg, "SYSTEM")


def heartbeat():
    """Поток для вывода статистики каждые 30 секунд"""
    while True:
        time.sleep(30)
        log_stats()


def connect_websocket():
    try:
        ws = websocket.WebSocketApp(
            "wss://fstream.binance.com/ws/!forceOrder@arr",
            on_open=on_websocket_open,
            on_message=on_websocket_message,
            on_error=on_websocket_error,
            on_close=on_websocket_close
        )
        ws.run_forever()
    except Exception as e:
        log(f"Ошибка подключения: {e}", "ERROR")
        time.sleep(5)
        connect_websocket()


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
                            log(f"Новый пользователь: {chat_id}", "SUCCESS")

                            url_send = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            payload = {
                                'chat_id': chat_id,
                                'text': f"✅ Вы подписались на сигналы!\n\n📊 ТЕКУЩИЕ НАСТРОЙКИ:\n• Памп от {PUMP_THRESHOLD}% за 5 мин\n• Ликвидация SHORT от ${LIQUIDATION_THRESHOLD:,}\n\n🎯 Шорт 0.6-2.5%",
                                'parse_mode': 'HTML'
                            }
                            requests.post(url_send, json=payload, timeout=REQUEST_TIMEOUT)

                    elif text == '/stop':
                        if chat_id in users:
                            del users[chat_id]
                            log(f"Пользователь {chat_id} удален", "WARNING")

                    elif text == '/stats':
                        msg = (
                            f"📊 <b>СТАТИСТИКА БОТА</b>\n\n"
                            f"• Ликвидаций всего: {stats['total_liquidations']}\n"
                            f"• SHORT ликвидаций: {stats['short_liquidations']}\n"
                            f"• Выше ${LIQUIDATION_THRESHOLD}: {stats['short_above_threshold']}\n"
                            f"• Проверок пампа: {stats['pump_checks']}\n"
                            f"• Пампов найдено: {stats['pump_found']}\n"
                            f"• Проверок Long/Short: {stats['ls_checks']}\n"
                            f"• Long/Short пройдено: {stats['ls_passed']}\n"
                            f"• Сигналов отправлено: {stats['signals_sent']}\n"
                        )
                        url_send = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'}
                        requests.post(url_send, json=payload, timeout=REQUEST_TIMEOUT)

                    elif text == '/help':
                        url_send = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': "🤖 <b>Команды:</b>\n/start - подписаться\n/stop - отписаться\n/stats - статистика\n/help - справка",
                            'parse_mode': 'HTML'
                        }
                        requests.post(url_send, json=payload, timeout=REQUEST_TIMEOUT)

            time.sleep(1)
        except Exception as e:
            log(f"Ошибка Telegram: {e}", "ERROR")
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
    print("СКРИНЕР «ПАМП + ЛИКВИДАЦИИ + ДИСБАЛАНС»")
    print(f"Порог пампа: {PUMP_THRESHOLD}% за 5 минут")
    print(f"Порог ликвидации: ${LIQUIDATION_THRESHOLD:,}")
    print(f"Long/Short фильтр: {'ВКЛЮЧЕН' if USE_LONG_SHORT_RATIO else 'ВЫКЛЮЧЕН'}")
    print("=" * 60)

    atexit.register(send_shutdown_message)

    # Запускаем обработчик Telegram
    log("Запуск обработчика Telegram...")
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    # Запускаем heartbeat для статистики
    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()

    time.sleep(2)

    # Запускаем WebSocket
    connect_websocket()


if __name__ == "__main__":
    main()
