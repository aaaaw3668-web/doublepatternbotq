import requests
import time
from datetime import datetime, timedelta
import urllib.parse
import threading
import atexit

# Настройки
TELEGRAM_BOT_TOKEN = '7446722367:AAFfl-bNGvYiU6_GpNsFeRmo2ZNZMJRx47I'
OI_THRESHOLD = 2
PRICE_INCREASE_THRESHOLD = 5  # Порог для роста цены
PRICE_DECREASE_THRESHOLD = -10  # Порог для падения цены
TIME_WINDOW = 60 * 5

# База данных пользователей (в памяти)
users = {
    '5296533274': {  # Пример пользователя
        'active': True,
        'alert_counts': {}  # Простой счетчик без сброса
    }
}

# Глобальные структуры данных
historical_data = {}


def get_alert_count(chat_id, symbol):
    """Возвращает количество уведомлений по символу за всё время"""
    return users[chat_id]['alert_counts'].get(symbol, 0)


def increment_alert_count(chat_id, symbol):
    """Увеличивает счётчик уведомлений для символа"""
    users[chat_id]['alert_counts'][symbol] = get_alert_count(chat_id, symbol) + 1


def can_send_alert(chat_id, symbol):
    """Проверяет, можно ли отправить уведомление"""
    if chat_id not in users or not users[chat_id]['active']:
        return False
    return True


def send_telegram_notification(chat_id, message, symbol):
    """Отправляет уведомление, увеличивая счётчик"""
    if not can_send_alert(chat_id, symbol):
        print(f"Пользователь {chat_id} не активен")
        return False

    # Увеличиваем счётчик уведомлений
    increment_alert_count(chat_id, symbol)
    current_count = get_alert_count(chat_id, symbol)
    
    import re
    
    # Моноширинный символ
    monospace_symbol = f"<code>{symbol}</code>"
    
    # Оборачиваем все числа в сообщении в моноширинный шрифт
    def wrap_numbers(text):
        return re.sub(r'(-?\d+(?:\.\d+)?%)', r'<code>\1</code>', text)
        return re.sub(r'(-?\d+(?:\.\d+)?)', r'<code>\1</code>', text)
    
    message = wrap_numbers(message)
    
    links = generate_links(symbol)
    message_with_links = (
        f"{message}\n\n"
        f"🔗 <b>Быстрый анализ:</b>\n"
        f"• 📊 <a href='{links['coinglass']}'>Coinglass TV</a>\n"
        f"• 📈 <a href='{links['tradingview']}'>TradingView</a>\n"
        f"• 💰 <a href='{links['binance']}'>Binance</a>\n"
        f"• ⚡ <a href='{links['bybit']}'>Bybit</a>\n\n"
        f"📊 <b>Всего уведомлений по {monospace_symbol}:</b> <code>{current_count}</code>"
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
        print(f"✓ Уведомление отправлено для {symbol} пользователю {chat_id} (всего: {current_count})")
        return True
    except Exception as e:
        print(f"✗ Ошибка отправки пользователю {chat_id}: {repr(e)}")
        return False


def calculate_change(old, new):
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


def fetch_perpetual_symbols():
    """Получение списка всех перпетуальных контрактов"""
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
    """Получение данных по тикеру"""
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
    """Генерация ссылок на аналитические ресурсы с URL-кодированием"""
    from urllib.parse import quote
    
    # Кодируем символы для URL
    encoded_symbol = quote(symbol)
    clean_symbol = symbol.replace('USDT', '').replace('1000', '')
    encoded_clean_symbol = quote(clean_symbol)
    
    return {
        'coinglass': f"https://www.coinglass.com/tv/Binance_{encoded_symbol}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BYBIT%3A{encoded_symbol}",
        'binance': f"https://www.binance.com/ru/trade/{encoded_symbol}",
        'bybit': f"https://www.bybit.com/trade/usdt/{encoded_symbol}"
    }


def add_user(chat_id):
    """Добавление нового пользователя"""
    if chat_id not in users:
        users[chat_id] = {
            'active': True,
            'alert_counts': {}
        }
        print(f"✓ Добавлен новый пользователь: {chat_id}")

        # Отправляем приветственное сообщение
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': "✅ <b>Вы успешно подписались на уведомления!</b>\n\n"
                   "📊 <b>Особенности:</b>\n"
                   "• Все <code>числа</code> и <code>проценты</code> можно скопировать одним нажатием\n"
                   "• <code>Название монеты</code> также копируется\n"
                   "• Счётчик показывает <b>общее количество уведомлений</b> по каждой монете\n"
                   "• <b>Нет ограничений</b> на количество уведомлений в день\n"
                   "• Команда <code>/stats</code> - статистика по всем монетам\n"
                   "• Команда <code>/reset_stats</code> - сбросить статистику",
            'parse_mode': 'HTML'
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"✗ Ошибка отправки приветствия: {e}")
        return True
    return False


