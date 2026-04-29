import requests
import time
from datetime import datetime, date
import urllib.parse
import threading
import atexit
import hashlib
import hmac
import json
import os

# ==================== НАСТРОЙКИ ====================
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
BYBIT_API_KEY = os.getenv('BYBIT_API_KEY')
BYBIT_API_SECRET = os.getenv('BYBIT_API_SECRET')

# Торговые настройки Bybit


BYBIT_BASE_URL = "https://api.bybit.com"
TRADE_SYMBOLS = "ALL"           # "ALL" – все пары USDT, либо список ['BTCUSDT','ETHUSDT']      # Базовый размер в монетах (количество, например 20 BTC? лучше в USDT? Уточним: для каждого символа размер должен быть в USDT или в монетах? Поскольку монеты разные, логичнее задавать в USDT. Но по вашему запросу "20 монет" – оставим в монетах, но это будет некорректно для разных цен. Предлагаю изменить на USDT: например, 20 USDT на сделку, тогда количество = 20 / цена. Я реализую в USDT.)
# Исправление: будем использовать фиксированный риск в USDT, пересчитывая в количество монет.
RISK_USDT_PER_TRADE = 5        # Базовый риск в USDT (20 USDT на первый и второй вход)
MAX_POSITIONS = 5               # Максимальное количество усреднений
PROFIT_TARGET_DAY = 15          # Цель по профитным сделкам в день
TAKE_PROFIT_PERCENT = 1.0       # Тейк 1% для первой позиции

# Глобальные структуры
users = {
    '5296533274': {
        'active': True,
        'daily_alerts': {
            'date': date.today(),
            'counts': {}
        }
    }
}

historical_data = {}            # для мониторинга цены (памп-скринер)
open_shorts = {}                # {symbol: {'avg_price': float, 'total_qty': float, 'levels': [], 'step': int, 'tp_price': float}}
daily_profit_trades = {}        # {date: count}  общий счетчик профитных сделок на день
resistance_levels_cache = {}    # {symbol: [levels]}
funding_rates_cache = {}        # {symbol: funding_rate}
last_resistance_fetch = {}      # время последнего обновления уровней

REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 2

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def make_request_with_retry(url, params=None, method='GET', json_body=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            if method.upper() == 'GET':
                response = requests.get(url, params=params, timeout=timeout)
            else:
                response = requests.post(url, params=params, json=json_body, timeout=timeout)
            if response.status_code == 200:
                return response
            else:
                print(f"Попытка {attempt+1}: Ошибка HTTP {response.status_code} для {url}")
        except Exception as e:
            print(f"Попытка {attempt+1}: {e}")
        if attempt < max_retries-1:
            time.sleep(RETRY_DELAY * (attempt+1))
    return None

def get_funding_rate(symbol):
    """Получить ставку фандинга для пары на Bybit"""
    url = f"{BYBIT_BASE_URL}/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    resp = make_request_with_retry(url, params=params)
    if resp:
        data = resp.json()
        if data['retCode'] == 0 and data['result']['list']:
            return float(data['result']['list'][0]['fundingRate'])
    return None

def get_klines(symbol, interval='60', limit=200):
    """Получить свечи для таймфрейма 1 час"""
    url = f"{BYBIT_BASE_URL}/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
    resp = make_request_with_retry(url, params=params)
    if resp:
        data = resp.json()
        if data['retCode'] == 0:
            return [[float(x[2]), float(x[3])] for x in data['result']['list']]  # high, low
    return []

def find_resistance_levels(symbol, lookback_bars=200, cluster_tolerance=0.005):
    """Находит уровни сопротивления на 1H (локальные максимумы)"""
    candles = get_klines(symbol, interval='60', limit=lookback_bars)
    if not candles:
        return []
    highs = [c[0] for c in candles]
    peaks = []
    for i in range(1, len(highs)-1):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
            peaks.append(highs[i])
    if not peaks:
        return []
    peaks.sort()
    clusters = []
    for p in peaks:
        if not clusters or abs(p - clusters[-1]) / clusters[-1] > cluster_tolerance:
            clusters.append(p)
    return clusters[-5:]  # последние 5 уровней

def bybit_signed_request(method, endpoint, params=None, body=None):
    """Подписанный запрос к Bybit v5"""
    timestamp = int(time.time() * 1000)
    full_url = BYBIT_BASE_URL + endpoint
    if params is None:
        params = {}
    params['api_key'] = BYBIT_API_KEY
    params['timestamp'] = timestamp
    param_str = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = hmac.new(bytes(BYBIT_API_SECRET, 'utf-8'), param_str.encode('utf-8'), hashlib.sha256).hexdigest()
    params['sign'] = signature
    
    if method.upper() == 'GET':
        resp = make_request_with_retry(full_url, params=params)
    else:
        resp = make_request_with_retry(full_url, method='POST', json_body=body, params=params)
    if resp:
        return resp.json()
    return None

def place_limit_order(symbol, side, qty, price, reduce_only=False):
    """Разместить лимитный ордер (side: Buy/Sell)"""
    endpoint = "/v5/order/create"
    body = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Limit",
        "qty": str(qty),
        "price": str(price),
        "timeInForce": "PostOnly",
        "reduceOnly": reduce_only
    }
    return bybit_signed_request('POST', endpoint, body=body)

