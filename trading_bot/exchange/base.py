from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional


class ExchangeAdapter(abc.ABC):
    """
    Базовый интерфейс для всех бирж.
    Реализации должны быть полностью асинхронными.
    """

    name: str

    # ---- Поддержка тикера ----

    @abc.abstractmethod
    async def has_market(self, symbol: str) -> bool:
        """
        Возвращает True, если указанный тикер поддерживается биржей (есть торговый инструмент).
        """
        ...

    # ---- Маркет‑данные ----

    @abc.abstractmethod
    async def get_klines(self, symbol: str, timeframe: str, limit: int = 200) -> List[Dict[str, Any]]:
        ...

    @abc.abstractmethod
    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    async def get_open_interest(self, symbol: str, timeframe: str, limit: int = 50) -> List[Dict[str, Any]]:
        ...

    # ---- Параметры инструмента / баланс ----

    @abc.abstractmethod
    async def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    async def get_wallet_balance(self, asset: str = "USDT") -> float:
        ...

    # ---- Торговые операции ----

    @abc.abstractmethod
    async def create_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        time_in_force: str = "GoodTillCancel",
    ) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    async def set_tp_sl(
        self,
        symbol: str,
        position_side: str,
        tp: Optional[float],
        sl: Optional[float],
    ) -> Dict[str, Any]:
        ...

    @abc.abstractmethod
    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Возвращает список открытых позиций.
        Если указан symbol, возвращает позиции только по этому тикеру.
        """
        ...

    @abc.abstractmethod
    async def close_position(self, symbol: str, position_side: str = "") -> Dict[str, Any]:
        """
        Закрывает позицию по рынку.
        """
        ...


