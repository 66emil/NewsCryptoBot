import logging
import math
from typing import Any, Dict, Optional

from trading_bot.exchange.base import ExchangeAdapter
from trading_bot.exchange.factory import get_exchange

logger = logging.getLogger(__name__)


async def create_long(
    symbol: str,
    qty: float,
    price: Optional[float] = None,
    exchange: ExchangeAdapter | None = None,
) -> Dict[str, Any]:
    """
    Создание LONG ордера (по умолчанию market, если price не указан).
    Использует универсальный адаптер биржи.
    """
    side = "Buy"
    order_type = "Limit" if price is not None else "Market"
    exch = exchange or get_exchange()
    return await exch.create_order(symbol=symbol, side=side, qty=qty, order_type=order_type, price=price)


async def create_short(
    symbol: str,
    qty: float,
    price: Optional[float] = None,
    exchange: ExchangeAdapter | None = None,
) -> Dict[str, Any]:
    """
    Создание SHORT ордера.
    """
    side = "Sell"
    order_type = "Limit" if price is not None else "Market"
    exch = exchange or get_exchange()
    return await exch.create_order(symbol=symbol, side=side, qty=qty, order_type=order_type, price=price)


async def set_tp_sl(
    symbol: str,
    position_side: str,
    tp: Optional[float],
    sl: Optional[float],
    exchange: ExchangeAdapter | None = None,
) -> Dict[str, Any]:
    """
    Установка TP/SL для открытой позиции через универсальный адаптер.
    """
    exch = exchange or get_exchange()
    return await exch.set_tp_sl(symbol=symbol, position_side=position_side, tp=tp, sl=sl)


def _round_down(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step


def _round_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return value
    return round(value / tick) * tick


async def build_order_plan(
    symbol: str,
    signal: str,
    last_price: float,
    tp_dist: float,
    sl_dist: float,
    risk_pct: float = 0.01,
    exchange: ExchangeAdapter | None = None,
) -> Dict[str, Any]:
    """
    Строит план сделки:
    - рассчитывает размер позиции как risk_pct от депозита по USDT
    - нормализует qty по шагу количества
    - рассчитывает цены TP/SL и нормализует их по tickSize.
    """
    exch = exchange or get_exchange()
    info = await exch.get_instrument_info(symbol)
    if not info:
        logger.error("Не удалось получить instrument info для %s, сделка отменена", symbol)
        return {"success": False, "reason": "instrument_info_missing"}

    lot = info.get("lotSizeFilter", {}) or {}
    price_filter = info.get("priceFilter", {}) or {}

    try:
        qty_step = float(lot.get("qtyStep", "0.001"))
        min_qty = float(lot.get("minOrderQty", qty_step))
    except (TypeError, ValueError):
        qty_step = 0.001
        min_qty = qty_step

    try:
        tick_size = float(price_filter.get("tickSize", "0.01"))
    except (TypeError, ValueError):
        tick_size = 0.01

    # risk_pct от депозита в USDT
    balance = await exch.get_wallet_balance("USDT")
    if balance <= 0:
        logger.error("Баланс USDT равен %.4f или не получен (exch=%s), сделка отменена", balance, exch.name)
        return {"success": False, "reason": "no_balance"}

    notional = balance * risk_pct
    
    # Проверка на минимальную стоимость ордера (обычно 5-6 USDT на биржах)
    MIN_NOTIONAL = 6.0  # Берем с запасом
    if notional < MIN_NOTIONAL:
        logger.warning(
            "Рассчитанный размер позиции (%.2f USDT) меньше минимума (%.2f USDT). "
            "Пытаюсь увеличить до минимального...",
            notional,
            MIN_NOTIONAL,
        )
        # Если баланс позволяет, увеличиваем до минимума
        if balance >= MIN_NOTIONAL:
            notional = MIN_NOTIONAL
        else:
             logger.error(
                 "Баланс (%.2f USDT) слишком мал даже для минимального ордера (%.2f USDT). Сделка отменена.",
                 balance,
                 MIN_NOTIONAL,
             )
             return {"success": False, "reason": "balance_too_low_for_min_order"}

    raw_qty = notional / last_price
    qty = _round_down(raw_qty, qty_step)
    if qty < min_qty:
        logger.error(
            "Рассчитанное количество %s меньше минимального %s для %s, сделка отменена",
            qty,
            min_qty,
            symbol,
        )
        return {"success": False, "reason": "qty_below_min"}

    # Исходные TP/SL цены по сигналу
    if signal == "LONG":
        tp_price = last_price + tp_dist
        sl_price = last_price - sl_dist
        side = "Buy"
    elif signal == "SHORT":
        tp_price = last_price - tp_dist
        sl_price = last_price + sl_dist
        side = "Sell"
    else:
        return {"success": False, "reason": "no_trade"}

    # Нормализация под шаг цены
    tp_price = _round_to_tick(tp_price, tick_size)
    sl_price = _round_to_tick(sl_price, tick_size)

    logger.info(
        "План сделки для %s: side=%s, balance=%.4f USDT, risk_pct=%.2f, "
        "notional=%.4f, qty=%.6f, tp=%.6f, sl=%.6f (tick=%.6f, step=%.6f)",
        symbol,
        side,
        balance,
        risk_pct,
        notional,
        qty,
        tp_price,
        sl_price,
        tick_size,
        qty_step,
    )

    return {
        "success": True,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "tp_price": tp_price,
        "sl_price": sl_price,
    }


