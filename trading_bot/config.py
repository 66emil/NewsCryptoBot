import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass
class Config:
    # Telegram
    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: str
    TELEGRAM_SESSION_NAME: str
    TELEGRAM_CHANNEL_IDS: List[int]

    # Bybit
    BYBIT_API_KEY: str
    BYBIT_API_SECRET: str
    BYBIT_BASE_URL: str
    BYBIT_WS_URL: str

    # Gate.io
    GATE_API_KEY: str
    GATE_API_SECRET: str
    GATE_BASE_URL: str

    # Binance
    BINANCE_API_KEY: str
    BINANCE_API_SECRET: str
    BINANCE_BASE_URL: str

    # Trading
    DEFAULT_SYMBOL: str
    DEFAULT_TIMEFRAME: str
    MAX_CANDLES: int

    # Logging / общие настройки
    LOG_LEVEL: str = "INFO"
    EXCHANGE_NAME: str = "bybit"
    EXCHANGE_PRIORITY: str = "gateio,binance,bybit"

    # NPM — News Processing Module
    NPM_MODEL_NAME: str = "ProsusAI/finbert"
    NPM_LAMBDA_DECAY: float = 0.1

    # TSM — Time Series Module (LSTM)
    TSM_MODEL_PATH: str = ""  # путь к файлу весов .pth; пустая строка = режим нейтрального скора
    TSM_WINDOW: int = 60


def _load_env_file() -> Dict[str, str]:
    """
    Загрузка значений из .env в корне проекта.
    Формат строк: KEY=VALUE, комментарии начинаются с #.
    Значения из .env имеют приоритет над системным окружением.
    """
    if not ENV_PATH.exists():
        return {}

    values: Dict[str, str] = {}
    try:
        with ENV_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    values[key] = value
    except OSError:
        return {}
    return values


def _get(key: str, default: str = "") -> str:
    """
    Берёт значение из .env (если есть), иначе из системного окружения.
    """
    env = _load_env_file()
    if key in env:
        return env[key]
    return os.getenv(key, default)


def _parse_channel_ids(raw: str) -> List[int]:
    if not raw:
        return []
    ids: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


def get_config() -> Config:
    return Config(
        TELEGRAM_API_ID=int(_get("TELEGRAM_API_ID", "0")),
        TELEGRAM_API_HASH=_get("TELEGRAM_API_HASH", ""),
        TELEGRAM_SESSION_NAME=_get("TELEGRAM_SESSION_NAME", "trading_bot_session"),
        TELEGRAM_CHANNEL_IDS=_parse_channel_ids(_get("TELEGRAM_CHANNEL_IDS", "")),
        BYBIT_API_KEY=_get("BYBIT_API_KEY", ""),
        BYBIT_API_SECRET=_get("BYBIT_API_SECRET", ""),
        BYBIT_BASE_URL=_get("BYBIT_BASE_URL", "https://api.bybit.com"),
        BYBIT_WS_URL=_get("BYBIT_WS_URL", "wss://stream.bybit.com/v5/public/linear"),
        GATE_API_KEY=_get("GATE_API_KEY", ""),
        GATE_API_SECRET=_get("GATE_API_SECRET", ""),
        GATE_BASE_URL=_get("GATE_BASE_URL", "https://api.gateio.ws/api/v4"),
        BINANCE_API_KEY=_get("BINANCE_API_KEY", ""),
        BINANCE_API_SECRET=_get("BINANCE_API_SECRET", ""),
        BINANCE_BASE_URL=_get("BINANCE_BASE_URL", "https://fapi.binance.com"),
        DEFAULT_SYMBOL=_get("DEFAULT_SYMBOL", "BTCUSDT"),
        DEFAULT_TIMEFRAME=_get("DEFAULT_TIMEFRAME", "60"),
        MAX_CANDLES=int(_get("MAX_CANDLES", "500")),
        LOG_LEVEL=_get("LOG_LEVEL", "INFO"),
        EXCHANGE_NAME=_get("EXCHANGE_NAME", "bybit"),
        EXCHANGE_PRIORITY=_get("EXCHANGE_PRIORITY", "gateio,bybit,binance"),
        NPM_MODEL_NAME=_get("NPM_MODEL_NAME", "ProsusAI/finbert"),
        NPM_LAMBDA_DECAY=float(_get("NPM_LAMBDA_DECAY", "0.1")),
        TSM_MODEL_PATH=_get("TSM_MODEL_PATH", ""),
        TSM_WINDOW=int(_get("TSM_WINDOW", "60")),
    )


