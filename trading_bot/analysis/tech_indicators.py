import logging
from typing import Any, Dict, List

from trading_bot.config import get_config
from trading_bot.exchange.base import ExchangeAdapter
from trading_bot.exchange.factory import get_exchange

logger = logging.getLogger(__name__)


def _ema(values: List[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema_val = values[0]
    for price in values[1:]:
        ema_val = price * k + ema_val * (1 - k)
    return ema_val


def _rsi(values: List[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff > 0:
            gains.append(diff)
        else:
            losses.append(-diff)
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val


def _true_range(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    trs: List[float] = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return trs


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float | None:
    if len(highs) <= period or len(lows) <= period or len(closes) <= period:
        return None
    trs = _true_range(highs, lows, closes)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


async def calculate_indicators(symbol: str, exchange: ExchangeAdapter | None = None) -> Dict[str, Any]:
    """
    Рассчитывает RSI(14), EMA(25/50), ATR, изменение объема (1h) и тренд OI.
    """
    config = get_config()
    # Используем 1m таймфрейм для скальпинга, как рекомендовано для ATR стратегии
    timeframe = "1"
    max_candles = config.MAX_CANDLES

    logger.info("Запрос свечей и данных OI для %s", symbol)
    if exchange is None:
        exchange = get_exchange()
    klines = await exchange.get_klines(symbol, timeframe, limit=max_candles)

    if not klines:
        logger.warning("Не удалось получить свечи для %s, пробую взять только last_price из тикера", symbol)
        ticker = await exchange.get_ticker(symbol)
        last_price_raw = ticker.get("lastPrice")
        if last_price_raw is None:
            logger.warning("Не удалось получить last_price из тикера для %s, технический анализ невозможен", symbol)
            return {
                "technical_score": 0.0,
                "indicators": {},
            }
        last_price = float(last_price_raw)
        indicators = {
            "rsi": None,
            "ema25": None,
            "ema50": None,
            "atr": None,
            "volume_change": 0.0,
            "volume_growing": False,
            "oi_trend": 0.0,
            "oi_trend_up": False,
            "last_price": last_price,
        }
        logger.info("Технические индикаторы (без свечей) для %s: %s", symbol, indicators)
        return {
            "technical_score": 0.0,
            "indicators": indicators,
        }

    closes: List[float] = [float(k["close"]) for k in klines]
    highs: List[float] = [float(k["high"]) for k in klines]
    lows: List[float] = [float(k["low"]) for k in klines]
    volumes: List[float] = [float(k.get("volume", 0.0)) for k in klines]

    # RSI, EMA, ATR
    rsi_val = _rsi(closes, period=14)
    ema25 = _ema(closes, period=25)
    ema50 = _ema(closes, period=50)
    atr_val = _atr(highs, lows, closes, period=14)

    # Изменение объема за последний "час" (последняя свеча к предыдущей)
    volume_change = 0.0
    volume_growing = False
    if len(volumes) >= 2:
        prev, last = volumes[-2], volumes[-1]
        if prev > 0:
            volume_change = (last - prev) / prev
        volume_growing = last > prev

    # Тренд Open Interest
    oi_series = await exchange.get_open_interest(symbol, timeframe=timeframe, limit=50)
    oi_trend_up = False
    oi_trend = 0.0
    if oi_series:
        oi_values = [float(x["openInterest"]) for x in oi_series if "openInterest" in x]
        if len(oi_values) >= 2:
            oi_trend = oi_values[-1] - oi_values[0]
            oi_trend_up = oi_trend > 0

    # Получаем текущую цену
    ticker = await exchange.get_ticker(symbol)
    last_price = float(ticker.get("lastPrice", closes[-1]))

    technical_score = 0.0

    # RSI правила
    if rsi_val is not None:
        if rsi_val < 30:
            technical_score += 0.3
        elif rsi_val > 70:
            technical_score -= 0.3

    # Объемы
    if volume_growing:
        technical_score += 0.3

    # EMA и цена
    if ema25 is not None and last_price > ema25:
        technical_score += 0.2
    if ema50 is not None and last_price < ema50:
        technical_score -= 0.2

    # OI тренд
    if oi_trend_up:
        technical_score += 0.4

    indicators = {
        "rsi": rsi_val,
        "ema25": ema25,
        "ema50": ema50,
        "atr": atr_val,
        "volume_change": volume_change,
        "volume_growing": volume_growing,
        "oi_trend": oi_trend,
        "oi_trend_up": oi_trend_up,
        "last_price": last_price,
    }

    logger.info("Технические индикаторы для %s: %s", symbol, indicators)

    return {
        "technical_score": technical_score,
        "indicators": indicators,
    }


