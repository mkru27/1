# StarGifty (Polling)

Клон функционала @AutoOneRobot под Telegram Stars: ручная покупка и автоснайпер NFT-подарков.
**Эта сборка — на пулинге** для локальной проверки. Далее легко переведём на вебхуки.

## Быстрый старт
1) Python 3.10+
2) Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
3) Скопируйте `.env.example` в `.env` и вставьте `BOT_TOKEN` от BotFather.
4) Запуск:
   ```bash
   python bot.py
   ```
5) Напишите боту `/start`.

## Что дальше
- Заполнить методы в `TelegramMarketClient` реальными вызовами маркета в ТГ:
  - `search_current_listings`
  - `search_new_listings`
  - `buy_item`
  - `transfer_nft`
- Перевести на вебхуки для продакшена.
