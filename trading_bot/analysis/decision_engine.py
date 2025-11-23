import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def make_decision(news_data: Dict[str, Any], technical_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Комбинирует news_score и technical_score, рассчитывает сигнал и TP/SL.
    """
    news_score = float(news_data.get("news_score", 0.0))
    technical_score = float(technical_data.get("technical_score", 0.0))
    indicators = technical_data.get("indicators", {}) or {}
    atr = indicators.get("atr") or 0.0

    final_score = 0.6 * news_score + 0.4 * technical_score

    if final_score > 0.3:
        signal = "LONG"
    elif final_score < -0.3:
        signal = "SHORT"
    else:
        signal = "HOLD"

    # Расчет TP/SL на основе ATR(14) для адаптивного скальпинга
    # Увеличенные интервалы (более агрессивно/свободно):
    # SL = 1.2 * ATR, TP = 2.4 * ATR
    if atr:
        tp = 2.4 * float(atr)
        sl = 1.2 * float(atr)
    else:
        tp = 0.0
        sl = 0.0

    result = {
        "signal": signal,
        "final_score": final_score,
        "tp": tp,
        "sl": sl,
    }
    logger.info("Результат decision engine: %s", result)
    return result