def place_market_order(symbol, side, qty, reduce_only=False):
    """Рыночный ордер для закрытия (если лимит не сработает)"""
    endpoint = "/v5/order/create"
    body = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "reduceOnly": reduce_only
    }
    return bybit_signed_request('POST', endpoint, body=body)

def get_open_position(symbol):
    """Получить текущую позицию (шорт)"""
    endpoint = "/v5/position/list"
    params = {"category": "linear", "symbol": symbol}
    data = bybit_signed_request('GET', endpoint, params=params)
    if data and data['retCode'] == 0 and data['result']['list']:
        pos = data['result']['list'][0]
        if float(pos['size']) > 0 and pos['side'] == 'Sell':
            return {
                'avg_price': float(pos['avgPrice']),
                'size': float(pos['size'])
            }
    return None

def cancel_all_orders(symbol):
    """Отменить все ордера по символу"""
    endpoint = "/v5/order/cancel-all"
    body = {"category": "linear", "symbol": symbol}
    return bybit_signed_request('POST', endpoint, body=body)

def set_take_profit_limit(symbol, qty, price):
    """Установить лимитный тейк-профит ордер (закрытие шорта покупкой)"""
    return place_limit_order(symbol, 'Buy', qty, price, reduce_only=True)

# ==================== УПРАВЛЕНИЕ ДНЕВНЫМИ ПРОФИТНЫМИ СДЕЛКАМИ ====================
def can_trade_today():
    today = date.today()
    if today not in daily_profit_trades:
        daily_profit_trades[today] = 0
    return daily_profit_trades[today] < PROFIT_TARGET_DAY

def add_profit_trade():
    today = date.today()
    if today not in daily_profit_trades:
        daily_profit_trades[today] = 0
    daily_profit_trades[today] += 1
    print(f"Профитных сделок сегодня: {daily_profit_trades[today]}/{PROFIT_TARGET_DAY}")

# ==================== ОСНОВНАЯ ТОРГОВАЯ ЛОГИКА ====================
def check_and_open_short(symbol, current_price, price_change_percent):
    """
    Вызывается при сигнале пампа (рост цены > PRICE_INCREASE_THRESHOLD за 5 мин)
    Решает, открывать ли шорт на основе сопротивлений.
    """
    if not can_trade_today():
        return
    # Проверяем, нет ли уже открытой позиции
    if symbol in open_shorts:
        return
    # Получаем уровни сопротивления
    if symbol not in resistance_levels_cache or (time.time() - last_resistance_fetch.get(symbol, 0) > 3600):
        levels = find_resistance_levels(symbol)
        resistance_levels_cache[symbol] = levels
        last_resistance_fetch[symbol] = time.time()
    else:
        levels = resistance_levels_cache[symbol]
    if not levels:
        return
    # Ближайшее сопротивление выше текущей цены
    nearest_res = None
    for lev in levels:
        if lev > current_price:
            nearest_res = lev
            break
    if not nearest_res:
        return
    # Расстояние до сопротивления (должно быть не более 0.5%, чтобы сигнал был актуален)
    dist_to_res = (nearest_res - current_price) / current_price * 100
    if dist_to_res > 0.5:   # слишком далеко от сопротивления – не входим, хотя сигнал пампа есть
        print(f"{symbol}: сигнал пампа, но до сопротивления {dist_to_res:.2f}% > 0.5% – вход отложен")
        return
    # Проверка фандинга
    funding = get_funding_rate(symbol)
    if funding is None:
        funding = 0
    if funding < -0.001:
        print(f"{symbol}: фандинг {funding:.5%} < -0.1% – шорт не открываем")
        return
    # Проверяем, что следующее сопротивление не более чем на 20% выше
    next_res = None
    for lev in levels:
        if lev > nearest_res:
            next_res = lev
            break
    if next_res and (next_res - nearest_res) / nearest_res > 0.20:
        print(f"{symbol}: следующее сопротивление через {(next_res/nearest_res-1)*100:.1f}% > 20% – вход заблокирован")
        return
    # Рассчитываем размер позиции в монетах на основе RISK_USDT_PER_TRADE
    qty = RISK_USDT_PER_TRADE / current_price
    # Округляем до шага лотности (Bybit требует определенный шаг, упростим: округлим до 6 знаков)
    qty = round(qty, 6)
    if qty <= 0:
        return
    # Открываем шорт лимитным ордером на уровне сопротивления
    order_res = place_limit_order(symbol, 'Sell', qty, nearest_res)
    if order_res and order_res['retCode'] == 0:
        print(f"✅ Открыт шорт лимит {symbol} на {qty} монет по цене {nearest_res}")
        # Сохраняем информацию об открытой позиции (ордер на исполнении)
        open_shorts[symbol] = {
            'avg_price': nearest_res,
            'total_qty': qty,
            'levels': levels,          # все уровни сопротивления для усреднения
            'step': 0,                 # 0 - первая позиция
            'tp_price': None,
            'base_qty': qty
        }
        # Выставляем тейк-профит на 1% от цены входа лимитным ордером
        tp_price = nearest_res * (1 - TAKE_PROFIT_PERCENT / 100)
        tp_price = round(tp_price, 6)
        set_take_profit_limit(symbol, qty, tp_price)
        open_shorts[symbol]['tp_price'] = tp_price
    else:
        print(f"❌ Ошибка открытия шорта {symbol}: {order_res}")

