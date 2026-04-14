from websocket import WebSocketApp
import json
import time
import requests
from datetime import datetime

# Настройки
TELEGRAM_BOT_TOKEN = '7572230525:AAFzAQsMe4DlTYAA8G5UgGnYH598ZxgZOjs'
TELEGRAM_CHAT_ID = '5296533274'
LIQUIDATION_THRESHOLD = 1000  # Минимальная сумма ликвидации в USD
TEST_MODE = False

# ТОП-100 МОНЕТ ПО КАПИТАЛИЗАЦИИ
TOP_100_COINS = {
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT', 'TRXUSDT', 'AVAXUSDT', 'SHIBUSDT',
    'DOTUSDT', 'LINKUSDT', 'BCHUSDT', 'LTCUSDT', 'MATICUSDT', 'UNIUSDT', 'ATOMUSDT', 'XLMUSDT', 'ETCUSDT', 'FILUSDT',
    'APTUSDT', 'ICPUSDT', 'HBARUSDT', 'NEARUSDT', 'VETUSDT', 'ALGOUSDT', 'ARBUSDT', 'GRTUSDT', 'RNDRUSDT', 'STXUSDT',
    'AAVEUSDT', 'MKRUSDT', 'CROUSDT', 'FTMUSDT', 'THETAUSDT', 'RUNEUSDT', 'FLOWUSDT', 'QNTUSDT', 'EGLDUSDT', 'CHZUSDT',
    'SANDUSDT', 'GALAUSDT', 'MANAUSDT', 'AXSUSDT', 'ENJUSDT', 'ZECUSDT', 'XTZUSDT', 'KSMUSDT', 'WAVESUSDT', 'ZILUSDT',
    'DYDXUSDT', 'COMPUSDT', 'SNXUSDT', 'CAKEUSDT', '1INCHUSDT', 'CRVUSDT', 'SUSHIUSDT', 'YFIUSDT', 'UMAUSDT', 'BATUSDT',
    'OCEANUSDT', 'ANKRUSDT', 'SKLUSDT', 'CELOUSDT', 'NEXOUSDT', 'LRCUSDT', 'ZRXUSDT', 'KNCUSDT', 'BALUSDT', 'STMXUSDT',
    'HOTUSDT', 'IOSTUSDT', 'COTIUSDT', 'CHRUSDT', 'ALICEUSDT', 'ILVUSDT', 'AUDIOUSDT', 'REEFUSDT', 'CTSIUSDT', 'FETUSDT',
    'AGIXUSDT', 'RLCUSDT', 'PONDUSDT', 'CLVUSDT', 'ATAUSDT', 'FIDAUSDT', 'ORNUSDT', 'POLSUSDT', 'DUSKUSDT',
    'CVCUSDT', 'STORJUSDT', 'BLZUSDT', 'VTHOUSDT', 'WINUSDT', 'SXPUSDT', 'TLMUSDT', 'TWTUSDT', 'BTTUSDT', '1000PEPEUSDT',
    'BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'XRPUSDC', 'DOGEUSDC', 'ADAUSDC', 'AVAXUSDC', 'DOTUSDC', 'LINKUSDC', 'MATICUSDC',
    'UNIUSDC', 'ATOMUSDC', 'LTCUSDC', 'BCHUSDC', 'NEARUSDC', 'APTUSDC', 'ARBUSDC', 'OPUSDC', 'FILUSDC', 'HBARUSDC', 'RAVEUSDT'
}


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def generate_links(symbol):
    """Генерация ссылок на аналитические ресурсы"""
    clean_symbol = symbol.replace('USDT', '').replace('1000', '')
    # Исправленная ссылка Coinglass TV (как у тебя было раньше)
    coinglass_symbol = f"Binance_{symbol}"
    return {
        'coinglass': f"https://www.coinglass.com/tv/{coinglass_symbol}",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}",
        'binance': f"https://www.binance.com/ru/trade/{symbol}",
        'bybit': f"https://www.bybit.com/trade/usdt/{symbol}"
    }


def send_telegram_alert(message, symbol):
    try:
        log(f"Отправка: {message[:50]}...")

        # Генерируем ссылки для символа
        links = generate_links(symbol)

        # Добавляем ссылки к сообщению
        message_with_links = (
            f"{message}\n\n"
            f"🔗 <b>Быстрый анализ:</b>\n"
            f"• 📊 <a href='{links['coinglass']}'>Coinglass TV</a>\n"
            f"• 📈 <a href='{links['tradingview']}'>TradingView</a>\n"
            f"• 💰 <a href='{links['binance']}'>Binance</a>\n"
            f"• ⚡ <a href='{links['bybit']}'>Bybit</a>"
        )

        # Используем моноширинный шрифт для символа в сообщении
        monospace_symbol = f"<code>{symbol}</code>"
        message_with_links = message_with_links.replace(symbol, monospace_symbol)

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message_with_links,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }
        response = requests.post(url, json=payload)
        response.raise_for_status()
        log("Сообщение отправлено в Telegram")
        return True
    except Exception as e:
        log(f"ОШИБКА Telegram: {str(e)}")
        return False


