import requests
import time
from datetime import datetime, date
import threading
import atexit
import math

# ==================== НАСТРОЙКИ ====================
TELEGRAM_BOT_TOKEN = '8514584009:AAFmnFff-9avc9mm-B9ZpR0AQcosUIaDb9g'

# ========== НАСТРОЙКИ СКРИНЕРА ==========
PUMP_THRESHOLD = 2.0               # Памп от 2% и выше
RSI_OVERBOUGHT = 70                # RSI перекуплен от 70
TIME_WINDOW = 60 * 5               # 5 минут для анализа
RESISTANCE_LOOKBACK = 20           # 20 свечей для поиска сопротивления
RESISTANCE_TOLERANCE = 0.002       # Допуск 0.2% от сопротивления

# ========== НАСТРОЙКИ АДАПТИВНЫХ ЦЕЛЕЙ ==========
MIN_TP = 0.6                       # Минимальный тейк (%)
MAX_TP = 2.5                       # Максимальный тейк (%)
MIN_SL = 0.4                       # Минимальный стоп (%)
MAX_SL = 1.2                       # Максимальный стоп (%)

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

historical_data = {}


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


# ==================== РАСЧЁТ RSI ====================
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
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

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ==================== РАСЧЁТ ВОЛАТИЛЬНОСТИ ====================
def calculate_volatility(prices):
    """Расчёт волатильности на основе ATR (Average True Range)"""
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


def calculate_volume_profile(volumes):
    """Анализ объёмов для оценки силы движения"""
    if len(volumes) < 5:
        return "normal"
    
    avg_volume = sum(volumes[-5:-1]) / 4
    current_volume = volumes[-1]
    
    if current_volume > avg_volume * 2:
        return "extremely_high"
    elif current_volume > avg_volume * 1.5:
        return "very_high"
    elif current_volume > avg_volume * 1.2:
        return "high"
    elif current_volume < avg_volume * 0.5:
        return "low"
    else:
        return "normal"


# ==================== АДАПТИВНЫЕ ЦЕЛИ ====================
def calculate_adaptive_targets(prices, volumes, pump_pct, rsi, resistance_level, current_price):
    """
    Расчёт адаптивных TP и SL на основе:
    - Волатильности актива
    - Силы пампа
    - Уровня RSI
    - Расстояния до сопротивления
    - Объёмов
    """
    # 1. Базовая волатильность
    volatility = calculate_volatility(prices)
    
    # 2. Коррекция на силу пампа (чем сильнее памп, тем глубже коррекция)
    pump_factor = min(1.5, pump_pct / 2.0)  # Памп 4% → фактор 2.0
    
    # 3. Коррекция на RSI (чем выше RSI, тем сильнее разворот)
    if rsi >= 85:
        rsi_factor = 1.5
    elif rsi >= 80:
        rsi_factor = 1.2
    elif rsi >= 75:
        rsi_factor = 1.0
    else:
        rsi_factor = 0.8
    
    # 4. Расстояние до сопротивления (чем ближе, тем точнее цель)
    distance_to_resistance = (resistance_level - current_price) / resistance_level * 100
    if distance_to_resistance < 0.1:
        resistance_factor = 1.2  # Почти у сопротивления
    elif distance_to_resistance < 0.3:
        resistance_factor = 1.0
    else:
        resistance_factor = 0.7
    
    # 5. Анализ объёмов
    volume_profile = calculate_volume_profile(volumes)
    if volume_profile == "extremely_high":
        volume_factor = 1.3
    elif volume_profile == "very_high":
        volume_factor = 1.1
    elif volume_profile == "high":
        volume_factor = 1.0
    elif volume_profile == "low":
        volume_factor = 0.6
    else:
        volume_factor = 0.8
    
    # 6. Расчёт итогового размера коррекции (глубина падения)
    # TP1 (первая цель) — консервативная
    tp1_base = 0.9 * pump_factor * rsi_factor * resistance_factor * volume_factor
    tp1_pct = max(MIN_TP, min(MAX_TP * 0.8, tp1_base))
    
    # TP2 (вторая цель) — более агрессивная
    tp2_base = 1.3 * pump_factor * rsi_factor * resistance_factor * volume_factor
    tp2_pct = max(tp1_pct + 0.3, min(MAX_TP, tp2_base))
    
    # Стоп-лосс (выше сопротивления с учётом волатильности)
    sl_base = 0.5 + volatility * 0.5
    sl_pct = max(MIN_SL, min(MAX_SL, sl_base))
    
    # Расчёт вероятности успеха (математическое ожидание)
    success_probability = calculate_success_probability(pump_pct, rsi, volume_profile)
    
    # Математическое ожидание в % (риск 1% ради профита X%)
    risk_reward_ratio = (tp1_pct + tp2_pct) / 2 / sl_pct
    
    return {
        'tp1_pct': round(tp1_pct, 2),
        'tp2_pct': round(tp2_pct, 2),
        'sl_pct': round(sl_pct, 2),
        'tp1_price': current_price * (1 - tp1_pct / 100),
        'tp2_price': current_price * (1 - tp2_pct / 100),
        'sl_price': resistance_level * (1 + sl_pct / 100),
        'success_probability': success_probability,
        'risk_reward': round(risk_reward_ratio, 2),
        'volatility': round(volatility, 2),
        'pump_factor': round(pump_factor, 2),
        'rsi_factor': round(rsi_factor, 2)
    }


