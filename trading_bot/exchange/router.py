import logging
from typing import List, Optional

from trading_bot.config import get_config
from trading_bot.exchange.base import ExchangeAdapter
from trading_bot.exchange.factory import get_exchange_by_name

logger = logging.getLogger(__name__)


def _parse_priority(raw: str) -> List[str]:
    return [name.strip().lower() for name in raw.split(",") if name.strip()]


async def route_symbol(symbol: str) -> Optional[ExchangeAdapter]:
    """
    Маршрутизатор бирж:
    - проходит по списку бирж в порядке приоритета
    - на каждой проверяет наличие тикера
    - возвращает первую подходящую биржу или None.
    """
    config = get_config()
    raw_priority = getattr(config, "EXCHANGE_PRIORITY", "") or getattr(config, "EXCHANGE_NAME", "bybit")
    priority = _parse_priority(raw_priority)
    if not priority:
        priority = ["bybit"]

    logger.info("Роутер бирж: начинаю поиск тикера %s по биржам %s", symbol, priority)

    for name in priority:
        exchange = get_exchange_by_name(name)
        if exchange is None:
            logger.info("Роутер: адаптер для биржи %s пока не реализован, пропускаю", name)
            continue

        logger.info("Роутер: проверяю наличие тикера %s на бирже %s", symbol, exchange.name)
        try:
            has = await exchange.has_market(symbol)
        except Exception as e:  # noqa: BLE001
            logger.error("Роутер: ошибка проверки тикера %s на бирже %s: %s", symbol, exchange.name, e)
            continue

        if has:
            logger.info("Роутер: тикер %s найден на бирже %s — биржа выбрана для сделки", symbol, exchange.name)
            return exchange

        logger.info("Роутер: тикер %s не найден на бирже %s, перехожу к следующей", symbol, exchange.name)

    logger.warning(
        "Роутер: тикер %s не найден ни на одной из бирж %s — торговый сигнал будет пропущен",
        symbol,
        priority,
    )
    return None


