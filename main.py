import requests
import time
from datetime import datetime, timedelta, timezone
import urllib.parse
import threading
import atexit
import os
import re
from zoneinfo import ZoneInfo
from flask import Flask  # <-- НОВОЕ: импорт Flask

# НОВОЕ: Создаем Flask приложение
app = Flask(__name__)
PORT = int(os.environ.get('PORT', 10000))

# Настройки
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
if not TELEGRAM_BOT_TOKEN:
    print("✗ Ошибка: TELEGRAM_BOT_TOKEN не найден в переменных окружения!")
    exit(1)

OI_THRESHOLD = 500
PRICE_INCREASE_THRESHOLD = 2.5     # Порог для роста цены
PRICE_DECREASE_THRESHOLD = -31     # Порог для падения цены
TIME_WINDOW = 60 * 5
DAILY_ALERT_LIMIT = 5              # Лимит уведомлений на одну монету в день

# База данных пользователей (в памяти)
users = {
    '5296533274': {  # Пример пользователя
        'active': True,
        'alert_counts': {}  # Структура: { 'BTCUSDT': количество_за_день }
    }
}

# Глобальные структуры данных
historical_data = {}

# НОВОЕ: Простой эндпоинт для keep-alive
@app.route('/')
def health_check():
    return "Bot is running!", 200

@app.route('/ping')
def ping():
    return "pong", 200

def get_ye_time():
    """Возвращает текущее время по Уфимскому времени (UTC+5)"""
    # Используем timezone-aware объекты с явным указанием UTC
    return datetime.now(timezone.utc) + timedelta(hours=5)


def get_ye_time_zoneinfo():
    """Альтернативный вариант с явным указанием часового пояса Уфы"""
    # Уфа находится в часовом поясе Asia/Yekaterinburg (UTC+5)
    return datetime.now(ZoneInfo("Asia/Yekaterinburg"))


def get_alert_count(chat_id, symbol):
    """Возвращает количество уведомлений по символу за сегодня"""
    if chat_id not in users:
        return 0
    return users[chat_id]['alert_counts'].get(symbol, 0)


def increment_alert_count(chat_id, symbol):
    """Увеличивает счётчик уведомлений для символа"""
    if chat_id in users:
        users[chat_id]['alert_counts'][symbol] = get_alert_count(chat_id, symbol) + 1


def can_send_alert(chat_id, symbol):
    """Проверяет, можно ли отправить уведомление (учитывая лимит)"""
    if chat_id not in users or not users[chat_id]['active']:
        return False
    # Если лимит исчерпан — отправлять нельзя
    if get_alert_count(chat_id, symbol) >= DAILY_ALERT_LIMIT:
        return False
    return True


def send_telegram_notification(chat_id, message, symbol):
    """Отправляет уведомление, увеличивая счётчик, если лимит не превышен"""
    if not can_send_alert(chat_id, symbol):
        return False

    # Увеличиваем счётчик уведомлений
    increment_alert_count(chat_id, symbol)
    current_count = get_alert_count(chat_id, symbol)
    
    # Моноширинный символ
    monospace_symbol = f"<code>{symbol}</code>"
    
    # Оборачиваем все числа в сообщении в моноширинный шрифт
    def wrap_numbers(text):
        return re.sub(r'(-?\d+(?:\.\d+)?%)', r'<code>\1</code>', text)
    
    message = wrap_numbers(message)
    
    links = generate_links(symbol)
    message_with_links = (
        f"{message}\n\n"
        f"🔗 <b>Быстрый анализ:</b>\n"
        f"• 📊 <a href='{links['coinglass']}'>Coinglass TV</a>\n"
        f"• 📈 <a href='{links['tradingview']}'>TradingView</a>\n"
        f"• 💰 <a href='{links['binance']}'>Binance</a>\n"
        f"• ⚡ <a href='{links['bybit']}'>Bybit</a>\n\n"
        f"📊 <b>Уведомлений по {monospace_symbol} за сегодня:</b> <code>{current_count}/{DAILY_ALERT_LIMIT}</code>"
    )

    # Заменяем обычное название символа на моноширинное
    message_with_links = message_with_links.replace(symbol, monospace_symbol)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message_with_links,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print(f"✓ Уведомление отправлено для {symbol} пользователю {chat_id} ({current_count}/{DAILY_ALERT_LIMIT})")
        return True
    except Exception as e:
        print(f"✗ Ошибка отправки пользователю {chat_id}: {repr(e)}")
        return False


