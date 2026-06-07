import os
import requests
import time
from datetime import datetime
import threading

# Настройки
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SYMBOL = "SOLUSDT"
PRICE_UP = 2.0      # Рост от 2%
PRICE_DOWN = -2.0   # Падение от 2%
TIME_WINDOW = 300   # 5 минут

# Хранилище пользователей и цен
users = {}  # {chat_id: True}
price_history = []

def send_message(chat_id, text):
    """Отправка сообщения в Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={'chat_id': chat_id, 'text': text})
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def send_alert(direction, change, old_price, new_price):
    """Отправка уведомления всем пользователям"""
    if direction == "up":
        text = f"🚨 {SYMBOL} Рост!\n📈 +{change:.1f}%\n💰 {old_price:.4f} → {new_price:.4f}"
    else:
        text = f"🔻 {SYMBOL} Падение!\n📉 {change:.1f}%\n💰 {old_price:.4f} → {new_price:.4f}"
    
    for chat_id in users.keys():
        send_message(chat_id, text)

def handle_telegram():
    """Обработка команд от пользователей"""
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
                            send_message(chat_id, f"✅ Подписка оформлена!\n\nМониторинг {SYMBOL}\nРост +{PRICE_UP}%\nПадение {PRICE_DOWN}%\nОкно {TIME_WINDOW // 60} мин")
                            print(f"➕ Новый пользователь: {chat_id}")
                        
                        elif text == '/stop':
                            if chat_id in users:
                                del users[chat_id]
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
    
    # Запускаем обработчик команд в отдельном потоке
    thread = threading.Thread(target=handle_telegram, daemon=True)
    thread.start()
    
    print(f"✅ Бот запущен | {SYMBOL} | ±{abs(PRICE_UP)}% за {TIME_WINDOW // 60} мин")
    print("📌 Пользователи могут подписаться через /start")
    
    while True:
        try:
            # Получаем цену SOLUSDT
            resp = requests.get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={SYMBOL}")
            price = float(resp.json()['result']['list'][0]['lastPrice'])
            now = time.time()
            
            # Сохраняем историю
            price_history.append({'price': price, 'time': now})
            price_history[:] = [p for p in price_history if now - p['time'] <= TIME_WINDOW]
            
            # Проверяем изменение
            if len(price_history) > 1 and users:
                old_price = price_history[0]['price']
                change = ((price - old_price) / old_price) * 100
                
                if change >= PRICE_UP:
                    print(f"📈 Рост: +{change:.1f}%")
                    send_alert("up", change, old_price, price)
                    time.sleep(60)
                    
                elif change <= PRICE_DOWN:
                    print(f"📉 Падение: {change:.1f}%")
                    send_alert("down", change, old_price, price)
                    time.sleep(60)
            
            time.sleep(5)
            
        except KeyboardInterrupt:
            print("\n❌ Бот остановлен")
            break
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