def calculate_success_probability(pump_pct, rsi, volume_profile):
    """Расчёт вероятности успеха сигнала"""
    prob = 50  # Базовая вероятность
    
    # Памп (чем больше, тем вероятнее коррекция)
    if pump_pct >= 4:
        prob += 15
    elif pump_pct >= 3:
        prob += 10
    elif pump_pct >= 2:
        prob += 5
    
    # RSI (чем выше, тем лучше)
    if rsi >= 85:
        prob += 15
    elif rsi >= 80:
        prob += 10
    elif rsi >= 75:
        prob += 5
    
    # Объёмы
    if volume_profile == "extremely_high":
        prob += 10
    elif volume_profile == "very_high":
        prob += 7
    elif volume_profile == "high":
        prob += 3
    
    return min(95, prob)


# ==================== ПОИСК СОПРОТИВЛЕНИЯ ====================
def find_resistance(prices, lookback=RESISTANCE_LOOKBACK, tolerance=RESISTANCE_TOLERANCE):
    if len(prices) < lookback:
        return None, 0
    
    period_prices = prices[-lookback:-1]
    if not period_prices:
        return None, 0
    
    resistance_level = max(period_prices)
    return resistance_level, resistance_level


def is_near_resistance(current_price, resistance_level, tolerance=RESISTANCE_TOLERANCE):
    if resistance_level is None or resistance_level == 0:
        return False
    
    distance_pct = abs(current_price - resistance_level) / resistance_level
    return distance_pct <= tolerance


# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С БИРЖАМИ ====================
def fetch_binance_klines(symbol, interval='5m', limit=30):
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': interval, 'limit': limit}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            if data and len(data) > 0:
                prices = [float(candle[4]) for candle in data]
                volumes = [float(candle[5]) for candle in data]
                return prices, volumes
    except Exception as e:
        print(f"Ошибка Binance {symbol}: {e}")
    return None, None


def fetch_bybit_klines(symbol, interval='5', limit=30):
    try:
        url = "https://api.bybit.com/v5/market/kline"
        params = {'category': 'linear', 'symbol': symbol, 'interval': interval, 'limit': limit}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            if data.get('retCode') == 0 and data.get('result', {}).get('list'):
                klines = data['result']['list']
                prices = [float(kline[3]) for kline in klines]
                volumes = [float(kline[4]) for kline in klines]
                return prices, volumes
    except Exception as e:
        print(f"Ошибка Bybit {symbol}: {e}")
    return None, None


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


def fetch_bybit_current_price(symbol):
    try:
        url = "https://api.bybit.com/v5/market/tickers"
        params = {"category": "linear", "symbol": symbol}
        response = make_request_with_retry(url, params)
        if response:
            data = response.json()
            if data.get('retCode') == 0 and data.get('result', {}).get('list'):
                return float(data['result']['list'][0]['lastPrice'])
    except Exception as e:
        print(f"Ошибка Bybit price {symbol}: {e}")
    return None