def check_and_reset_at_midnight():
    """Фоновое отслеживание наступления 00:00 по Уфе для сброса лимитов"""
    # Запоминаем текущую дату по Уфе при запуске
    last_reset_date = get_ye_time().date()
    
    while True:
        try:
            time.sleep(1)
            current_ye_time = get_ye_time()
            current_date = current_ye_time.date()
            
            # Если дата сменилась, значит наступила полночь 00:00 по Уфе
            if current_date > last_reset_date:
                print(f"⏰ Наступила полночь по Уфимскому времени ({current_ye_time}). Сброс лимитов...")
                
                # Очищаем суточные счетчики для всех пользователей
                for chat_id in users:
                    users[chat_id]['alert_counts'] = {}
                
                # Оповещаем пользователей в боте
                reset_message = (
                    "🔄 <b>Внимание! Наступила полночь по Уфимскому времени (00:00).</b>\n"
                    f"Суточные лимиты уведомлений (<code>{DAILY_ALERT_LIMIT}</code> на монету) успешно сброшены!"
                )
                broadcast_message(reset_message)
                
                # Обновляем контрольную дату
                last_reset_date = current_date
        except Exception as e:
            print(f"✗ Ошибка в потоке сброса лимитов: {e}")
            time.sleep(5)


def calculate_change(old, new):
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


def fetch_perpetual_symbols():
    url = "https://api.bybit.com/v5/market/instruments-info"
    params = {"category": "linear"}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            if data['retCode'] == 0:
                symbols = [item['symbol'] for item in data['result']['list']]
                print(f"✓ Загружено {len(symbols)} символов")
                return symbols
    except Exception as e:
        print(f"✗ Ошибка получения символов: {e}")
    return []


def fetch_bybit_ticker(symbol):
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            if data['retCode'] == 0 and data['result']['list']:
                return data['result']['list'][0]
    except Exception as e:
        print(f"✗ Ошибка получения данных {symbol}: {e}")
    return None


def generate_links(symbol):
    from urllib.parse import quote
    encoded_symbol = quote(symbol)
    clean_symbol = symbol.replace('USDT', '').replace('1000', '')
    
    return {
        'coinglass': f"https://www.coinglass.com/tv/Binance_{encoded_symbol}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BYBIT%3A{encoded_symbol}",
        'binance': f"https://www.binance.com/ru/trade/{encoded_symbol}",
        'bybit': f"https://www.bybit.com/trade/usdt/{encoded_symbol}"
    }


def add_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            'active': True,
            'alert_counts': {}
        }
        print(f"✓ Добавлен новый пользователь: {chat_id}")

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': f"✅ <b>Вы успешно подписались на уведомления!</b>\n\n"
                    f"📊 <b>Особенности:</b>\n"
                    f"• Все <code>числа</code> и <code>проценты</code> можно скопировать одним нажатием\n"
                    f"• Лимит на уведомления: <b>{DAILY_ALERT_LIMIT} в сутки</b> по каждой монете\n"
                    f"• Сброс лимитов происходит каждый день в <b>00:00 по Уфимскому времени</b>\n"
                    f"• Команда <code>/stats</code> - статистика за текущие сутки\n"
                    f"• Команда <code>/reset_stats</code> - сбросить текущую статистику вручную",
            'parse_mode': 'HTML'
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"✗ Ошибка отправки приветствия: {e}")
        return True
    return False