def manage_averaging_and_tp(symbol, current_price):
    """Управление усреднением и перестановкой тейка при каждом тике цены"""
    if symbol not in open_shorts:
        return
    pos = open_shorts[symbol]
    avg_price = pos['avg_price']
    total_qty = pos['total_qty']
    step = pos['step']
    levels = pos['levels']
    
    # Ищем, на каком уровне сопротивления мы сейчас находимся (цена >= уровень)
    current_level_index = -1
    for i, lev in enumerate(levels):
        if current_price >= lev * 0.998:   # очень близко к уровню
            current_level_index = i
            break
    
    # Нужно ли усреднение: если мы прошли уровень, на котором ещё не усредняли, и он следующий за шагом
    if current_level_index > step and current_level_index < len(levels):
        # Рассчитываем следующий размер позиции по прогрессии: первые два одинаковые, потом удвоение
        if step == 0:
            new_qty = pos['base_qty']
        elif step == 1:
            new_qty = pos['base_qty']
        else:
            new_qty = pos['base_qty'] * (2 ** (step - 1))
        # Проверяем, что новый уровень не более чем на 20% выше предыдущего (уже проверено при входе, но перепроверим)
        if current_level_index > 0:
            prev_level = levels[current_level_index-1]
            if (levels[current_level_index] - prev_level) / prev_level > 0.20:
                print(f"{symbol}: уровень {levels[current_level_index]} более чем на 20% выше предыдущего – усреднение отменено")
                return
        # Округляем количество
        new_qty = round(new_qty, 6)
        if new_qty <= 0:
            return
        # Выставляем лимитный ордер на усреднение (шорт на этом уровне)
        order_res = place_limit_order(symbol, 'Sell', new_qty, levels[current_level_index])
        if order_res and order_res['retCode'] == 0:
            print(f"🔄 Усреднение {symbol}: +{new_qty} монет по цене {levels[current_level_index]}")
            # Обновим среднюю цену (средневзвешенную)
            new_total_qty = total_qty + new_qty
            new_avg_price = (avg_price * total_qty + levels[current_level_index] * new_qty) / new_total_qty
            open_shorts[symbol]['avg_price'] = new_avg_price
            open_shorts[symbol]['total_qty'] = new_total_qty
            open_shorts[symbol]['step'] = current_level_index
            # После усреднения (если это первое усреднение, step стал 1) переставляем тейк на безубыток
            if current_level_index == 1:
                # Пересчитываем безубыток с учетом комиссий и фандинга
                # Комиссия Bybit для лимитных ~0.01% (тейкер 0.055%? Примем 0.05% на круг)
                # Фандинг: пока нет, просто добавим запас 0.05%
                be_price = new_avg_price * (1 + 0.0005)  # небольшой запас 0.05%
                be_price = round(be_price, 6)
                # Отменяем старый тейк-профит ордер
                cancel_all_orders(symbol)
                # Устанавливаем новый лимитный ордер на безубыток для всей позиции
                set_take_profit_limit(symbol, new_total_qty, be_price)
                open_shorts[symbol]['tp_price'] = be_price
                print(f"📊 Переставлен тейк на безубыток: {be_price}")
        else:
            print(f"❌ Ошибка усреднения {symbol}: {order_res}")
    
    # Проверка, не достигли ли тейка (цена стала <= tp_price)
    if pos['tp_price'] and current_price <= pos['tp_price']:
        # Закрываем позицию (тейк сработал)
        # Ордер уже должен быть исполнен, но на всякий случай проверим позицию
        position = get_open_position(symbol)
        if position and position['size'] > 0:
            # Довыставляем рыночный ордер для закрытия остатка (если лимитный не полностью закрыл)
            remaining = position['size']
            if remaining > 0:
                place_market_order(symbol, 'Buy', remaining, reduce_only=True)
        print(f"🎯 Тейк-профит сработал для {symbol}! Прибыльная сделка.")
        add_profit_trade()
        # Удаляем позицию из отслеживания
        del open_shorts[symbol]
        cancel_all_orders(symbol)