# ==================== ОСНОВНАЯ ЛОГИКА ====================
def check_signal(prices, volumes, current_price):
    if len(prices) < RESISTANCE_LOOKBACK + 2:
        return False, None
    
    # 1. Проверка пампа
    price_start = prices[0]
    price_end = prices[-1]
    
    if price_start == 0:
        return False, None
    
    pump_pct = ((price_end - price_start) / price_start) * 100
    
    if pump_pct < PUMP_THRESHOLD:
        return False, None
    
    # 2. Поиск сопротивления
    resistance_level, _ = find_resistance(prices)
    
    if resistance_level is None:
        return False, None
    
    # 3. Проверка возле сопротивления
    near_resistance = is_near_resistance(current_price, resistance_level)
    
    if not near_resistance:
        return False, None
    
    # 4. Расчёт RSI
    rsi = calculate_rsi(prices)
    
    if rsi is None or rsi < RSI_OVERBOUGHT:
        return False, None
    
    # 5. Расчёт адаптивных целей
    targets = calculate_adaptive_targets(prices, volumes, pump_pct, rsi, resistance_level, current_price)
    
    # Определяем силу сигнала
    if rsi >= 85:
        strength_emoji = "🔴🔴🔴"
        strength_text = "ЭКСТРЕМАЛЬНО СИЛЬНЫЙ"
    elif rsi >= 80:
        strength_emoji = "🔴🔴"
        strength_text = "ОЧЕНЬ СИЛЬНЫЙ"
    else:
        strength_emoji = "🔴"
        strength_text = "СИЛЬНЫЙ"
    
    details = {
        'pump_pct': pump_pct,
        'resistance': resistance_level,
        'rsi': rsi,
        'current_price': current_price,
        'strength_emoji': strength_emoji,
        'strength_text': strength_text,
        'distance_to_resistance': ((resistance_level - current_price) / resistance_level) * 100,
        'targets': targets
    }
    
    return True, details


