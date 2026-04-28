import requests
import time
from datetime import datetime, date, timedelta
import urllib.parse
import threading
import atexit
import pytz

# Настройки
TELEGRAM_BOT_TOKEN = '7446722367:AAFfl-bNGvYiU6_GpNsFeRmo2ZNZMJRx47I'
OI_THRESHOLD = 20
PRICE_INCREASE_THRESHOLD = 1.5  # Порог для роста цены
PRICE_DECREASE_THRESHOLD = -10

# Порог для падения цены
TIME_WINDOW = 60 * 5

# База данных пользователей (в памяти)
users = {
    '5296533274': {  # Пример пользователя
        'active': True,
        'daily_alerts': {
            'date': None,  # Будет установлен при первом обновлении
            'counts': {}
        }
    }
}

# Устанавливаем часовой пояс Уфы (Екатеринбург UTC+5)
UFA_TZ = pytz.timezone('Asia/Yekaterinburg')

# Глобальные структуры данных
historical_data = {}


def get_ufa_date():
    """Возвращает текущую дату по времени Уфы"""
    return datetime.now(UFA_TZ).date()


def reset_daily_counters(chat_id):
    """Сбрасывает счётчики уведомлений, если наступил новый день по Уфе"""
    today_ufa = get_ufa_date()
    user_data = users[chat_id]['daily_alerts']
    
    if user_data['date'] != today_ufa:
        user_data['date'] = today_ufa
        user_data['counts'] = {}
        print(f"Счетчики уведомлений сброшены для пользователя {chat_id} (новая дата: {today_ufa})")
        return True
    return False


def get_alert_count(chat_id, symbol):
    """Возвращает количество уведомлений за сегодня по символу"""
    reset_daily_counters(chat_id)
    return users[chat_id]['daily_alerts']['counts'].get(symbol, 0)


def increment_alert_count(chat_id, symbol):
    """Увеличивает счётчик уведомлений для символа"""
    reset_daily_counters(chat_id)
    users[chat_id]['daily_alerts']['counts'][symbol] = get_alert_count(chat_id, symbol) + 1


def can_send_alert(chat_id, symbol):
    """Проверяет, можно ли отправить уведомление (без лимита, только проверка активности)"""
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
    
    # Используем моноширинный шрифт для ВСЕХ чисел и важных значений
    import re
    
    # Моноширинный символ
    monospace_symbol = f"<code>{symbol}</code>"
    
    # Оборачиваем все числа в сообщении в моноширинный шрифт
    def wrap_numbers(text):
        # Находим все числа (целые и десятичные, с минусом и без)
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
        f"📊 <b>Уведомлений за сегодня:</b> <code>{current_count}</code>"
    )

    # Заменяем обычное название символа на моноширинное
    message_with_links = message_with_links.replace(symbol, monospace_symbol)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message_with_links,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False,
        'link_preview_options': {'is_disabled': False}
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Ошибка отправки пользователю {chat_id}: {repr(e)}")
        return False


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
                return [item['symbol'] for item in data['result']['list']]
    except Exception as e:
        print(f"Ошибка получения символов: {e}")
    return []


def fetch_bybit_ticker(symbol):
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            if data['retCode'] == 0:
                return data['result']['list'][0]
    except Exception as e:
        print(f"Ошибка получения данных {symbol}: {e}")
    return None


def generate_links(symbol):
    """Генерация ссылок на аналитические ресурсы"""
    clean_symbol = symbol.replace('USDT', '').replace('1000', '')
    # Исправленная ссылка Coinglass TV (как в памп боте)
    coinglass_symbol = f"Binance_{symbol}"
    return {
        'coinglass': f"https://www.coinglass.com/tv/{coinglass_symbol}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BYBIT%3A{symbol}",
        'dextools': f"https://www.dextools.io/app/en/ether/pair-explorer/{clean_symbol}",
        'binance': f"https://www.binance.com/ru/trade/{symbol}",
        'bybit': f"https://www.bybit.com/trade/usdt/{symbol}"
    }


