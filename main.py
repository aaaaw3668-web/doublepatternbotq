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

# Кеши
long_short_cache = {}
price_history_cache = {}
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
        return True
    except Exception as e:
        print(f"Ошибка отправки: {e}")
        return False


# ==================== LONG/SHORT RATIO (Binance) ====================
def fetch_binance_long_short_ratio(symbol):
    """Получает Long/Short Ratio с Binance (бесплатно)"""
    try:
        current_time = time.time()
        clean_symbol = symbol.replace('USDT', '').replace('1000', '')
        
        # Проверяем кеш
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
                return ratio, long_account, short_account
    except Exception as e:
        print(f"Ошибка Long/Short для {symbol}: {e}")
    
    return None, None, None


# ==================== ПРОВЕРКА ПАМПА ====================
def check_pump(symbol, current_price):
    """Проверяет памп за 5 минут"""
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': '5m', 'limit': 2}
        response = make_request_with_retry(url, params)
        
        if response:
            data = response.json()
            if data and len(data) >= 2:
                price_5min_ago = float(data[0][4])
                pump_pct = ((current_price - price_5min_ago) / price_5min_ago) * 100
                return pump_pct >= PUMP_THRESHOLD, pump_pct
    except Exception as e:
        print(f"Ошибка проверки пампа {symbol}: {e}")
    
    return False, 0


def fetch_current_price(symbol):
    """Получает текущую цену"""
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


# ==================== РАСЧЁТ ЦЕЛЕЙ ====================
def calculate_adaptive_targets(pump_pct, liquidation_value, ls_ratio, current_price):
    """Адаптивные TP/SL"""
    # Базовые множители
    pump_factor = min(1.5, pump_pct / 2.0)
    liq_factor = min(1.5, liquidation_value / 50000)
    ls_factor = min(1.5, 1.0 + (ls_ratio - 1.0) * 0.5) if ls_ratio else 1.0
    
    total_factor = pump_factor * liq_factor * ls_factor
    
    tp1_pct = max(MIN_TP, min(MAX_TP * 0.8, 0.8 * total_factor))
    tp2_pct = max(tp1_pct + 0.3, min(MAX_TP, 1.2 * total_factor))
    sl_pct = max(MIN_SL, min(MAX_SL, 0.5 + (pump_pct / 10)))
    
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
            liq = data['o']
            symbol = liq.get('s', 'UNKNOWN')
            side = liq.get('S')
            quantity = float(liq.get('q', 0))
            price = float(liq.get('ap', 0))
            usd_value = quantity * price
            
            # Только SHORT ликвидации (цена растёт)
            if side != 'BUY' or usd_value < LIQUIDATION_THRESHOLD:
                return
            
            # Проверяем не было ли недавнего сигнала
            current_time = time.time()
            if symbol in last_alert_time and current_time - last_alert_time[symbol] < MIN_TIME_BETWEEN_SIGNALS:
                return
            
            # Получаем текущую цену
            current_price = fetch_current_price(symbol)
            if current_price is None:
                return
            
            # Проверяем памп
            is_pump, pump_pct = check_pump(symbol, current_price)
            if not is_pump:
                return
            
            # Проверяем Long/Short Ratio
            if USE_LONG_SHORT_RATIO:
                ls_ratio, long_pct, short_pct = fetch_binance_long_short_ratio(symbol)
                if ls_ratio is None or ls_ratio < LONG_SHORT_RATIO_THRESHOLD:
                    return
            else:
                ls_ratio, long_pct, short_pct = 1.5, 60, 40
            
            # Все условия выполнены → сигнал
            last_alert_time[symbol] = current_time
            targets = calculate_adaptive_targets(pump_pct, usd_value, ls_ratio, current_price)
            
            # Сила сигнала
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
        print(f"Ошибка обработки: {e}")


def on_websocket_error(ws, error):
    print(f"Ошибка WebSocket: {error}")
    time.sleep(5)


def on_websocket_close(ws, close_status_code, close_msg):
    print(f"Соединение закрыто. Переподключение...")
    time.sleep(2)
    connect_websocket()


def on_websocket_open(ws):
    print("✅ WebSocket подключен к Binance")
    send_startup_message()


def send_startup_message():
    msg = (
        "🔍 <b>Скринер «Памп + Ликвидации + Дисбаланс» запущен!</b>\n\n"
        "📊 <b>Условия:</b>\n"
        "• Памп от 2% за 5 минут\n"
        "• Ликвидация SHORT от $50,000\n"
        "• Лонгов > шортов в 1.3x раза\n\n"
        "🎯 Шорт 0.6-2.5%"
    )
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            send_telegram_notification(chat_id, msg, "SYSTEM")


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
        print(f"Ошибка подключения: {e}")
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
                            print(f"✅ Новый пользователь: {chat_id}")

                            url_send = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            payload = {
                                'chat_id': chat_id,
                                'text': "✅ Вы подписались на сигналы!\n\n📊 Условия:\n• Памп от 2% за 5 мин\n• Ликвидация SHORT от $50k\n• Лонгов > шортов в 1.3x\n\n🎯 Шорт 0.6-2.5%",
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
                            'text': "🤖 <b>Команды:</b>\n/start - подписаться\n/stop - отписаться\n/help - справка",
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
    print("СКРИНЕР «ПАМП + ЛИКВИДАЦИИ + ДИСБАЛАНС»")
    print("WebSocket: Binance (один поток)")
    print("REST API: Binance (памп + Long/Short)")
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