def remove_user(chat_id):
    """Удаление пользователя"""
    if chat_id in users:
        del users[chat_id]
        print(f"✓ Пользователь {chat_id} удален")
        return True
    return False


def reset_user_stats(chat_id):
    """Сброс статистики уведомлений для пользователя"""
    if chat_id in users:
        users[chat_id]['alert_counts'] = {}
        print(f"✓ Статистика сброшена для пользователя {chat_id}")
        
        # Отправляем подтверждение
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': "✅ <b>Статистика уведомлений сброшена!</b>\n\nВсе счётчики обнулены.",
            'parse_mode': 'HTML'
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"✗ Ошибка отправки подтверждения: {e}")
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
                requests.post(url, json=payload)
            except Exception as e:
                print(f"✗ Ошибка отправки сообщения пользователю {chat_id}: {e}")


def send_shutdown_message():
    """Отправка сообщения о выключении бота"""
    shutdown_msg = "🛑 <b>Бот остановлен</b>\n\nМониторинг приостановлен. Для возобновления работы перезапустите бота."
    broadcast_message(shutdown_msg)
    print("✓ Сообщение о выключении отправлено всем пользователям")


def handle_telegram_updates():
    """Обработка входящих сообщений от пользователей"""
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

                    # Обработка команд
                    if text == '/start':
                        add_user(chat_id)
                    elif text == '/stop':
                        remove_user(chat_id)
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': "❌ Вы отписались от уведомлений.",
                            'parse_mode': 'HTML'
                        }
                        try:
                            requests.post(url, json=payload)
                        except Exception as e:
                            print(f"✗ Ошибка отправки сообщения: {e}")
                    elif text == '/stats':
                        # Отправляем статистику по уведомлениям
                        counts = users[chat_id]['alert_counts']
                        if counts:
                            # Сортируем по количеству уведомлений
                            sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
                            stats_text = "📊 <b>Статистика уведомлений (всего):</b>\n\n"
                            for sym, count in sorted_counts[:20]:  # Показываем топ-20
                                stats_text += f"• <code>{sym}</code>: {count}\n"
                            if len(sorted_counts) > 20:
                                stats_text += f"\n<i>...и ещё {len(sorted_counts) - 20} монет</i>"
                        else:
                            stats_text = "📊 Пока не было ни одного уведомления"
                        
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': stats_text,
                            'parse_mode': 'HTML'
                        }
                        try:
                            requests.post(url, json=payload)
                        except Exception as e:
                            print(f"✗ Ошибка отправки статистики: {e}")
                    elif text == '/reset_stats':
                        reset_user_stats(chat_id)
                    elif text == '/help':
                        # Отправляем справку
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': "🤖 <b>Команды бота:</b>\n\n"
                                   "/start - подписаться на уведомления\n"
                                   "/stop - отписаться от уведомлений\n"
                                   "/stats - показать статистику уведомлений\n"
                                   "/reset_stats - сбросить статистику\n"
                                   "/help - показать эту справку\n\n"
                                   "📊 <b>Особенности:</b>\n"
                                   "• Все <code>числа</code> и <code>проценты</code> выделены моноширинным шрифтом\n"
                                   "• <b>Нет ограничений</b> на количество уведомлений\n"
                                   "• Счётчик показывает общее количество уведомлений за всё время",
                            'parse_mode': 'HTML'
                        }
                        try:
                            requests.post(url, json=payload)
                        except Exception as e:
                            print(f"✗ Ошибка отправки справки: {e}")

            time.sleep(1)
        except Exception as e:
            print(f"✗ Ошибка обработки обновлений: {e}")
            time.sleep(5)