def add_user(chat_id):
    """Добавление нового пользователя"""
    if chat_id not in users:
        users[chat_id] = {
            'active': True,
            'daily_alerts': {
                'date': get_ufa_date(),  # Устанавливаем текущую дату по Уфе
                'counts': {}
            }
        }
        print(f"Добавлен новый пользователь: {chat_id}")

        # Отправляем приветственное сообщение
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': "✅ Вы успешно подписались на уведомления о торговых сигналах!\n\n📊 <b>Формат сообщений:</b>\n• Все <code>числа</code> и <code>проценты</code> можно скопировать одним нажатием\n• <code>Название монеты</code> также копируется\n• Счётчик уведомлений сбрасывается каждый день в <b>00:00 по Уфе</b>",
            'parse_mode': 'HTML'
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            print(f"Ошибка отправки приветствия: {e}")

        return True
    return False


def remove_user(chat_id):
    """Удаление пользователя"""
    if chat_id in users:
        del users[chat_id]
        print(f"Пользователь {chat_id} удален")
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
                print(f"Ошибка отправки сообщения пользователю {chat_id}: {e}")


def send_shutdown_message():
    """Отправка сообщения о выключении бота"""
    shutdown_msg = "🛑 <b>Бот остановлен</b>\n\nМониторинг приостановлен. Для возобновления работы перезапустите бота."
    broadcast_message(shutdown_msg)
    print("Сообщение о выключении отправлено всем пользователям")


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
                        # Отправляем сообщение о отписке
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': "❌ Вы отписались от уведомлений.",
                            'parse_mode': 'HTML'
                        }
                        try:
                            requests.post(url, json=payload)
                        except Exception as e:
                            print(f"Ошибка отправки сообщения: {e}")
                    elif text == '/stats':
                        # Отправляем статистику по уведомлениям
                        reset_daily_counters(chat_id)
                        counts = users[chat_id]['daily_alerts']['counts']
                        if counts:
                            stats_text = "📊 <b>Статистика уведомлений за сегодня:</b>\n\n"
                            for sym, count in counts.items():
                                stats_text += f"• <code>{sym}</code>: {count}\n"
                            stats_text += f"\n🕐 <i>Сбросится в 00:00 по Уфе</i>"
                        else:
                            stats_text = "📊 Сегодня уведомлений не было"
                        
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': stats_text,
                            'parse_mode': 'HTML'
                        }
                        try:
                            requests.post(url, json=payload)
                        except Exception as e:
                            print(f"Ошибка отправки статистики: {e}")
                    elif text == '/help':
                        # Отправляем справку
                        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            'chat_id': chat_id,
                            'text': "🤖 <b>Команды бота:</b>\n\n/start - подписаться на уведомления\n/stop - отписаться от уведомлений\n/stats - показать статистику уведомлений за сегодня\n/help - показать эту справку\n\n📊 <b>Особенности:</b>\n• Все <code>числа</code> и <code>проценты</code> выделены моноширинным шрифтом\n• Счётчик уведомлений сбрасывается каждый день в <b>00:00 по Уфе</b>\n• Нет ограничений на количество уведомлений",
                            'parse_mode': 'HTML'
                        }
                        try:
                            requests.post(url, json=payload)
                        except Exception as e:
                            print(f"Ошибка отправки справки: {e}")

            time.sleep(1)
        except Exception as e:
            print(f"Ошибка обработки обновлений: {e}")
            time.sleep(5)