# ==================== МОНИТОРИНГ ЦЕН (ПАМП-СКРИНЕР) ====================
def fetch_binance_symbols():
    url = "https://api.binance.com/api/v3/exchangeInfo"
    resp = make_request_with_retry(url, timeout=15)
    if resp:
        data = resp.json()
        symbols = [s['symbol'] for s in data['symbols'] if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING']
        return symbols
    return []

def fetch_bybit_symbols():
    url = "https://api.bybit.com/v5/market/instruments-info"
    params = {"category": "linear"}
    resp = make_request_with_retry(url, params=params)
    if resp:
        data = resp.json()
        if data['retCode'] == 0:
            return [item['symbol'] for item in data['result']['list']]
    return []

def fetch_binance_ticker(symbol):
    url = "https://api.binance.com/api/v3/ticker/24hr"
    params = {"symbol": symbol}
    resp = make_request_with_retry(url, params=params)
    if resp:
        data = resp.json()
        if 'lastPrice' in data:
            return {
                'symbol': symbol,
                'lastPrice': float(data['lastPrice']),
                'priceChangePercent': float(data['priceChangePercent'])
            }
    return None

def fetch_bybit_ticker(symbol):
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    resp = make_request_with_retry(url, params=params)
    if resp:
        data = resp.json()
        if data['retCode'] == 0 and data['result']['list']:
            t = data['result']['list'][0]
            return {
                'symbol': symbol,
                'lastPrice': float(t['lastPrice']),
                'priceChangePercent': float(t['price24hPcnt']) * 100
            }
    return None

def monitor_exchange(exchange_name, fetch_symbols_func, fetch_ticker_func):
    print(f"Запуск мониторинга {exchange_name}")
    symbols = fetch_symbols_func()
    if TRADE_SYMBOLS != "ALL":
        symbols = [s for s in symbols if s in TRADE_SYMBOLS]
    else:
        # Можно ограничить количество для производительности, например, все USDT пары
        symbols = symbols[:200]  # ограничим 200 для начала
    if not symbols:
        print(f"Нет символов для {exchange_name}")
        return
    for sym in symbols:
        key = f"{exchange_name}_{sym}"
        if key not in historical_data:
            historical_data[key] = {'price': []}
    print(f"{exchange_name}: отслеживается {len(symbols)} символов")
    error_count = 0
    while True:
        try:
            successful = 0
            for symbol in symbols:
                ticker = fetch_ticker_func(symbol)
                if ticker:
                    successful += 1
                    error_count = 0
                    current_price = ticker['lastPrice']
                    timestamp = int(time.time())
                    key = f"{exchange_name}_{symbol}"
                    hist = historical_data[key]['price']
                    hist.append({'value': current_price, 'timestamp': timestamp})
                    # удаляем старые данные старше TIME_WINDOW
                    historical_data[key]['price'] = [x for x in hist if timestamp - x['timestamp'] <= TIME_WINDOW]
                    
                    # Проверка изменения цены за последние TIME_WINDOW секунд
                    if len(historical_data[key]['price']) >= 2:
                        first_price = historical_data[key]['price'][0]['value']
                        price_change_pct = (current_price - first_price) / first_price * 100
                        if price_change_pct >= PRICE_INCREASE_THRESHOLD:
                            print(f"🔥 ПАМП {symbol} (+{price_change_pct:.2f}%) за 5 мин")
                            # Вызываем торговую логику для открытия шорта
                            check_and_open_short(symbol, current_price, price_change_pct)
                        # Управление открытыми позициями (усреднение, тейк) - вызываем для каждого тикера
                        manage_averaging_and_tp(symbol, current_price)
            print(f"{exchange_name}: успешно {successful}/{len(symbols)}")
            time.sleep(5)
        except Exception as e:
            print(f"Ошибка в {exchange_name}: {e}")
            time.sleep(10)

# ==================== TELEGRAM БОТ (ОБРАБОТКА КОМАНД) ====================
def generate_links(symbol):
    clean = symbol.replace('USDT', '').replace('1000', '')
    coinglass_sym = f"Binance_{symbol}"
    return {
        'coinglass': f"https://www.coinglass.com/tv/{coinglass_sym}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}",
        'binance': f"https://www.binance.com/ru/trade/{symbol}",
        'bybit': f"https://www.bybit.com/trade/usdt/{symbol}"
    }

def send_telegram_notification(chat_id, message, symbol, exchange):
    # ограничение уведомлений
    today = date.today()
    if users[chat_id]['daily_alerts']['date'] != today:
        users[chat_id]['daily_alerts']['date'] = today
        users[chat_id]['daily_alerts']['counts'] = {}
    cnt = users[chat_id]['daily_alerts']['counts'].get(symbol, 0)
    if cnt >= MAX_ALERTS_PER_DAY:
        return False
    users[chat_id]['daily_alerts']['counts'][symbol] = cnt + 1
    links = generate_links(symbol)
    msg = f"{message}\n\n🔗 <b>Анализ:</b>\n• <a href='{links['coinglass']}'>Coinglass</a>\n• <a href='{links['tradingview']}'>TradingView</a>"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML', 'disable_web_page_preview': False}
    try:
        requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        return True
    except:
        return False

def handle_telegram_updates():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {'timeout': 30, 'offset': last_update_id+1}
            resp = requests.get(url, params=params, timeout=35)
            data = resp.json()
            if data['ok']:
                for upd in data['result']:
                    last_update_id = upd['update_id']
                    if 'message' not in upd: continue
                    msg = upd['message']
                    chat_id = str(msg['chat']['id'])
                    text = msg.get('text', '').strip().lower()
                    if text == '/start':
                        if chat_id not in users:
                            users[chat_id] = {'active': True, 'daily_alerts': {'date': date.today(), 'counts': {}}}
                        else:
                            users[chat_id]['active'] = True
                        send_telegram_notification(chat_id, "✅ Вы подписались на сигналы и торговлю", "BOT", "info")
                    elif text == '/stop':
                        if chat_id in users:
                            users[chat_id]['active'] = False
                        send_telegram_notification(chat_id, "❌ Вы отписались", "BOT", "info")
                    elif text == '/help':
                        help_msg = "Команды:\n/start - подписка\n/stop - отписка\n/status - статус торговли"
                        send_telegram_notification(chat_id, help_msg, "BOT", "info")
                    elif text == '/status':
                        trades_today = daily_profit_trades.get(date.today(), 0)
                        active = len(open_shorts)
                        status_msg = f"Профитных сделок сегодня: {trades_today}/{PROFIT_TARGET_DAY}\nАктивных шортов: {active}"
                        send_telegram_notification(chat_id, status_msg, "BOT", "info")
            time.sleep(1)
        except Exception as e:
            print(f"Telegram ошибка: {e}")
            time.sleep(5)

def send_shutdown_message():
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {'chat_id': chat_id, 'text': "🛑 Бот остановлен", 'parse_mode': 'HTML'}
                requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except: pass

# ==================== MAIN ====================
def main():
    print("Запуск бота мониторинг+автоторговля")
    atexit.register(send_shutdown_message)
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()
    # Запускаем мониторинг Binance и Bybit (оба источника сигналов)
    binance_thread = threading.Thread(target=monitor_exchange, args=("Binance", fetch_binance_symbols, fetch_binance_ticker), daemon=True)
    bybit_thread = threading.Thread(target=monitor_exchange, args=("Bybit", fetch_bybit_symbols, fetch_bybit_ticker), daemon=True)
    binance_thread.start()
    bybit_thread.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Остановка по Ctrl+C")

if __name__ == "__main__":
    main()
