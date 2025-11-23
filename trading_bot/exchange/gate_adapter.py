import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

from trading_bot.config import get_config
from trading_bot.exchange.base import ExchangeAdapter

logger = logging.getLogger(__name__)


class GateExchange(ExchangeAdapter):
    """
    Адаптер Gate.io (USDT‑маржинальные фьючерсы, futures/usdt).

    ВНИМАНИЕ: реализация основана на общедоступной спецификации API Gate.io v4.
    Некоторые детали (например, поля ордера) могут потребовать точной настройки по доке.
    """

    name = "gateio"

    def __init__(self) -> None:
        # Берём настройки Gate.io из общего конфига, который читает .env
        config = get_config()
        self._base_url = config.GATE_BASE_URL.rstrip("/")
        self._api_key = config.GATE_API_KEY
        self._api_secret = config.GATE_API_SECRET

    # ---------- Вспомогательные методы ----------

    def _to_gate_symbol(self, symbol: str) -> str:
        """
        Преобразует универсальный символ вида BTCUSDT → BTC_USDT.
        """
        s = symbol.upper()
        if "_" in s:
            return s
        if s.endswith("USDT"):
            base = s[:-4]
            quote = "USDT"
        else:
            base = s
            quote = "USDT"
        return f"{base}_{quote}"

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        private: bool = False,
    ) -> Any:
        """
        Низкоуровневый запрос к Gate.io (futures/usdt).
        """
        url_path = f"/api/v4{path}"
        url = f"{self._base_url}{path}"
        method_upper = method.upper()
        params = params or {}
        body = body or {}

        # Формируем query и body строки
        query_str = ""
        if params:
            from urllib.parse import urlencode

            query_str = urlencode(params, doseq=True)

        body_str = ""
        if method_upper in ("POST", "PUT", "DELETE") and body:
            body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }

        if private:
            if not self._api_key or not self._api_secret:
                logger.error("Gate.io: GATE_API_KEY или GATE_API_SECRET не заданы. Приватный запрос невозможен.")
                return None

            ts = str(int(time.time()))
            # Подпись согласно спецификации Gate.io v4:
            # sign = HMAC_SHA512(secret, f"{ts}\n{method}\n{url_path}\n{query}\n{body}")
            sign_payload = "\n".join([ts, method_upper, url_path, query_str, body_str])
            sign = hmac.new(self._api_secret.encode(), sign_payload.encode(), hashlib.sha512).hexdigest()

            headers.update(
                {
                    "KEY": self._api_key,
                    "SIGN": sign,
                    "Timestamp": ts,
                }
            )

        logger.debug("Gate.io request %s %s params=%s body=%s", method_upper, url, params, body)

        async with aiohttp.ClientSession() as session:
            try:
                if method_upper == "GET":
                    async with session.get(url, params=params, headers=headers, timeout=10) as resp:
                        data = await resp.json()
                else:
                    async with session.request(
                        method_upper,
                        url,
                        params=params if method_upper != "POST" else None,
                        data=body_str if body_str else None,
                        headers=headers,
                        timeout=10,
                    ) as resp:
                        data = await resp.json()
            except asyncio.TimeoutError:
                logger.error("Gate.io: таймаут запроса %s", url)
                return None
            except aiohttp.ClientError as e:
                logger.error("Gate.io: HTTP‑ошибка при запросе %s: %s", url, e)
                return None

        logger.debug("Gate.io ответ: %s", data)
        return data

    # ---------- Реализация интерфейса ExchangeAdapter ----------

    async def has_market(self, symbol: str) -> bool:
        contract = self._to_gate_symbol(symbol)
        path = f"/futures/usdt/contracts/{contract}"
        data = await self._request("GET", path, private=False)
        exists = isinstance(data, dict) and bool(data.get("name"))
        logger.info("Проверка тикера %s (Gate: %s): %s", symbol, contract, "найден" if exists else "не найден")
        return exists

    async def get_klines(self, symbol: str, timeframe: str, limit: int = 200) -> List[Dict[str, Any]]:
        contract = self._to_gate_symbol(symbol)
        # Gate.io для futures/usdt использует строковые интервалы:
        # 1m, 5m, 15m, 30m, 1h, 4h, 1d и т.п.
        tf = str(timeframe).strip().lower()

        # Если пользователь сразу указал строковый формат (1m/1h/1d) — используем как есть.
        if tf.endswith(("m", "h", "d")):
            interval = tf
        else:
            # Иначе считаем, что это минуты и маппим в один из поддерживаемых форматов.
            try:
                tf_minutes = int(tf)
            except ValueError:
                interval = tf
            else:
                mapping = {
                    1: "1m",
                    5: "5m",
                    15: "15m",
                    30: "30m",
                    60: "1h",
                    240: "4h",
                    1440: "1d",
                }
                interval = mapping.get(tf_minutes, f"{tf_minutes}m")

        params = {
            "contract": contract,
            "interval": interval,
            "limit": limit,
        }
        data = await self._request("GET", "/futures/usdt/candlesticks", params=params, private=False)
        if not isinstance(data, list):
            logger.warning("Gate.io: не удалось получить свечи для %s: %s", contract, data)
            return []

        result: List[Dict[str, Any]] = []
        # Gate.io может возвращать свечи как массивы или как словари.
        for item in data:
            try:
                if isinstance(item, (list, tuple)):
                    # Формат: [time, open, high, low, close, volume]
                    t, o, h, l, c, v = item[:6]
                elif isinstance(item, dict):
                    # Возможный словарный формат
                    t = item.get("t") or item.get("time")
                    o = item.get("o") or item.get("open")
                    h = item.get("h") or item.get("high")
                    l = item.get("l") or item.get("low")
                    c = item.get("c") or item.get("close")
                    v = item.get("v") or item.get("volume")
                else:
                    logger.debug("Gate.io: неизвестный формат свечи: %s", item)
                    continue

                result.append(
                    {
                        "openTime": int(float(t)),
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": float(v),
                    }
                )
            except (ValueError, TypeError, IndexError, KeyError) as e:
                logger.debug("Gate.io: не удалось распарсить свечу %s: %s", item, e)
                continue
        return result

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        contract = self._to_gate_symbol(symbol)
        params = {"contract": contract}
        data = await self._request("GET", "/futures/usdt/tickers", params=params, private=False)
        if not isinstance(data, list) or not data:
            logger.warning("Gate.io: не удалось получить тикер для %s: %s", contract, data)
            return {}
        ticker = data[0]
        # Приводим к интерфейсу, похожему на Bybit
        return {
            "lastPrice": float(ticker.get("last", 0.0)),
            "openInterest": float(ticker.get("open_interest", 0.0)) if ticker.get("open_interest") is not None else 0.0,
        }

    async def get_open_interest(self, symbol: str, timeframe: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        У Gate.io нет прямого аналога исторического OI во futures/usdt.
        Для простоты используем текущее значение из тикера и возвращаем список из одного элемента.
        """
        contract = self._to_gate_symbol(symbol)
        params = {"contract": contract}
        data = await self._request("GET", "/futures/usdt/tickers", params=params, private=False)
        if not isinstance(data, list) or not data:
            logger.warning("Gate.io: не удалось получить open interest для %s: %s", contract, data)
            return []
        ticker = data[0]
        try:
            oi = float(ticker.get("open_interest", 0.0))
        except (TypeError, ValueError):
            oi = 0.0
        return [{"timestamp": int(time.time()), "openInterest": oi}]

    async def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        contract = self._to_gate_symbol(symbol)
        path = f"/futures/usdt/contracts/{contract}"
        data = await self._request("GET", path, private=False)
        if not isinstance(data, dict) or not data:
            logger.warning("Gate.io: не удалось получить instrument info для %s: %s", contract, data)
            return {}

        # Приводим к структуре, похожей на Bybit
        lot_size_filter = {
            "qtyStep": data.get("order_size_increment") or data.get("order_size_min") or "0.001",
            "minOrderQty": data.get("order_size_min") or data.get("order_size_increment") or "0.001",
        }
        price_filter = {
            "tickSize": data.get("order_price_increment", "0.01"),
        }
        return {
            "name": data.get("name"),
            "lotSizeFilter": lot_size_filter,
            "priceFilter": price_filter,
        }

    async def get_wallet_balance(self, asset: str = "USDT") -> float:
        # Логируем начало запроса
        logger.debug("Gate.io: запрос баланса для %s", asset)
        
        data = await self._request("GET", "/futures/usdt/accounts", private=True)
        
        # Логируем сырой ответ для отладки
        logger.info("Gate.io raw balance response: %s", data)

        if not isinstance(data, dict):
            logger.warning("Gate.io: не удалось получить баланс аккаунта (неверный формат ответа): %s", data)
            return 0.0

        # Проверка на ошибку API
        if "label" in data:
            logger.error("Gate.io ошибка при получении баланса: %s - %s", data.get("label"), data.get("message"))
            return 0.0

        try:
            # futures/usdt/accounts отдаёт объект с полями, включая available и total
            data_currency = data.get("currency", "")
            data_settle = data.get("settle", "")
            
            # Проверяем совпадение валюты
            if data_currency == asset or data_settle == asset.lower():
                # Аккуратно парсим значения. Обрабатываем случай, когда значение может быть None.
                # dict.get(k, default) возвращает default только если ключа нет. Если ключ есть, но значение null — вернет None.
                raw_av = data.get("available")
                raw_tot = data.get("total")
                
                available = float(raw_av) if raw_av is not None else 0.0
                total = float(raw_tot) if raw_tot is not None else 0.0
                
                logger.info("Gate.io баланс parsed: available=%.4f, total=%.4f, currency=%s", available, total, data_currency)
                
                # Для торговли используем доступный баланс.
                return available
            else:
                logger.warning("Gate.io: валюта аккаунта (%s/settle=%s) не совпадает с запрошенной (%s)", data_currency, data_settle, asset)
        except (TypeError, ValueError) as e:
            logger.error("Gate.io: ошибка парсинга баланса: %s", e)
            return 0.0
        return 0.0

    async def create_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        time_in_force: str = "GoodTillCancel",
    ) -> Dict[str, Any]:
        """
        Создание ордера на Gate.io futures/usdt.
        Для LONG size > 0, для SHORT size < 0.
        ВНИМАНИЕ: Размер size на Gate.io futures (USDT) указывается в контрактах (целое число).
        """
        contract = self._to_gate_symbol(symbol)
        
        # Приводим qty к int, так как Gate Futures оперирует количеством контрактов
        size_int = int(qty)
        if size_int == 0:
             logger.warning("Gate.io: попытка создания ордера с qty < 1 (qty=%s), округлено до 0", qty)
             return {"success": False, "reason": "qty_too_small"}

        size = size_int if side == "Buy" else -size_int

        # Gate.io: tif "gtc"|"ioc"|"poc"; маппим GoodTillCancel -> "gtc"
        tif = "gtc" if time_in_force == "GoodTillCancel" else "gtc"

        body: Dict[str, Any] = {
            "contract": contract,
            "size": size,
            "tif": tif,
        }

        if order_type == "Limit" and price is not None:
            body["price"] = str(price)
        else:
            # Для маркет ордера на Gate.io достаточно size, цену можно поставить 0
            body["price"] = "0"

        logger.info("Отправка ордера на Gate.io: %s", body)
        data = await self._request("POST", "/futures/usdt/orders", body=body, private=True)

        # Успешный ответ Gate.io обычно содержит поле "id" или не содержит поля "label" с ошибкой.
        if not isinstance(data, dict) or "id" not in data:
            logger.error("Ошибка создания ордера на Gate.io, полный ответ: %s", data)
            return {"success": False, "raw": data}

        return {"success": True, "raw": data}

    async def _get_position(self, contract: str) -> float:
        """
        Возвращает текущий размер позиции (с знаком: +Long, -Short).
        """
        path = f"/futures/usdt/positions/{contract}"
        data = await self._request("GET", path, private=True)
        if not isinstance(data, dict) or "size" not in data:
            return 0.0
        try:
            return float(data.get("size", 0))
        except (ValueError, TypeError):
            return 0.0

    async def set_tp_sl(
        self,
        symbol: str,
        position_side: str,
        tp: Optional[float],
        sl: Optional[float],
    ) -> Dict[str, Any]:
        """
        Установка TP/SL на Gate.io через дополнительные ордера:
        - TP: лимитный reduce-only ордер в сторону закрытия позиции.
        - SL: стоп-ордер (здесь реализован как лимитный reduce-only для простоты).

        ВНИМАНИЕ: Семантика TP/SL полностью зависит от настроек фьючерсной позиции на Gate.io.
        Здесь реализован "best-effort" подход с подробным логированием.
        """
        contract = self._to_gate_symbol(symbol)

        if tp is None and sl is None:
            logger.info("Gate.io TP/SL: tp и sl не заданы для %s, пропускаю установку", symbol)
            return {"success": False, "reason": "no_tp_sl"}

        # Получаем текущий размер позиции для корректного выставления reduce-only ордеров
        current_size = await self._get_position(contract)
        if current_size == 0:
            logger.warning("Gate.io TP/SL: нет открытой позиции по %s (size=0), пропускаю установку", contract)
            return {"success": False, "reason": "no_position"}

        # Для закрытия позиции нам нужен обратный размер
        # Если Long (size > 0) -> продаем (size < 0)
        # Если Short (size < 0) -> покупаем (size > 0)
        close_size = -current_size

        orders: List[Dict[str, Any]] = []
        
        # TP — лимитный reduce-only ордер
        if tp is not None:
            tp_body = {
                "contract": contract,
                "size": int(close_size) if close_size.is_integer() else close_size,
                "price": str(tp),
                "tif": "gtc",
                "reduce_only": True,
            }
            logger.info("Gate.io TP: создаю лимитный reduce-only ордер: %s", tp_body)
            tp_resp = await self._request("POST", "/futures/usdt/orders", body=tp_body, private=True)
            if isinstance(tp_resp, dict) and "id" in tp_resp:
                orders.append({"type": "tp", "success": True, "raw": tp_resp})
            else:
                logger.error("Gate.io TP: ошибка создания TP ордера, ответ: %s", tp_resp)
                orders.append({"type": "tp", "success": False, "raw": tp_resp})

        # SL — лимитный reduce-only ордер (best-effort)
        if sl is not None:
            sl_body = {
                "contract": contract,
                "size": int(close_size) if close_size.is_integer() else close_size,
                "price": str(sl),
                "tif": "gtc",
                "reduce_only": True,
            }
            logger.info("Gate.io SL: создаю лимитный reduce-only ордер: %s", sl_body)
            sl_resp = await self._request("POST", "/futures/usdt/orders", body=sl_body, private=True)
            if isinstance(sl_resp, dict) and "id" in sl_resp:
                orders.append({"type": "sl", "success": True, "raw": sl_resp})
            else:
                logger.error("Gate.io SL: ошибка создания SL ордера, ответ: %s", sl_resp)
                orders.append({"type": "sl", "success": False, "raw": sl_resp})

        all_ok = all(o.get("success") for o in orders if "success" in o)
        return {"success": all_ok, "orders": orders}