def daily_reset_checker():
    """Фоновый поток для проверки сброса счётчиков в 00:00 по Уфе"""
    while True:
        try:
            now_ufa = datetime.now(UFA_TZ)
            # Вычисляем время до следующего полуночи
            midnight_ufa = datetime(now_ufa.year, now_ufa.month, now_ufa.day, 0, 0, 0) + timedelta(days=1)
            seconds_until_midnight = (midnight_ufa - now_ufa).total_seconds()
            
            # Ждём до полуночи
            time.sleep(seconds_until_midnight)
            
            # Сбрасываем счётчики для всех пользователей
            current_date = get_ufa_date()
            for chat_id in list(users.keys()):
                users[chat_id]['daily_alerts']['date'] = current_date
                users[chat_id]['daily_alerts']['counts'] = {}
                print(f"Автоматический сброс счётчиков для {chat_id} в {current_date}")
            
            # Отправляем уведомление о сбросе
            broadcast_message(f"🔄 <b>Счётчики уведомлений обновлены</b>\n\nНовый день по Уфе: <code>{current_date}</code>\nВсе счётчики сброшены до 0")
            
        except Exception as e:
            print(f"Ошибка в daily_reset_checker: {e}")
            time.sleep(60)


def main():
    print("Запуск мониторинга...")
    print(f"Часовой пояс: Уфа (UTC+5)")
    print(f"Текущая дата по Уфе: {get_ufa_date()}")
    
    # Инициализируем даты для существующих пользователей
    current_date = get_ufa_date()
    for chat_id in users:
        if users[chat_id]['daily_alerts']['date'] is None:
            users[chat_id]['daily_alerts']['date'] = current_date

    # Регистрируем функцию для отправки сообщения при выключении
    atexit.register(send_shutdown_message)

    # Запускаем обработчик сообщений в отдельном потоке
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()
    
    # Запускаем поток для ежедневного сброса счётчиков
    reset_thread = threading.Thread(target=daily_reset_checker, daemon=True)
    reset_thread.start()

    symbols = fetch_perpetual_symbols()
    if not symbols:
        print("Не удалось получить список символов")
        return

    for symbol in symbols:
        historical_data[symbol] = {'oi': [], 'price': []}

    # Уведомление о запуске всем пользователям
    broadcast_message(f"🔍 <b>Бот начал работу!</b>\n\nМониторинг рынка активирован с аналитическими ссылками!\n\n📊 <b>Особенности:</b>\n• Все <code>числа</code> и <code>проценты</code> можно скопировать\n• Счётчик уведомлений сбрасывается в <b>00:00 по Уфе</b>\n• <b>Нет ограничений</b> на количество уведомлений\n• Команда /stats показывает статистику за сегодня")
    print("Бот успешно запущен")

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

                # Проверка OI (только рост)
                if len(historical_data[symbol]['oi']) > 1:
                    old_oi = historical_data[symbol]['oi'][0]['value']
                    oi_change = calculate_change(old_oi, current_oi)

                    if oi_change >= OI_THRESHOLD:
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
                                alert_count = get_alert_count(chat_id, symbol)
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
                                       f"📌 <b>Было:</b> <code>{old_price:.4f}</code>\n"
                                       f"📌 <b>Стало:</b> <code>{current_price:.4f}</code>")
                                send_telegram_notification(chat_id, msg, symbol)

                    # Сигнал на падение цены
                    elif price_change <= PRICE_DECREASE_THRESHOLD:
                        for chat_id in list(users.keys()):
                            if users[chat_id]['active']:
                                msg = (f"🔻 <b>{symbol}</b>\n\n"
                                       f"📉 <b>Падение цены:</b> <code>{price_change:.2f}%</code>\n\n"
                                       f"📌 <b>Было:</b> <code>{old_price:.2f}</code>\n"
                                       f"📌 <b>Стало:</b> <code>{current_price:.2f}</code>")
                                send_telegram_notification(chat_id, msg, symbol)

            time.sleep(5)

        except KeyboardInterrupt:
            print("\nОстановка бота...")
            break
        except Exception as e:
            print(f"Критическая ошибка: {repr(e)}")
            time.sleep(10)


if __name__ == "__main__":
    main()