def main():
    print("=" * 50)
    print("Запуск мониторинга...")
    print("=" * 50)
    
    # Регистрируем функцию для отправки сообщения при выключении
    atexit.register(send_shutdown_message)

    # Запускаем обработчик сообщений в отдельном потоке
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()

    # Получаем список символов
    symbols = fetch_perpetual_symbols()
    if not symbols:
        print("✗ Не удалось получить список символов")
        return

    # Инициализируем исторические данные
    for symbol in symbols:
        historical_data[symbol] = {'oi': [], 'price': []}

    # Уведомление о запуске всем пользователям
    broadcast_message(
        "🔍 <b>Бот начал работу!</b>\n\n"
        "Мониторинг рынка активирован!\n\n"
        "📊 <b>Особенности:</b>\n"
        "• Все <code>числа</code> и <code>проценты</code> можно скопировать\n"
        "• <b>Нет ограничений</b> на количество уведомлений\n"
        "• Команда <code>/stats</code> - статистика\n"
        "• Команда <code>/reset_stats</code> - сбросить статистику"
    )
    
    print(f"✓ Бот успешно запущен")
    print(f"✓ Отслеживается {len(symbols)} символов")
    print(f"✓ Порог OI: {OI_THRESHOLD}%")
    print(f"✓ Порог роста цены: {PRICE_INCREASE_THRESHOLD}%")
    print(f"✓ Порог падения цены: {PRICE_DECREASE_THRESHOLD}%")
    print(f"✓ Временное окно: {TIME_WINDOW // 60} минут")
    print("=" * 50)

    while True:
        try:
            for symbol in symbols:
                ticker_data = fetch_bybit_ticker(symbol)
                if not ticker_data:
                    continue

                current_oi = float(ticker_data['openInterest'])
                current_price = float(ticker_data['lastPrice'])
                timestamp = int(datetime.now().timestamp())

                # Обновляем данные OI
                historical_data[symbol]['oi'].append({'value': current_oi, 'timestamp': timestamp})
                historical_data[symbol]['oi'] = [x for x in historical_data[symbol]['oi']
                                                 if timestamp - x['timestamp'] <= TIME_WINDOW]

                # Проверка роста OI
                if len(historical_data[symbol]['oi']) > 1:
                    old_oi = historical_data[symbol]['oi'][0]['value']
                    oi_change = calculate_change(old_oi, current_oi)

                    if oi_change >= OI_THRESHOLD:
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
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

                    # Сигнал на рост цены
                    if price_change >= PRICE_INCREASE_THRESHOLD:
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
                                msg = (f"🚨 <b>{symbol}</b>\n\n"
                                       f"📈 <b>Рост цены:</b> <code>+{price_change:.2f}%</code>\n\n"
                                       f"📌 <b>Было:</b> <code>{old_price:.8f}</code>\n"
                                       f"📌 <b>Стало:</b> <code>{current_price:.8f}</code>")
                                send_telegram_notification(chat_id, msg, symbol)

                    # Сигнал на падение цены
                    elif price_change <= PRICE_DECREASE_THRESHOLD:
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
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


if __name__ == "__main__":
    main()
