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
TIME_WINDOW = 60 * 5               # 5 минут для анализа пампа

# ========== НАСТРОЙКИ ЛИКВИДАЦИЙ ==========
MIN_LIQUIDATION_VOLUME = {
    'large': 500000,    # BTC/ETH: $500k+
    'mid': 50000,       # Средние: $50k+
    'small': 5000       # Мелкие: $5k+
}

# ========== НАСТРОЙКИ АДАПТИВНЫХ ЦЕЛЕЙ ==========
MIN_TP = 0.6
MAX_TP = 2.5
MIN_SL = 0.4
MAX_SL = 1.2

# Общие настройки
MIN_PRICE = 0.01
MAX_ALERTS_PER_DAY = 10
SCAN_INTERVAL = 5
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

historical_prices = {}
liquidation_data = {}
data_lock = threading.Lock()
symbols_list = []
websocket_connections = {}


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def get_asset_category(symbol):
    symbol_clean = symbol.replace('USDT', '').replace('1000', '')
    
    large_cap = ['BTC', 'ETH']
    mid_cap = ['SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX', 'DOT', 'LINK', 'MATIC']
    
    if symbol_clean in large_cap:
        return 'large'
    elif symbol_clean in mid_cap:
        return 'mid'
    else:
        return 'small'


def get_liquidation_threshold_volume(symbol):
    category = get_asset_category(symbol)
    return MIN_LIQUIDATION_VOLUME.get(category, 5000)


