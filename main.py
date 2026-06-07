import os
import requests
import time
from datetime import datetime

# Настройки
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SYMBOL = "SOLUSDT"
PRICE_UP = 2.0      # Рост от 2%
PRICE_DOWN = -2.0   # Падение от 2%
TIME_WINDOW = 300   # 5 минут

# Хранилище цен
price_history = []

def send_alert(chat_id, direction, change, old_price, new_price):
    """Отправка простого уведомления"""
    if direction == "up":
        text = f"🚨 {SYMBOL} Рост!\n📈 +{change:.1f}%\n💰 {old_price:.4f} → {new_price:.4f}"
    else:
        text = f"🔻 {SYMBOL} Падение!\n📉 {change:.1f}%\n💰 {old_price:.4f} → {new_price:.4f}"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={'chat_id': chat_id, 'text': text})

def main():
    print(f"Мониторинг {SYMBOL} | ±{abs(PRICE_UP)}% за 5 мин")
    
    while True:
        try:
            # Получаем цену SOLUSDT
            resp = requests.get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={SYMBOL}")
            price = float(resp.json()['result']['list'][0]['lastPrice'])
            now = time.time()
            
            # Сохраняем историю
            price_history.append({'price': price, 'time': now})
            price_history[:] = [p for p in price_history if now - p['time'] <= TIME_WINDOW]
            
            # Проверяем изменение за 5 минут
            if len(price_history) > 1:
                old_price = price_history[0]['price']
                change = ((price - old_price) / old_price) * 100
                
                if change >= PRICE_UP:
                    print(f"Рост: +{change:.1f}%")
                    send_alert("5296533274", "up", change, old_price, price)
                    time.sleep(60)  # Не спамим 1 минуту
                elif change <= PRICE_DOWN:
                    print(f"Падение: {change:.1f}%")
                    send_alert("5296533274", "down", change, old_price, price)
                    time.sleep(60)  # Не спамим 1 минуту
            
            time.sleep(5)
            
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
