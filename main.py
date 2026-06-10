import os
import requests
import time
import threading
import re

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
PRICE_DOWN_THRESHOLD = -2.0  # Порог роста 2%
TIME_WINDOW = 300  # 5 минут

users = {}
price_history = {}  # {exchange_symbol: [{'price': price, 'time': timestamp}]}

def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'})
    except:
        pass

def format_message(symbol, change, old_price, new_price):
    """Форматирование сообщения с моноширинным шрифтом (без названия биржи)"""
    # Оборачиваем символ в моноширинный
    monospace_symbol = f"<code>{symbol}</code>"
    
    # Оборачиваем процент
    percent_text = f"<code>+{change:.2f}%</code>"
    
    # Оборачиваем цены
    old_price_text = f"<code>{old_price:.8f}</code>"
    new_price_text = f"<code>{new_price:.8f}</code>"
    
    # Формируем сообщение
    message = (
        f"🚨 РОСТ\n"
        f"{monospace_symbol}\n\n"
        f"📊 Изменение: {percent_text}\n\n"
        f"💰 Было: {old_price_text}\n"
        f"💰 Стало: {new_price_text}"
    )
    
    return message

def get_binance_symbols():
    """Получение всех USDT перпетуальных контрактов с Binance"""
    try:
        url = "https://fapi.binance.com/fapi/v1/ticker/price"
        resp = requests.get(url)
        data = resp.json()
        symbols = [item['symbol'] for item in data if item['symbol'].endswith('USDT')]
        print(f"✅ Binance: загружено {len(symbols)} монет")
        return symbols
    except Exception as e:
        print(f"Ошибка загрузки Binance: {e}")
        return []

def get_bybit_symbols():
    """Получение всех USDT перпетуальных контрактов с Bybit"""
    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        resp = requests.get(url)
        data = resp.json()
        symbols = [item['symbol'] for item in data['result']['list']]
        print(f"✅ Bybit: загружено {len(symbols)} монет")
        return symbols
    except Exception as e:
        print(f"Ошибка загрузки Bybit: {e}")
        return []

def get_binance_price(symbol):
    """Получение цены с Binance"""
    try:
        url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}"
        resp = requests.get(url)
        return float(resp.json()['price'])
    except:
        return None

def get_bybit_price(symbol):
    """Получение цены с Bybit"""
    try:
        url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
        resp = requests.get(url)
        return float(resp.json()['result']['list'][0]['lastPrice'])
    except:
        return None

def handle_telegram():
    """Обработка команд /start и /stop"""
    last_update = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            response = requests.get(url, params={'timeout': 30, 'offset': last_update + 1})
            data = response.json()
            
            if data['ok']:
                for update in data['result']:
                    last_update = update['update_id']
                    if 'message' in update:
                        chat_id = str(update['message']['chat']['id'])
                        text = update['message'].get('text', '').lower()
                        
                        if text == '/start':
                            users[chat_id] = True
                            welcome_msg = (
                                f"✅ <b>Подписка оформлена!</b>\n\n"
                                f"📊 Отслеживается {len(price_history)} пар\n"
                                f"🎯 Порог роста: <code>+{PRICE_UP_THRESHOLD}%</code> за <code>{TIME_WINDOW // 60}</code> минут\n\n"
                                f"💡 <b>Совет:</b> Все <code>числа</code> и <code>названия</code> можно скопировать одним нажатием"
                            )
                            send_message(chat_id, welcome_msg)
                            print(f"➕ Новый пользователь: {chat_id}")
                        
                        elif text == '/stop':
                            users.pop(chat_id, None)
                            send_message(chat_id, "❌ Вы отписались от уведомлений")
                            print(f"➖ Пользователь отписался: {chat_id}")
            time.sleep(1)
        except Exception as e:
            print(f"Ошибка обработки: {e}")
            time.sleep(5)

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Ошибка: TELEGRAM_BOT_TOKEN не установлен!")
        return
    
    # Получаем монеты с обеих бирж
    binance_symbols = get_binance_symbols()
    bybit_symbols = get_bybit_symbols()
    
    if not binance_symbols and not bybit_symbols:
        print("❌ Не удалось загрузить монеты")
        return
    
    # Инициализируем историю
    for symbol in binance_symbols:
        price_history[f"Binance_{symbol}"] = []
    for symbol in bybit_symbols:
        price_history[f"Bybit_{symbol}"] = []
    
    # Запускаем обработчик команд
    threading.Thread(target=handle_telegram, daemon=True).start()
    
    print("=" * 50)
    print(f"✅ Бот запущен (только рост)")
    print(f"📍 Binance: {len(binance_symbols)} монет")
    print(f"📍 Bybit: {len(bybit_symbols)} монет")
    print(f"🎯 Порог роста: +{PRICE_UP_THRESHOLD}% за {TIME_WINDOW // 60} минут")
    print("=" * 50)
    
    while True:
        try:
            # Проверяем Binance монеты (только рост)
            for symbol in binance_symbols:
                key = f"Binance_{symbol}"
                price = get_binance_price(symbol)
                if not price:
                    continue
                
                now = time.time()
                
                # Сохраняем историю
                price_history[key].append({'price': price, 'time': now})
                price_history[key] = [p for p in price_history[key] if now - p['time'] <= TIME_WINDOW]
                
                # Проверяем рост
                if len(price_history[key]) > 1 and users:
                    old_price = price_history[key][0]['price']
                    change = ((price - old_price) / old_price) * 100
                    
                     if change <= PRICE_DOWN_THRESHOLD:
                        print(f"📈 {symbol}: +{change:.2f}% | {price:.8f}")
                        msg = format_message(symbol, change, old_price, price)
                        for chat_id in users:
                            send_message(chat_id, msg)
                        time.sleep(0.5)
            
            # Проверяем Bybit монеты (только рост)
            for symbol in bybit_symbols:
                key = f"Bybit_{symbol}"
                price = get_bybit_price(symbol)
                if not price:
                    continue
                
                now = time.time()
                
                # Сохраняем историю
                price_history[key].append({'price': price, 'time': now})
                price_history[key] = [p for p in price_history[key] if now - p['time'] <= TIME_WINDOW]
                
                # Проверяем рост
                if len(price_history[key]) > 1 and users:
                    old_price = price_history[key][0]['price']
                    change = ((price - old_price) / old_price) * 100
                    
                    if change >= PRICE_UP_THRESHOLD:
                        print(f"📈 {symbol}: +{change:.2f}% | {price:.8f}")
                        msg = format_message(symbol, change, old_price, price)
                        for chat_id in users:
                            send_message(chat_id, msg)
                        time.sleep(0.5)
            
            time.sleep(10)
            
        except KeyboardInterrupt:
            print("\n❌ Бот остановлен")
            break
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
