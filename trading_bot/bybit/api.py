import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

from trading_bot.config import get_config

logger = logging.getLogger(__name__)


RECV_WINDOW_MS = 5000


def _sign_v5(api_key: str, secret: str, timestamp: int, recv_window: int, body: str) -> str:
    """
    Подпись для приватных запросов Bybit v5 (SIGN-TYPE=2).

    Согласно доке Bybit:
        sign = HMAC_SHA256(secret, f"{timestamp}{api_key}{recv_window}{body}")
    где body — строка JSON (для POST) или query string (для GET).
    """
    payload = f"{timestamp}{api_key}{recv_window}{body}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


async def _request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    private: bool = False,
) -> Any:
    config = get_config()
    base_url = config.BYBIT_BASE_URL.rstrip("/")
    url = f"{base_url}{path}"
    params = params or {}

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
    }

    method_upper = method.upper()

    # Формируем "тело" для подписи:
    # - для POST: compact JSON
    # - для приватного GET: query string
    body_str = ""
    if method_upper == "POST":
        body_str = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
    elif method_upper == "GET" and private and params:
        # query string в алфавитном порядке ключей
        body_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    if private:
        api_key = config.BYBIT_API_KEY
        api_secret = config.BYBIT_API_SECRET
        if not api_key or not api_secret:
            logger.error("BYBIT_API_KEY или BYBIT_API_SECRET не заданы. Приватный запрос невозможен.")
            return None

        timestamp = int(time.time() * 1000)
        recv_window = RECV_WINDOW_MS

        sign = _sign_v5(api_key=api_key, secret=api_secret, timestamp=timestamp, recv_window=recv_window, body=body_str)

        headers.update(
            {
                "X-BAPI-API-KEY": api_key,
                "X-BAPI-TIMESTAMP": str(timestamp),
                "X-BAPI-RECV-WINDOW": str(recv_window),
                "X-BAPI-SIGN": sign,
                "X-BAPI-SIGN-TYPE": "2",
            }
        )

        logger.debug(
            "Bybit private headers (без секрета): key=%s, timestamp=%s, recv_window=%s",
            api_key,
            timestamp,
            recv_window,
        )

    logger.debug("Bybit request %s %s params=%s", method_upper, url, params)

    async with aiohttp.ClientSession() as session:
        try:
            if method_upper == "GET":
                async with session.get(url, params=params, headers=headers, timeout=10) as resp:
                    data = await resp.json()
            else:
                # Для приватных POST используем body_str, чтобы совпадало с тем, что подписывали.
                if private:
                    async with session.post(url, data=body_str, headers=headers, timeout=10) as resp:
                        data = await resp.json()
                else:
                    async with session.post(url, json=params, headers=headers, timeout=10) as resp:
                        data = await resp.json()
        except asyncio.TimeoutError:
            logger.error("Таймаут запроса к Bybit: %s", url)
            return None
        except aiohttp.ClientError as e:
            logger.error("Ошибка HTTP при запросе к Bybit: %s", e)
            return None

    logger.debug("Ответ Bybit: %s", data)
    return data


async def get_instrument_info(symbol: str) -> Dict[str, Any]:
    """
    Получить параметры инструмента (lot size, tick size и т.п.) для symbol.
    """
    params = {
        "category": "linear",
        "symbol": symbol,
    }
    data = await _request("GET", "/v5/market/instruments-info", params=params, private=False)
    if not data or data.get("retCode") != 0:
        logger.warning("Не удалось получить instrument info для %s: %s", symbol, data)
        return {}
    items = data.get("result", {}).get("list", [])
    return items[0] if items else {}