def remove_user(chat_id):
    if chat_id in users:
        del users[chat_id]
        print(f"✓ Пользователь {chat_id} удален")
        return True
    return False


def reset_user_stats(chat_id):
    if chat_id in users:
        users[chat_id]['alert_counts'] = {}
        print(f"✓ Статистика сброшена для пользователя {chat_id}")
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': "✅ <b>Суточная статистика и лимиты уведомлений сброшены!</b>",
            'parse_mode': 'HTML'
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"✗ Ошибка отправки подтверждения: {e}")
        return True
    return False


def broadcast_message(message):
    for chat_id in list(users.keys()):
        if users[chat_id]['active']:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            try:
                requests.post(url, json=payload)
            except Exception as e:
                print(f"✗ Ошибка отправки сообщения пользователю {chat_id}: {e}")


def send_shutdown_message():
    shutdown_msg = "🛑 <b>Бот остановлен</b>\n\nМониторинг приостановлен. Для возобновления работы перезапустите бота."
    broadcast_message(shutdown_msg)


def handle_telegram_updates():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {'timeout': 30, 'offset': last_update_id + 1}
            response = requests.get(url, params=params)
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
                        payload = {'chat_id': chat_id, 'text': "❌ Вы отписались от уведомлений.", 'parse_mode': 'HTML'}
                        try: requests.post(url, json=payload)
                        except: pass
                    elif text == '/stats':
                        counts = users.get(chat_id, {}).get('alert_counts', {})
                        if counts:
                            sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
                            stats_text = f"📊 <b>Статистика уведомлений за сегодня (Лимит: {DAILY_ALERT_LIMIT}):</b>\n\n"
                            for sym, count in sorted_counts[:20]:
                                stats_text += f"• <code>{sym}</code>: {count}/{DAILY_ALERT_LIMIT}\n"
                            if len(sorted_counts) > 20:
                                stats_text += f"\n<i>...и ещё {len(sorted_counts) - 20} монет</i>"
                        else:
                            stats_text = "📊 Сегодня уведомлений по монетам ещё не поступало."
                        
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {'chat_id': chat_id, 'text': stats_text, 'parse_mode': 'HTML'}
                        try: requests.post(url, json=payload)
                        except: pass
                    elif text == '/reset_stats':
                        reset_user_stats(chat_id)
                    elif text == '/help':
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': f"🤖 <b>Команды бота:</b>\n\n"
                                    f"/start - подписаться на уведомления\n"
                                    f"/stop - отписаться от уведомлений\n"
                                    f"/stats - показать статистику за день\n"
                                    f"/reset_stats - сбросить суточные лимиты\n"
                                    f"/help - справка\n\n"
                                    f"📊 <b>Лимиты:</b> Максимум <code>{DAILY_ALERT_LIMIT}</code> алертов на одну монету. Автосброс в 00:00 (Уфа).",
                            'parse_mode': 'HTML'
                        }
                        try: requests.post(url, json=payload)
                        except: pass

            time.sleep(1)
        except Exception as e:
            print(f"✗ Ошибка обработки обновлений: {e}")
            time.sleep(5)


