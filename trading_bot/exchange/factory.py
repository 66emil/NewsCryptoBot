import logging
from functools import lru_cache

from trading_bot.config import get_config
from trading_bot.exchange.base import ExchangeAdapter
from trading_bot.exchange.binance_adapter import BinanceExchange
from trading_bot.exchange.bybit_adapter import BybitExchange
from trading_bot.exchange.gate_adapter import GateExchange

logger = logging.getLogger(__name__)


def _create_exchange(name: str) -> ExchangeAdapter | None:
    """
    Фабричный метод для создания адаптера по имени биржи.
    Сейчас реализована только Bybit.
    """
    n = name.lower()
    if n in ("bybit", "bybit-linear", "bybit-perp"):
        logger.info("Инициализирован адаптер биржи: Bybit")
        return BybitExchange()
    if n in ("gateio", "gate", "gate-io"):
        logger.info("Инициализирован адаптер биржи: Gate.io")
        return GateExchange()
    if n in ("binance", "binance-futures"):
        logger.info("Инициализирован адаптер биржи: Binance")
        return BinanceExchange()
    # Здесь позже можно добавить Binance и т.д.
    logger.info("Адаптер биржи %s пока не реализован", name)
    return None


@lru_cache(maxsize=None)
def get_exchange_by_name(name: str) -> ExchangeAdapter | None:
    """
    Кэшированный доступ к адаптеру по имени.
    """
    return _create_exchange(name)


@lru_cache(maxsize=1)
def get_exchange() -> ExchangeAdapter:
    """
    Возвращает singleton‑экземпляр адаптера биржи, указанной в конфиге EXCHANGE_NAME.
    Используется как "дефолтная" биржа, если роутер не задействован.
    """
    config = get_config()
    name = getattr(config, "EXCHANGE_NAME", "bybit").lower()
    exchange = get_exchange_by_name(name)
    if exchange is not None:
        return exchange

    logger.warning("Неизвестное значение EXCHANGE_NAME=%s, по умолчанию используется Bybit", name)
    # Безопасный дефолт
    return BybitExchange()