async def get_wallet_balance(coin: str = "USDT", account_type: str = "UNIFIED") -> float:
    """
    Получить доступный баланс по монете (по умолчанию USDT).
    Пробует сначала UNIFIED аккаунт, если не вышло — CONTRACT (для классических аккаунтов).
    """
    # 1. Пробуем запрошенный account_type (по умолчанию UNIFIED)
    types_to_try = [account_type]
    if account_type == "UNIFIED":
        # Если не нашли в UNIFIED, попробуем CONTRACT (Standard Account)
        types_to_try.append("CONTRACT")

    for ac_type in types_to_try:
        params = {
            "accountType": ac_type,
            "coin": coin,
        }
        # Логируем попытку, чтобы понимать, какой тип сработал
        logger.debug("Запрос баланса Bybit, accountType=%s, coin=%s", ac_type, coin)

        data = await _request("GET", "/v5/account/wallet-balance", params=params, private=True)
        
        # Если ошибка API (кроме "неверный аккаунт", который может быть при несовпадении типа)
        # Но Bybit обычно просто возвращает пустой list или ошибку, если типа нет.
        if not data or data.get("retCode") != 0:
            logger.debug("Не удалось получить баланс Bybit (type=%s): %s", ac_type, data)
            continue

        try:
            lists = data.get("result", {}).get("list", [])
            if not lists:
                continue
            
            coins = lists[0].get("coin", [])
            for c in coins:
                if c.get("coin") == coin:
                    # Безопасно извлекаем значения, так как API может вернуть null
                    wb = c.get("walletBalance")
                    eq = c.get("equity")
                    
                    # Преобразуем в float, если не None
                    val_wb = float(wb) if wb is not None else 0.0
                    val_eq = float(eq) if eq is not None else 0.0
                    
                    # Используем то, что больше 0 (обычно они равны, если нет PnL)
                    val = val_wb or val_eq
                    
                    if val > 0:
                        logger.info("Баланс Bybit получен (type=%s): %f %s", ac_type, val, coin)
                        return val
        except (TypeError, ValueError) as e:
            logger.debug("Ошибка парсинга баланса Bybit: %s", e)
            continue

    logger.warning("Баланс Bybit не найден ни для одного типа аккаунта: %s", types_to_try)
    return 0.0


async def get_klines(symbol: str, timeframe: str, limit: int = 200) -> List[Dict[str, Any]]:
    """
    Получение исторических свечей.
    """
    # Примерный эндпоинт v5 market kline
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": timeframe,
        "limit": limit,
    }
    data = await _request("GET", "/v5/market/kline", params=params, private=False)
    if not data or data.get("retCode") != 0:
        logger.warning("Не удалось получить kline для %s: %s", symbol, data)
        return []

    result = []
    for item in data.get("result", {}).get("list", []):
        # Формат упрощён в более удобный для нас вид
        # [startTime, open, high, low, close, volume, turnover]
        try:
            result.append(
                {
                    "openTime": int(item[0]),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
            )
        except (IndexError, ValueError, TypeError):
            continue
    return result


async def get_ticker(symbol: str) -> Dict[str, Any]:
    """
    Получить текущий тикер (последняя цена и др.).
    """
    params = {
        "category": "linear",
        "symbol": symbol,
    }
    data = await _request("GET", "/v5/market/tickers", params=params, private=False)
    if not data or data.get("retCode") != 0:
        logger.warning("Не удалось получить тикер для %s: %s", symbol, data)
        return {}
    tickers = data.get("result", {}).get("list", [])
    if not tickers:
        return {}
    return tickers[0]


async def get_open_interest(symbol: str, timeframe: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Получить серию open interest по времени, если доступно.
    """
    params = {
        "category": "linear",
        "symbol": symbol,
        "intervalTime": timeframe,
        "limit": limit,
    }
    data = await _request("GET", "/v5/market/open-interest", params=params, private=False)
    if not data or data.get("retCode") != 0:
        logger.warning("Не удалось получить open interest для %s: %s", symbol, data)
        return []
    result = []
    for item in data.get("result", {}).get("list", []):
        try:
            result.append(
                {
                    "timestamp": int(item["timestamp"]),
                    "openInterest": float(item["openInterest"]),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue
    return result


class BybitWSClient:
    """
    Простой WebSocket-клиент для получения последней цены, объёмов и OI.
    Не используется напрямую в основном потоке, но может быть расширен для стриминга.
    """

    def __init__(self) -> None:
        self._config = get_config()
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        async with self._lock:
            if self._ws and not self._ws.closed:
                return
            self._session = aiohttp.ClientSession()
            logger.info("Подключение к Bybit WS: %s", self._config.BYBIT_WS_URL)
            self._ws = await self._session.ws_connect(self._config.BYBIT_WS_URL)

    async def subscribe_ticker(self, symbol: str) -> None:
        await self.connect()
        assert self._ws is not None
        msg = {
            "op": "subscribe",
            "args": [f"tickers.{symbol}"],
        }
        await self._ws.send_str(json.dumps(msg))
        logger.info("Подписка на тикер %s через WS", symbol)

    async def listen(self):
        """
        Простой слушатель, который логирует входящие сообщения.
        """
        if not self._ws:
            await self.connect()
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                logger.debug("WS сообщение: %s", msg.data)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("WS ошибка: %s", msg)
                break

    async def close(self) -> None:
        async with self._lock:
            if self._ws and not self._ws.closed:
                await self._ws.close()
            if self._session and not self._session.closed:
                await self._session.close()
            logger.info("WS соединение Bybit закрыто")