def make_request_with_retry(url, params=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                return response
            else:
                print(f"HTTP {response.status_code} для {url}")
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


# ==================== РАСЧЁТ АДАПТИВНЫХ ЦЕЛЕЙ ====================
def calculate_volatility(prices):
    if len(prices) < 10:
        return 1.0
    
    changes = []
    for i in range(1, len(prices)):
        change = abs(prices[i] - prices[i-1])
        changes.append(change)
    
    avg_change = sum(changes) / len(changes)
    avg_price = sum(prices) / len(prices)
    
    if avg_price == 0:
        return 1.0
    
    volatility_pct = (avg_change / avg_price) * 100
    return max(0.3, min(3.0, volatility_pct))


def calculate_adaptive_targets(prices, pump_pct, liquidation_volume_usdt, current_price, symbol):
    """Расчёт адаптивных TP и SL на основе всех факторов"""
    volatility = calculate_volatility(prices)
    category = get_asset_category(symbol)
    
    # Фактор волатильности
    vol_factor = volatility / 1.0
    
    # Фактор пампа
    pump_factor = min(1.5, pump_pct / 2.0)
    
    # Фактор ликвидаций
    liq_threshold = get_liquidation_threshold_volume(symbol)
    liq_factor = min(1.5, 1.0 + (liquidation_volume_usdt / liq_threshold) * 0.5)
    
    # Фактор категории
    if category == 'large':
        category_factor = 0.7
    elif category == 'mid':
        category_factor = 1.0
    else:
        category_factor = 1.3
    
    # Расчёт TP1
    tp1_base = 0.8 * pump_factor * liq_factor * category_factor
    tp1_pct = max(MIN_TP, min(MAX_TP * 0.8, tp1_base))
    
    # Расчёт TP2
    tp2_base = 1.2 * pump_factor * liq_factor * category_factor
    tp2_pct = max(tp1_pct + 0.3, min(MAX_TP, tp2_base))
    
    # Расчёт стоп-лосса
    sl_base = 0.5 + volatility * 0.4
    sl_pct = max(MIN_SL, min(MAX_SL, sl_base))
    
    # Расчёт вероятности успеха
    success_prob = 50
    success_prob += min(20, pump_pct * 5)
    success_prob += min(25, (liquidation_volume_usdt / liq_threshold) * 15)
    success_prob = min(95, success_prob)
    
    # Risk/Reward
    avg_tp = (tp1_pct + tp2_pct) / 2
    risk_reward = round(avg_tp / sl_pct, 2)
    
    return {
        'tp1_pct': round(tp1_pct, 2),
        'tp2_pct': round(tp2_pct, 2),
        'sl_pct': round(sl_pct, 2),
        'tp1_price': current_price * (1 - tp1_pct / 100),
        'tp2_price': current_price * (1 - tp2_pct / 100),
        'sl_price': current_price * (1 + sl_pct / 100),
        'success_probability': success_prob,
        'risk_reward': risk_reward,
        'volatility': round(volatility, 2)
    }


# ==================== WEBSOCKET ДЛЯ ЛИКВИДАЦИЙ ====================
def on_liquidation_message(ws, message, symbol):
    try:
        data = json.loads(message)
        
        if 'data' in data:
            liquidations = data['data']
            
            with data_lock:
                if symbol not in liquidation_data:
                    liquidation_data[symbol] = []
                
                current_time = time.time()
                
                for liq in liquidations:
                    side = liq.get('side', '')
                    if 'Short' in side or side == 'Sell':
                        size = float(liq.get('size', 0))
                        price = float(liq.get('price', 0))
                        volume_usdt = size * price
                        
                        liquidation_data[symbol].append({
                            'time': current_time,
                            'volume_usdt': volume_usdt,
                            'price': price
                        })
                
                # Очищаем старые ликвидации (старше 2 минут)
                liquidation_data[symbol] = [
                    l for l in liquidation_data[symbol]
                    if current_time - l['time'] <= 120
                ]
                
    except Exception as e:
        print(f"Ошибка обработки ликвидаций для {symbol}: {e}")


def on_liquidation_error(ws, error, symbol):
    print(f"WebSocket ошибка для {symbol}: {error}")


def on_liquidation_close(ws, close_status_code, close_msg, symbol):
    print(f"WebSocket закрыт для {symbol}, переподключение...")
    time.sleep(5)
    connect_liquidation_websocket(symbol)


def on_liquidation_open(ws, symbol):
    print(f"WebSocket ликвидаций подключен для {symbol}")
    subscribe_msg = {
        "op": "subscribe",
        "args": [f"liquidation.{symbol}"]
    }
    ws.send(json.dumps(subscribe_msg))


def connect_liquidation_websocket(symbol):
    try:
        websocket_url = "wss://stream.bybit.com/v5/public/linear"
        
        ws = websocket.WebSocketApp(
            websocket_url,
            on_open=lambda ws: on_liquidation_open(ws, symbol),
            on_message=lambda ws, msg: on_liquidation_message(ws, msg, symbol),
            on_error=lambda ws, error: on_liquidation_error(ws, error, symbol),
            on_close=lambda ws, code, msg: on_liquidation_close(ws, code, msg, symbol)
        )
        
        wst = threading.Thread(target=ws.run_forever, daemon=True)
        wst.start()
        
        websocket_connections[symbol] = ws
        print(f"✅ WebSocket ликвидаций запущен для {symbol}")
        
    except Exception as e:
        print(f"❌ Ошибка WebSocket для {symbol}: {e}")


# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С БИРЖАМИ ====================
def fetch_binance_klines(symbol, interval='5m', limit=10):
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


def fetch_binance_current_price(symbol):
    try:
        url = "https://api.binance.com/api/v3/ticker/price"
        params = {"symbol": symbol}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            return float(data['price'])
    except Exception as e:
        print(f"Ошибка Binance price {symbol}: {e}")
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


# ==================== ОСНОВНАЯ ЛОГИКА ====================
def check_pump_and_liquidations(symbol, current_price):
    try:
        # 1. Получаем историю цен и проверяем памп
        prices = fetch_binance_klines(symbol, limit=6)
        if prices is None or len(prices) < 2:
            return False, None
        
        price_5min_ago = prices[-2] if len(prices) >= 2 else prices[0]
        pump_pct = ((current_price - price_5min_ago) / price_5min_ago) * 100
        
        if pump_pct < PUMP_THRESHOLD:
            return False, None
        
        # 2. Получаем объём ликвидаций шортов
        with data_lock:
            total_volume_usdt = 0
            current_time = time.time()
            
            if symbol in liquidation_data:
                for liq in liquidation_data[symbol]:
                    if current_time - liq['time'] <= 60:
                        total_volume_usdt += liq['volume_usdt']
        
        liq_threshold = get_liquidation_threshold_volume(symbol)
        
        if total_volume_usdt < liq_threshold:
            return False, None
        
        # 3. Рассчитываем адаптивные цели
        targets = calculate_adaptive_targets(prices, pump_pct, total_volume_usdt, current_price, symbol)
        
        # 4. Определяем силу сигнала
        volume_ratio = total_volume_usdt / liq_threshold
        
        if volume_ratio >= 5:
            strength_emoji = "🔴🔴🔴"
            strength_text = "ЭКСТРЕМАЛЬНО СИЛЬНЫЙ"
        elif volume_ratio >= 3:
            strength_emoji = "🔴🔴"
            strength_text = "ОЧЕНЬ СИЛЬНЫЙ"
        elif volume_ratio >= 1.5:
            strength_emoji = "🔴"
            strength_text = "СИЛЬНЫЙ"
        else:
            strength_emoji = "🟠"
            strength_text = "СРЕДНИЙ"
        
        details = {
            'pump_pct': pump_pct,
            'total_volume_usdt': total_volume_usdt,
            'liq_threshold': liq_threshold,
            'current_price': current_price,
            'strength_emoji': strength_emoji,
            'strength_text': strength_text,
            'targets': targets,
            'category': get_asset_category(symbol)
        }
        
        return True, details
        
    except Exception as e:
        print(f"Ошибка проверки {symbol}: {e}")
        return False, None


def monitor_pumps():
    global symbols_list
    
    print("🚀 Запуск мониторинга памп + ликвидации...")
    print("📊 Логика: Памп 2%+ за 5 минут + ликвидации шортов → Шорт")
    
    symbols_list = fetch_binance_symbols()
    if not symbols_list:
        print("Не удалось получить символы")
        return
    
    # Запускаем WebSocket для каждого символа
    print(f"Запуск WebSocket для {len(symbols_list)} символов...")
    for symbol in symbols_list:
        connect_liquidation_websocket(symbol)
        time.sleep(0.05)
    
    print(f"Мониторинг {len(symbols_list)} символов")
    print(f"Пороги ликвидаций: BTC/ETH: ${MIN_LIQUIDATION_VOLUME['large']:,}, "
          f"Средние: ${MIN_LIQUIDATION_VOLUME['mid']:,}, "
          f"Мелкие: ${MIN_LIQUIDATION_VOLUME['small']:,}")
    
    last_alert_time = {}
    error_count = 0
    
    while True:
        try:
            for symbol in symbols_list:
                try:
                    current_time_check = time.time()
                    if symbol in last_alert_time and current_time_check - last_alert_time[symbol] < MIN_TIME_BETWEEN_SIGNALS:
                        continue
                    
                    current_price = fetch_binance_current_price(symbol)
                    if current_price is None or current_price < MIN_PRICE:
                        continue
                    
                    is_signal, details = check_pump_and_liquidations(symbol, current_price)
                    
                    if is_signal and details:
                        last_alert_time[symbol] = current_time_check
                        t = details['targets']
                        
                        # Категория актива
                        if details['category'] == 'large':
                            cat_emoji = "🐋"
                            cat_text = "Крупная капа"
                        elif details['category'] == 'mid':
                            cat_emoji = "📊"
                            cat_text = "Средняя капа"
                        else:
                            cat_emoji = "🔥"
                            cat_text = "Мелкая капа"
                        
                        prob_bar = "█" * (t['success_probability'] // 10) + "░" * (10 - t['success_probability'] // 10)
                        
                        liq_volume_formatted = f"${details['total_volume_usdt']:,.0f}"
                        liq_threshold_formatted = f"${details['liq_threshold']:,.0f}"
                        
                        msg = (
                            f"{details['strength_emoji']} <b>ПАМП + ЛИКВИДАЦИИ ШОРТОВ</b> {details['strength_emoji']}\n\n"
                            f"Монета: <code>{symbol}</code> (Binance)\n"
                            f"{cat_emoji} Категория: {cat_text}\n"
                            f"💰 Цена: {current_price:.8f}\n\n"
                            f"📊 <b>Сигнал:</b>\n"
                            f"• Памп за 5 мин: +{details['pump_pct']:.2f}%\n"
                            f"• Ликвидации шортов: {liq_volume_formatted} (порог: {liq_threshold_formatted})\n"
                            f"• Качество: {details['strength_text']}\n\n"
                            f"📈 <b>Адаптивные цели:</b>\n"
                            f"• Волатильность: {t['volatility']}%\n"
                            f"• Вероятность успеха: {t['success_probability']}% {prob_bar}\n"
                            f"• Risk/Reward: 1:{t['risk_reward']}\n\n"
                            f"🎯 <b>Тейк-профит:</b>\n"
                            f"• TP1 ({t['tp1_pct']}%): {t['tp1_price']:.8f}\n"
                            f"• TP2 ({t['tp2_pct']}%): {t['tp2_price']:.8f}\n\n"
                            f"⛔ <b>Стоп-лосс:</b>\n"
                            f"• SL ({t['sl_pct']}%): {t['sl_price']:.8f}\n\n"
                            f"💡 <b>Логика:</b>\n"
                            f"Цена выросла на {details['pump_pct']:.1f}%, выбив шортов на {liq_volume_formatted}.\n"
                            f"Умные деньги забрали ликвидность — готовьте шорт!"
                        )
                        
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
                                send_telegram_notification(chat_id, msg, symbol, "Binance")
                    
                except Exception as e:
                    print(f"Ошибка {symbol}: {e}")
                    error_count += 1
                    continue
            
            # Логирование каждые 30 секунд
            if int(time.time()) % 30 == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Мониторинг активен...")
            
            if error_count > 100:
                print(f"Много ошибок ({error_count}), перезапуск мониторинга...")
                error_count = 0
            
            time.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            print(f"Критическая ошибка: {e}")
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
                                'text': f"✅ Вы подписались на сигналы «Памп + Ликвидации шортов»!\n\n📊 <b>Что отслеживается:</b>\n\n1️⃣ <b>Памп от {PUMP_THRESHOLD}% за 5 минут</b>\n\n2️⃣ <b>Ликвидации шортов ПО ОБЪЁМУ</b>\n• BTC/ETH: от $500,000\n• Средние: от $50,000\n• Мелкие: от $5,000\n\n🎯 <b>Адаптивные цели</b>\n• TP/SL под каждый актив\n\n📍 Биржа: Binance",
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
                            'text': "🤖 <b>Памп + Ликвидации шортов</b>\n\n/start - подписаться\n/stop - отписаться\n/help - справка\n\n📈 <b>Логика:</b>\n1. Памп от 2% за 5 минут\n2. Ликвидации шортов на значительную сумму\n3. → Шорт\n\n📍 Биржа: Binance",
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
    print("СКРИНЕР «ПАМП + ЛИКВИДАЦИИ ШОРТОВ»")
    print(f"Памп: от {PUMP_THRESHOLD}% за 5 минут")
    print("Ликвидации: по объёму в USDT")
    print("=" * 60)

    atexit.register(send_shutdown_message)

    # Запускаем обработчик Telegram
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    time.sleep(2)

    # Отправляем приветствие
    startup_msg = (
        f"🔍 <b>Скринер запущен!</b>\n\n"
        f"📊 <b>Условия для шорта:</b>\n"
        f"• Памп от {PUMP_THRESHOLD}% за 5 минут\n"
        f"• Ликвидации шортов на сумму от:\n"
        f"  - BTC/ETH: ${MIN_LIQUIDATION_VOLUME['large']:,}\n"
        f"  - Средние: ${MIN_LIQUIDATION_VOLUME['mid']:,}\n"
        f"  - Мелкие: ${MIN_LIQUIDATION_VOLUME['small']:,}\n\n"
        f"🎯 <b>Адаптивные цели:</b>\n"
        f"• TP: от {MIN_TP}% до {MAX_TP}%\n"
        f"• SL: от {MIN_SL}% до {MAX_SL}%\n\n"
        f"📍 Биржа: Binance"
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
    monitor_pumps()


if __name__ == "__main__":
    main()