# ==================== МОНИТОРИНГ ====================
def monitor_signal(exchange_name, fetch_symbols_func, fetch_klines_func, fetch_price_func):
    print(f"🚀 Запуск адаптивного скринера на {exchange_name}...")

    symbols = fetch_symbols_func()
    if not symbols:
        print(f"{exchange_name}: не удалось получить символы")
        time.sleep(30)
        return

    print(f"{exchange_name}: мониторинг {len(symbols)} символов")

    last_alert_time = {}
    error_count = 0
    max_errors_before_reload = 20

    while True:
        try:
            successful_scans = 0
            
            for symbol in symbols:
                try:
                    current_time_check = time.time()
                    if symbol in last_alert_time and current_time_check - last_alert_time[symbol] < MIN_TIME_BETWEEN_SIGNALS:
                        continue
                    
                    current_price = fetch_price_func(symbol)
                    if current_price is None or current_price < MIN_PRICE:
                        continue
                    
                    prices, volumes = fetch_klines_func(symbol, limit=30)
                    if prices is None or len(prices) < 15:
                        continue
                    
                    successful_scans += 1
                    error_count = 0
                    
                    is_signal, details = check_signal(prices, volumes, current_price)
                    
                    if is_signal and details:
                        last_alert_time[symbol] = current_time_check
                        t = details['targets']
                        
                        # Создаём визуализацию вероятности
                        prob_bar = "█" * (t['success_probability'] // 10) + "░" * (10 - t['success_probability'] // 10)
                        
                        msg = (
                            f"{details['strength_emoji']} <b>ПАМП У СОПРОТИВЛЕНИЯ</b> {details['strength_emoji']}\n\n"
                            f"Монета: <code>{symbol}</code> ({exchange_name})\n"
                            f"💰 Цена: {current_price:.8f}\n\n"
                            f"📊 <b>Сигнал:</b>\n"
                            f"• Памп за 5 мин: +{details['pump_pct']:.2f}%\n"
                            f"• RSI: {details['rsi']:.1f} (перекуплен)\n"
                            f"• Сопротивление: {details['resistance']:.8f}\n"
                            f"• До сопротивления: {details['distance_to_resistance']:.2f}%\n"
                            f"• Качество: {details['strength_text']}\n\n"
                            f"📈 <b>Адаптивные цели (на основе волатильности):</b>\n"
                            f"• Волатильность: {t['volatility']}%\n"
                            f"• Вероятность успеха: {t['success_probability']}% {prob_bar}\n"
                            f"• Risk/Reward: 1:{t['risk_reward']}\n\n"
                            f"🎯 <b>Тейк-профит:</b>\n"
                            f"• TP1 ({t['tp1_pct']}%): {t['tp1_price']:.8f}\n"
                            f"• TP2 ({t['tp2_pct']}%): {t['tp2_price']:.8f}\n\n"
                            f"⛔ <b>Стоп-лосс:</b>\n"
                            f"• SL ({t['sl_pct']}%): {t['sl_price']:.8f} (выше сопротивления)\n\n"
                            f"💡 <b>Математическое ожидание:</b>\n"
                            f"Риск {t['sl_pct']}% ради профита {t['tp1_pct']}-{t['tp2_pct']}%\n"
                            f"Ожидаемая доходность: +{t['risk_reward'] * 0.7:.2f}R"
                        )
                        
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
                                send_telegram_notification(chat_id, msg, symbol, exchange_name)
                    
                except Exception as e:
                    print(f"{exchange_name} ошибка {symbol}: {e}")
                    error_count += 1
                    continue
            
            if successful_scans > 0:
                scan_rate = (successful_scans / len(symbols)) * 100 if symbols else 0
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {exchange_name}: {successful_scans}/{len(symbols)} ({scan_rate:.1f}%)")
            
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
                                'text': f"✅ Вы подписались на АДАПТИВНЫЙ скринер!\n\n📊 <b>Что нового:</b>\n\n🎯 <b>Адаптивные цели</b>\n• TP/SL рассчитываются под каждый актив\n• Учитывают волатильность, силу пампа, RSI\n• Разные цели для разных монет\n\n📈 <b>Математическое ожидание</b>\n• Вероятность успеха сигнала\n• Risk/Reward ratio\n• Ожидаемая доходность\n\n📍 Биржи: Binance + Bybit\n⏱️ Таймфрейм: 5 минут",
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
                            'text': "🤖 <b>Адаптивный скринер</b>\n\n/start - подписаться\n/stop - отписаться\n/help - справка\n\n📈 <b>Как рассчитываются цели:</b>\n• Волатильность актива\n• Сила пампа (чем сильнее, тем глубже коррекция)\n• Уровень RSI\n• Анализ объёмов\n\n🎯 Тейк: от 0.6% до 2.5%\n⛔ Стоп: от 0.4% до 1.2%",
                            'parse_mode': 'HTML'
                        }
                        requests.post(url_send, json=payload, timeout=REQUEST_TIMEOUT)

            time.sleep(1)
        except Exception as e:
            print(f"Ошибка Telegram: {e}")
            time.sleep(5)


def send_shutdown_message():
    shutdown_msg = "🛑 <b>Адаптивный скринер остановлен</b>"
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
    print("АДАПТИВНЫЙ СКРИНЕР «ПАМП У СОПРОТИВЛЕНИЯ»")
    print("TP/SL рассчитываются под каждый актив")
    print("Биржи: Binance + Bybit")
    print("=" * 60)

    atexit.register(send_shutdown_message)

    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    time.sleep(2)

    startup_msg = (
        f"🔍 <b>Адаптивный скринер запущен!</b>\n\n"
        f"📊 <b>Условия для шорта:</b>\n"
        f"• Памп от {PUMP_THRESHOLD}% за 5 минут\n"
        f"• Цена возле сопротивления\n"
        f"• RSI выше {RSI_OVERBOUGHT}\n\n"
        f"🎯 <b>Адаптивные цели:</b>\n"
        f"• TP рассчитывается под волатильность\n"
        f"• Учитывается сила пампа и RSI\n"
        f"• Разные цели для разных активов\n\n"
        f"📍 Биржи: Binance + Bybit"
    )
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {'chat_id': chat_id, 'text': startup_msg, 'parse_mode': 'HTML'}
                requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except Exception:
                pass

    binance_thread = threading.Thread(
        target=monitor_signal,
        args=("Binance", fetch_binance_symbols, fetch_binance_klines, fetch_binance_current_price),
        daemon=True
    )

    bybit_thread = threading.Thread(
        target=monitor_signal,
        args=("Bybit", fetch_bybit_symbols, fetch_bybit_klines, fetch_bybit_current_price),
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