def main():
    print("=" * 50)
    print("Запуск мониторинга...")
    print("=" * 50)
    
    atexit.register(send_shutdown_message)

    # Поток обновлений Telegram
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    # Поток автоматического сброса лимитов в полночь по Уфе
    reset_thread = threading.Thread(target=check_and_reset_at_midnight, daemon=True)
    reset_thread.start()

    symbols = fetch_perpetual_symbols()
    if not symbols:
        print("✗ Не удалось получить список символов")
        return

    for symbol in symbols:
        historical_data[symbol] = {'oi': [], 'price': []}

    broadcast_message(
        f"🔍 <b>Бот начал работу!</b>\n\n"
        f"Мониторинг рынка активирован!\n\n"
        f"📊 <b>Лимиты:</b>\n"
        f"• Ограничение: <code>{DAILY_ALERT_LIMIT}</code> уведомлений на монету в сутки.\n"
        f"• Сброс счетчиков происходит автоматически в <b>00:00 по Уфимскому времени</b>."
    )
    
    print(f"✓ Бот успешно запущен. Лимит: {DAILY_ALERT_LIMIT} алертов/монета в день.")

    while True:
        try:
            for symbol in symbols:
                ticker_data = fetch_bybit_ticker(symbol)
                if not ticker_data:
                    continue

                current_oi = float(ticker_data['openInterest'])
                current_price = float(ticker_data['lastPrice'])
                timestamp = int(datetime.now(timezone.utc).timestamp())

                # Обновляем данные OI
                historical_data[symbol]['oi'].append({'value': current_oi, 'timestamp': timestamp})
                historical_data[symbol]['oi'] = [x for x in historical_data[symbol]['oi']
                                                 if timestamp - x['timestamp'] <= TIME_WINDOW]

                if len(historical_data[symbol]['oi']) > 1:
                    old_oi = historical_data[symbol]['oi'][0]['value']
                    oi_change = calculate_change(old_oi, current_oi)

                    if oi_change >= OI_THRESHOLD:
                        for chat_id in list(users.keys()):
                            if can_send_alert(chat_id, symbol):
                                msg = (f"📈 <b>{symbol}</b>\n\n"
                                       f"📊 <b>Рост OI:</b> <code>+{oi_change:.2f}%</code>\n\n"
                                       f"📌 <b>Было:</b> <code>{old_oi:.0f}</code>\n"
                                       f"📌 <b>Стало:</b> <code>{current_oi:.0f}</code>")
                                send_telegram_notification(chat_id, msg, symbol)

                # Обновляем данные цены
                historical_data[symbol]['price'].append({'value': current_price, 'timestamp': timestamp})
                historical_data[symbol]['price'] = [x for x in historical_data[symbol]['price']
                                                     if timestamp - x['timestamp'] <= TIME_WINDOW]

                if len(historical_data[symbol]['price']) > 1:
                    old_price = historical_data[symbol]['price'][0]['value']
                    price_change = calculate_change(old_price, current_price)

                    if price_change >= PRICE_INCREASE_THRESHOLD:
                        for chat_id in list(users.keys()):
                            if can_send_alert(chat_id, symbol):
                                msg = (f"🚨 <b>{symbol}</b>\n\n"
                                       f"📈 <b>Рост цены:</b> <code>+{price_change:.2f}%</code>\n\n"
                                       f"📌 <b>Было:</b> <code>{old_price:.8f}</code>\n"
                                       f"📌 <b>Стало:</b> <code>{current_price:.8f}</code>")
                                send_telegram_notification(chat_id, msg, symbol)

                    elif price_change <= PRICE_DECREASE_THRESHOLD:
                        for chat_id in list(users.keys()):
                            if can_send_alert(chat_id, symbol):
                                msg = (f"🔻 <b>{symbol}</b>\n\n"
                                       f"📉 <b>Падение цены:</b> <code>{price_change:.2f}%</code>\n\n"
                                       f"📌 <b>Было:</b> <code>{old_price:.8f}</code>\n"
                                       f"📌 <b>Стало:</b> <code>{current_price:.8f}</code>")
                                send_telegram_notification(chat_id, msg, symbol)

            time.sleep(5)

        except KeyboardInterrupt:
            print("\n🛑 Остановка бота...")
            break
        except Exception as e:
            print(f"✗ Критическая ошибка: {repr(e)}")
            time.sleep(10)


# НОВОЕ: Измененная точка входа
if __name__ == "__main__":
    # Запускаем основную логику бота в отдельном потоке
    bot_thread = threading.Thread(target=main, daemon=True)
    bot_thread.start()
    
    # Запускаем Flask сервер (основной поток)
    # Flask будет слушать порт и отвечать на ping-запросы Render
    print(f"🚀 Запуск веб-сервера на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
