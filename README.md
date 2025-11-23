## Асинхронный Telegram → Bybit трейдинг-бот (русские новости)

Проект `trading_bot` реализует полный пайплайн:

- **Telegram (Pyrogram, вход как пользователь)** → чтение русскоязычных новостных каналов
- **Ключевой новостной анализ (русские словари)** → `analysis/keyword_analyzer.py`
- **Технический анализ по Bybit (RSI, EMA, ATR, объёмы, OI)** → `analysis/tech_indicators.py`
- **Движок решений (комбинация news + tech)** → `analysis/decision_engine.py`
- **Торговля на Bybit (REST + WebSocket каркас)** → `bybit/api.py`, `bybit/orders.py`
- **Хранение новостей в JSON** → `storage/storage.py`

Все основные модули работают асинхронно на `asyncio` и `aiohttp`.

---

### Установка

```bash
cd /Users/ldst/Desktop/crypto
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

### Настройки: .env и переменные окружения

Бот читает конфиг из двух источников:

1. Файл `.env` в корне проекта (основной способ, чтобы не вводить ничего руками).
2. Переменные окружения (как запасной вариант; используются, если какого‑то ключа нет в `.env`).

#### Формат `.env`

Создайте в корне (`/Users/ldst/Desktop/crypto/.env`) файл вида:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_telegram_api_hash
TELEGRAM_SESSION_NAME=trading_bot_session
TELEGRAM_CHANNEL_IDS=-1001234567890,-100987654321

BYBIT_API_KEY=your_bybit_key
BYBIT_API_SECRET=your_bybit_secret
BYBIT_BASE_URL=https://api.bybit.com
BYBIT_WS_URL=wss://stream.bybit.com/v5/public/linear

DEFAULT_SYMBOL=BTCUSDT
DEFAULT_TIMEFRAME=60
MAX_CANDLES=500
LOG_LEVEL=INFO
```

- Формат строк: `KEY=VALUE`, комментарии можно начинать с `#`.
- Значения из `.env` имеют **приоритет** над переменными окружения.

---

### Запуск бота

```bash
cd /Users/ldst/Desktop/crypto
python -m trading_bot.main
```

При первом запуске Pyrogram создаст сессию пользователя и попросит ввести код/пароль в консоли.

---

### Логика сигналов

- **Новостной анализ** (`analysis/keyword_analyzer.py`)
  - Словари `positive_keywords`, `negative_keywords`, `neutral_keywords` полностью на русском.
  - Поиск тикера по регэкспу `\\b([A-Z]{2,10})\\b` (например, `BTC`, `BTCUSDT`).

- **Технический анализ** (`analysis/tech_indicators.py`)
  - RSI(14), EMA(25/50), ATR, изменение объёма (последняя свеча к предыдущей), тренд Open Interest.
  - `technical_score` собирается по правилам из задания.

- **Движок решений** (`analysis/decision_engine.py`)
  - `final_score = 0.6 * news_score + 0.4 * technical_score`
  - `final_score > 0.3` → `LONG`
  - `final_score < -0.3` → `SHORT`
  - иначе `HOLD`
  - TP/SL считаются от ATR: `tp = 1.5 * ATR`, `sl = 0.8 * ATR`.

- **Торговля** (`bybit/orders.py`)
  - `create_long`, `create_short`, `set_tp_sl` — асинхронные, используют REST v5.

---

### Где я сделал чуть лучше, чем в ТЗ

- **Нормализация news_score** — после суммирования весов ключевых слов score ограничивается в диапазон \\([-1, 1]\\), чтобы экстремально длинные новости не ломали шкалу.
- **Более аккуратная работа с OI и kline** — данные приводятся к удобному внутреннему формату перед расчётом индикаторов.
- **WS‑клиент Bybit** — вынесен в отдельный класс `BybitWSClient`, чтобы проще было расширять стриминг цен/объёмов, хотя в основном пайплайне достаточно REST.


