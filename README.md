# NewsCryptoBot

Асинхронный торговый бот, который читает русскоязычные новостные каналы в Telegram, оценивает их влияние на конкретный тикер, накладывает технический анализ и открывает позицию на криптобирже.

## Как это работает

```
Telegram-канал → извлечение тикера → анализ новости ─┐
                                                     ├→ агрегация скоров → LONG / SHORT / HOLD → ордер + TP/SL
        свечи, объёмы, Open Interest → теханализ ────┘
```

Бот подключается к Telegram как пользователь (Pyrogram), поэтому читает любые каналы, на которые подписан аккаунт. Каждое новое сообщение проходит пайплайн: из текста вытаскивается тикер, новость оценивается на тональность, параллельно считаются индикаторы по свечам с биржи, затем модуль агрегации сводит оценки в одно решение и, если сигнал уверенный, выставляется ордер с тейк-профитом и стоп-лоссом от ATR.

## Модули анализа

| Модуль | Файл | Назначение |
|---|---|---|
| NPM | `analysis/npm.py` | Оценка тональности новости трансформерной моделью (по умолчанию FinBERT) с экспоненциальным затуханием влияния во времени |
| TAM | `analysis/tam.py` | Технический анализ: RSI, EMA, ATR, динамика объёма и Open Interest |
| TSM | `analysis/tsm.py` | Прогноз по временному ряду на LSTM; без файла весов работает в нейтральном режиме |
| FDS | `analysis/fds.py` | Детектор флэта — гасит сигналы в боковике |
| SGS | `analysis/sgs.py` | Агрегация оценок модулей в итоговый сигнал |
| — | `analysis/keyword_analyzer.py` | Русскоязычные словари ключевых слов и поиск тикера по регулярному выражению |
| — | `analysis/tech_indicators.py` | Базовые расчёты индикаторов |

## Поддерживаемые биржи

Bybit, Binance и Gate.io подключены через общий интерфейс: `exchange/base.py` задаёт контракт, адаптеры реализуют его под каждую биржу, `factory.py` выбирает нужный, а `router.py` определяет, где торговать конкретным символом. Порядок приоритета задаётся переменной `EXCHANGE_PRIORITY`.

## Стек

Python 3.12, `asyncio`, `aiohttp`, Pyrogram, PyTorch + Transformers, NumPy, statsmodels.

## Установка

```bash
git clone https://github.com/66emil/NewsCryptoBot.git
cd NewsCryptoBot
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Настройка

Создайте `.env` в корне проекта:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_telegram_api_hash
TELEGRAM_SESSION_NAME=trading_bot_session
TELEGRAM_CHANNEL_IDS=-1001234567890,-100987654321

BYBIT_API_KEY=
BYBIT_API_SECRET=
BYBIT_BASE_URL=https://api.bybit.com
BYBIT_WS_URL=wss://stream.bybit.com/v5/public/linear

GATE_API_KEY=
GATE_API_SECRET=
BINANCE_API_KEY=
BINANCE_API_SECRET=

EXCHANGE_PRIORITY=gateio,binance,bybit
DEFAULT_SYMBOL=BTCUSDT
DEFAULT_TIMEFRAME=60
MAX_CANDLES=500

NPM_MODEL_NAME=ProsusAI/finbert
NPM_LAMBDA_DECAY=0.1
TSM_MODEL_PATH=
TSM_WINDOW=60
LOG_LEVEL=INFO
```

Значения из `.env` имеют приоритет над системными переменными окружения. Полный список параметров — в `trading_bot/config.py`.

## Запуск

```bash
python -m trading_bot.main
```

При первом запуске Pyrogram создаст файл сессии и запросит код подтверждения в консоли.

## Логика решения

Итоговый скор собирается из новостной и технической составляющих. Порог `> 0.3` открывает LONG, `< -0.3` — SHORT, между ними бот остаётся вне рынка. Тейк-профит и стоп-лосс считаются от ATR: `tp = 1.5 × ATR`, `sl = 0.8 × ATR`. Открытые позиции проверяются фоновой задачей мониторинга.

Новостной скор нормализуется в диапазон `[-1, 1]`, чтобы длинные сообщения с большим числом ключевых слов не ломали шкалу.

## Структура

```
trading_bot/
├── main.py           точка входа, пайплайн обработки сообщений
├── config.py         загрузка конфигурации из .env
├── telegram/         клиент Pyrogram и обработчик новых сообщений
├── exchange/         base, factory, router и адаптеры бирж
├── bybit/            REST/WS-клиент и выставление ордеров
├── analysis/         NPM, TAM, TSM, FDS, SGS и индикаторы
├── models/           LSTM-модель для TSM
└── storage/          сохранение новостей в JSON
```

## Дисклеймер

Проект написан в исследовательских целях. Алгоритмическая торговля криптовалютой сопряжена с риском полной потери средств. Прогоняйте бота на тестовой сети или с минимальным объёмом, прежде чем подключать реальный счёт.

## Что стоит вычистить из репозитория

В текущем состоянии в git закоммитированы файл сессии Pyrogram (`trading_bot_session.session`) и виртуальное окружение `.venv/`. Файл сессии даёт полный доступ к Telegram-аккаунту — его нужно удалить из истории и отозвать сессию в настройках Telegram. Обе записи стоит добавить в `.gitignore`.