def is_top_100_coin(symbol):
    """Проверяем, является ли монета топ-100 по капитализации"""
    return symbol in TOP_100_COINS


def on_message(ws, message):
    try:
        log(f"Получены данные: {message[:100]}...")

        data = json.loads(message)

        if TEST_MODE:
            log("Тестовый режим: отправка fake-ликвидации")
            test_message = (
                f"⚡️ <b>ТЕСТОВОЕ УВЕДОМЛЕНИЕ</b>\n"
                f"Сторона: 🔴 SHORT\n"
                f"Сумма: $999,999\n"
                f"Время: {datetime.now().strftime('%H:%M:%S')}"
            )
            send_telegram_alert(test_message, "TESTUSDT")
            return

        if 'e' in data and data['e'] == 'forceOrder':
            liq = data['o']
            symbol = liq.get('s', 'UNKNOWN')

            # Проверяем, является ли монета топ-100 (игнорируем если ДА)
            if is_top_100_coin(symbol):
                log(f"Игнорируем топ-100 монету: {symbol}")
                return

            side = liq.get('S')  # 'BUY' или 'SELL'
            quantity = float(liq.get('q', 0))
            price = float(liq.get('ap', 0))
            usd_value = quantity * price
            time_stamp = datetime.fromtimestamp(data.get('E', 0) / 1000).strftime('%H:%M:%S')

            log(f"Обработка: {symbol} {side} ${usd_value:,.0f}")

            # ФИЛЬТРАЦИЯ: отправляем только ШОРТЫ (SHORT) - когда цена растет
            # В Binance: 'SELL' = ликвидация LONG, 'BUY' = ликвидация SHORT
            if side == 'BUY' and usd_value >= LIQUIDATION_THRESHOLD:
                msg = (
                    f"📈 <b>Рост цены + Ликвидация SHORT</b>\n"
                    f"Монета: <code>{symbol}</code>\n"
                    f"🔴 Ликвидировано SHORT: <code>${usd_value:,.0f}</code>\n"
                    f"💰 Цена ликвидации: <code>{price:.2f}</code>\n"
                    f"🕒 Время: <code>{time_stamp}</code>\n"
                    f"📊 Не топ-100 монета"
                )
                send_telegram_alert(msg, symbol)
            else:
                if side != 'BUY':
                    log(f"Игнорируем LONG ликвидацию (падение цены): {symbol}")
                else:
                    log("Ликвидация ниже порога, игнорируем")

    except Exception as e:
        log(f"КРИТИЧЕСКАЯ ОШИБКА: {str(e)}")


def on_error(ws, error):
    log(f"ОШИБКА WebSocket: {str(error)}")
    time.sleep(5)
    connect_websocket()


def on_close(ws, close_status_code, close_msg):
    log(f"Соединение закрыто. Код: {close_status_code}, Причина: {close_msg}")
    time.sleep(2)
    connect_websocket()


def on_open(ws):
    log("Успешное подключение к Binance WebSocket")
    send_telegram_alert(
        f"🔌 Бот запущен и подключен к Binance\n\n📈 Отслеживаю SHORT ликвидации (когда цена растет) для НЕ топ-100 монет\n\n📊 Игнорируется {len(TOP_100_COINS)} монет",
        "SYSTEM")
    if TEST_MODE:
        log("Тестовый режим активирован")


def connect_websocket():
    log("Попытка подключения к WebSocket...")
    try:
        ws = WebSocketApp("wss://fstream.binance.com/ws/!forceOrder@arr",
                          on_open=on_open,
                          on_message=on_message,
                          on_error=on_error,
                          on_close=on_close)
        ws.run_forever()
    except Exception as e:
        log(f"ОШИБКА ПОДКЛЮЧЕНИЯ: {str(e)}")


if __name__ == "__main__":
    log("Запуск бота ликвидаций")
    log(f"Игнорируемые монеты: {len(TOP_100_COINS)} топ-100")
    log("Отслеживаю только SHORT ликвидации (рост цены)")
    log("Ссылка Coinglass TV: https://www.coinglass.com/tv/Binance_XXX")

    try:
        import websocket

        log("Все зависимости установлены")
    except ImportError as e:
        log(f"ОШИБКА: Не установлены зависимости - {str(e)}")
        exit(1)

    connect_websocket()
