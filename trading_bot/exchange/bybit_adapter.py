import logging
from typing import Any, Dict, List, Optional

from trading_bot.bybit import api as bybit_api
from trading_bot.exchange.base import ExchangeAdapter

logger = logging.getLogger(__name__)


class BybitExchange(ExchangeAdapter):
    """
    Адаптер Bybit, реализующий универсальный интерфейс биржи.
    Использует существующий модуль `trading_bot.bybit.api`.
    """

    name = "bybit"

    async def has_market(self, symbol: str) -> bool:
        """
        Проверяет наличие инструмента на Bybit через instruments-info.
        """
        info = await bybit_api.get_instrument_info(symbol)
        exists = bool(info)
        logger.info("Проверка тикера %s на Bybit: %s", symbol, "найден" if exists else "не найден")
        return exists

    # ---- Маркет‑данные ----

    async def get_klines(self, symbol: str, timeframe: str, limit: int = 200) -> List[Dict[str, Any]]:
        return await bybit_api.get_klines(symbol, timeframe, limit=limit)

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        return await bybit_api.get_ticker(symbol)

    async def get_open_interest(self, symbol: str, timeframe: str, limit: int = 50) -> List[Dict[str, Any]]:
        return await bybit_api.get_open_interest(symbol, timeframe=timeframe, limit=limit)

    # ---- Параметры инструмента / баланс ----

    async def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        return await bybit_api.get_instrument_info(symbol)

    async def get_wallet_balance(self, asset: str = "USDT") -> float:
        return await bybit_api.get_wallet_balance(coin=asset)

    # ---- Торговые операции ----

    async def create_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        time_in_force: str = "GoodTillCancel",
    ) -> Dict[str, Any]:
        # Форматируем qty, чтобы избежать scientific notation (1e-5) или лишних знаков (120.10000000001)
        # Bybit требует строку, но без лишней "хвостатой" точности.
        # Самый надежный способ: "{:.Xf}" где X - достаточное кол-во знаков, но лучше просто format(qty, 'f') и убрать лишние нули,
        # однако, мы уже нормализовали qty в build_order_plan.
        # Проблема в том, что str(float) в питоне может давать "120.10000000000001".
        # Лучше обрезать до разумных 6-8 знаков.
        qty_str = "{:.8f}".format(qty).rstrip("0").rstrip(".")
        
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": qty_str,
            "timeInForce": time_in_force,
        }
        if price is not None:
            params["price"] = str(price)

        # Сохраняем существующий стиль логирования
        logger.info("Отправка ордера на Bybit: %s", params)
        data = await bybit_api._request("POST", "/v5/order/create", params=params, private=True)
        if not data or data.get("retCode") != 0:
            code = None if not data else data.get("retCode")
            msg = None if not data else data.get("retMsg")
            logger.error(
                "Ошибка создания ордера (код=%s, причина=%s), полный ответ: %s",
                code,
                msg,
                data,
            )
            return {"success": False, "code": code, "message": msg, "raw": data}
        return {"success": True, "raw": data}

    async def set_tp_sl(
        self,
        symbol: str,
        position_side: str,
        tp: Optional[float],
        sl: Optional[float],
    ) -> Dict[str, Any]:
        # positionIdx: 0 - One-Way Mode, 1 - Hedge Mode Buy Side, 2 - Hedge Mode Sell Side
        # Многие аккаунты по умолчанию One-Way (idx=0), но некоторые Hedge.
        # Ошибка "TakeProfit set for Buy position should be higher than base_price" говорит о том, что
        # мы пытаемся поставить TP для BUY позиции (потому что idx=0 часто воспринимается как Buy/OneWay),
        # но цена TP ниже текущей (как для Short).
        
        # Если мы шортим (position_side="Sell"), TP должен быть ниже входа.
        # Если мы лонгуем (position_side="Buy"), TP должен быть выше входа.
        
        # Попробуем определить корректный positionIdx.
        # Для One-Way Mode это всегда 0. Но TP/SL в OneWay Mode привязываются ко всей позиции.
        # Если у нас One-Way Mode и мы открыли Short, то позиция будет Short, и TP должен быть ниже цены.
        # Если Bybit ругается, значит он думает, что мы ставим TP для Long (или не понимает направление).
        
        # Самый простой фикс для One-Way Mode: не указывать positionIdx или явно ставить 0,
        # НО важно понимать контекст.
        
        # В Hedge Mode: 1=Buy, 2=Sell.
        # Попробуем эвристику:
        # Если position_side == "Sell", то idx=2 (для Hedge) или 0 (для OneWay).
        # Но если мы отправим 0, а у нас Short, Bybit должен понять по цене TP (ниже текущей), что это для Short?
        # Нет, в OneWay Mode TP/SL привязывается к позиции.
        
        # Ошибка "TakeProfit set for Buy position..." намекает, что Bybit считает позицию BUY или idx=0 трактует как BUY сторону.
        
        idx = 0
        # Если пользователь использует Hedge Mode, нужно ставить 1 или 2.
        # Поскольку мы не знаем режим, попробуем стандартное решение:
        # Если ошибка, попробуем другие индексы? Нет, это сложно.
        # Предположим, что пользователь в One-Way Mode (стандарт для новых аккаунтов Unified).
        
        # В One-Way Mode (idx=0) TP/SL работает для всей позиции.
        # Если позиция SHORT, то TP < Entry. Если позиция LONG, то TP > Entry.
        # Ошибка говорит: "TP set for Buy position...". Значит Bybit видит позицию как Buy? Или idx=0 по дефолту Buy?
        
        # Попробуем не передавать positionIdx вообще, если это One-Way? Нет, он обязателен для некоторых эндпоинтов.
        
        # Попробуем передать правильный idx для Hedge Mode на всякий случай, если вдруг аккаунт в нем.
        if position_side.lower() == "buy":
            idx = 1 # Hedge Buy
        elif position_side.lower() == "sell":
            idx = 2 # Hedge Sell
            
        # НО! Если аккаунт в One-Way, то idx=1/2 вызовет ошибку "position idx invalid".
        # Поэтому вернемся к 0.
        
        # В One-Way Mode (idx=0):
        # Ошибка "TakeProfit ... set for Buy position should be higher than base_price"
        # возникает, когда вы пытаетесь поставить TP ниже текущей цены, но система думает, что вы в Лонге (или у вас нет позиции, и она считается "пустой/лонг"?).
        # Вероятно, позиция еще не успела обновиться/создаться (ордер Market исполняется быстро, но может быть лаг).
        # Или мы шортим, а позиция (idx=0) почему-то считается Buy? В OneWay mode позиция одна, и она имеет сторону.
        
        # Решение:
        # 1. Проверить режим позиции (слишком долго).
        # 2. Использовать универсальный подход: ловить ошибку и пробовать другой idx? Нет.
        # 3. У большинства сейчас One-Way. Скорее всего ошибка из-за того, что мы шортим, а ставим TP в поле, которое Bybit валидирует как для Лонга?
        # Нет, поле одно.
        
        # ВАЖНО: В One-Way Mode TP/SL можно ставить прямо в ордере! (при создании).
        # Это надежнее. Но наш интерфейс разделяет создание и TP/SL.
        
        # Попробуем так: Если мы в One-Way (idx=0), то TP должен соответствовать направлению позиции.
        # Если ошибка 10001, значит направление TP не совпадает с направлением позиции.
        # Значит, позиция действительно Short, а мы ставим TP выше цены? Нет, в логе TP=0.0883, Last=0.0969 (примерно). TP ниже цены. Это верно для Short.
        # Почему Bybit пишет "set for Buy position"? Значит он считает, что позиция Buy (или 0).
        # Может быть, у вас открылась Long позиция вместо Short? Нет, side=Sell.
        
        # Гипотеза: У вас включен Hedge Mode на аккаунте. В Hedge Mode idx=0 недопустим или ведет себя странно.
        # Давайте попробуем так: сначала пробуем idx=0. Если ошибка — пробуем idx=1 (если Buy) или idx=2 (если Sell).
        
        async def _try_set_tp_sl(index: int):
            params = {
                "category": "linear",
                "symbol": symbol,
                "positionIdx": index,
            }
            if tp is not None:
                params["takeProfit"] = str(tp)
            if sl is not None:
                params["stopLoss"] = str(sl)
                
            logger.info("Попытка TP/SL idx=%s: %s", index, params)
            return await bybit_api._request("POST", "/v5/position/trading-stop", params=params, private=True)

        # Сначала пробуем 0 (One-Way)
        data = await _try_set_tp_sl(0)
        
        if data and data.get("retCode") != 0:
            err_msg = data.get("retMsg", "")
            # Если ошибка намекает на неверный режим или направление
            if "position" in err_msg.lower() or "mode" in err_msg.lower() or "invalid" in err_msg.lower():
                # Пробуем Hedge Mode индексы
                hedge_idx = 1 if position_side.lower() == "buy" else 2
                logger.info("Ошибка с idx=0 (%s), пробую Hedge Mode idx=%s", err_msg, hedge_idx)
                data_hedge = await _try_set_tp_sl(hedge_idx)
                
                # Если hedge сработал — возвращаем его
                if data_hedge and data_hedge.get("retCode") == 0:
                    return {"success": True, "raw": data_hedge}
                
                # Если и hedge не сработал, возвращаем ошибку от ПЕРВОГО (или второго, если он информативнее)
                # Обычно ошибка первого (idx=0) более показательна для большинства, если они в One-Way.
            
            code = data.get("retCode")
            msg = data.get("retMsg")
            logger.error("Ошибка установки TP/SL (код=%s, причина=%s)", code, msg)
            return {"success": False, "code": code, "message": msg, "raw": data}

        return {"success": True, "raw": data}

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Возвращает список открытых позиций.
        """
        params = {
            "category": "linear",
            "settleCoin": "USDT",
        }
        if symbol:
            params["symbol"] = symbol
            
        data = await bybit_api._request("GET", "/v5/position/list", params=params, private=True)
        
        if not data or data.get("retCode") != 0:
            logger.error("Ошибка получения позиций: %s", data)
            return []
            
        result = data.get("result", {})
        list_data = result.get("list", [])
        
        # Фильтруем только активные позиции (где size > 0)
        active_positions = [p for p in list_data if float(p.get("size", 0)) > 0]
        return active_positions

    async def close_position(self, symbol: str, position_side: str = "") -> Dict[str, Any]:
        """
        Закрывает позицию полностью (market order на весь объем в противоположную сторону).
        Проще всего использовать API Bybit для закрытия или отправить ордер reduceOnly.
        Но поскольку у нас уже есть метод create_order, можно просто отправить ордер в другую сторону?
        Нет, проще получить размер и закрыть.
        
        Для Bybit проще всего отправить ордер с reduceOnly=True или просто закрыть.
        В v5/order/create есть параметр reduceOnly.
        
        Но самый надежный способ - это определить текущий размер и направление, и отправить обратный ордер.
        
        1. Получаем позицию.
        2. Определяем size и side.
        3. Отправляем Market Order на закрытие.
        """
        positions = await self.get_positions(symbol)
        if not positions:
            return {"success": False, "message": "No open position found"}
            
        # В One-Way mode позиция одна. В Hedge - может быть две.
        # Если position_side не указан, и у нас One-Way - берем первую.
        # Если Hedge - надо знать какую закрывать.
        
        target_pos = None
        for pos in positions:
            # В One-Way mode поле side может быть "Buy" или "Sell" или "None"?
            # API Bybit v5: side: "Buy", "Sell".
            if not position_side:
                target_pos = pos
                break
            
            # Если side указан (Buy/Sell), ищем совпадение.
            # Но для закрытия нам нужно знать, какую именно закрывать.
            # Обычно закрывают ту, которая есть.
            # В Hedge Mode у Buy позиции side="Buy".
            if pos.get("side").lower() == position_side.lower():
                target_pos = pos
                break
                
        if not target_pos:
             return {"success": False, "message": f"Position {position_side} not found for {symbol}"}
             
        size = float(target_pos["size"])
        side = target_pos["side"] # Buy or Sell
        
        close_side = "Sell" if side == "Buy" else "Buy"
        
        # Отправляем ордер
        # reduceOnly=True гарантирует, что мы не откроем новую, если размер не совпадет чуть-чуть.
        
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": close_side,
            "orderType": "Market",
            "qty": str(size),
            "timeInForce": "GoodTillCancel",
            "reduceOnly": True
        }
        
        # Если Hedge Mode, нужно указать positionIdx?
        # idx = target_pos["positionIdx"]
        # params["positionIdx"] = idx
        
        idx = int(target_pos.get("positionIdx", 0))
        params["positionIdx"] = idx
        
        logger.info("Закрытие позиции %s (%s size=%s idx=%s)", symbol, side, size, idx)
        
        data = await bybit_api._request("POST", "/v5/order/create", params=params, private=True)
        if not data or data.get("retCode") != 0:
             return {"success": False, "raw": data}
             
        return {"success": True, "raw": data}


