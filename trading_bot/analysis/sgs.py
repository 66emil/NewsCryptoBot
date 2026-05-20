"""
Signal Generation System (SGS).
S_final = 0.25·S_news + 0.40·S_tech + 0.35·S_ts
LONG  when S_final > 0.55
SHORT when S_final < 0.45
NEUTRAL otherwise
TP = 3·ATR, SL = 1·ATR
"""
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

_LONG_THRESHOLD = 0.55
_SHORT_THRESHOLD = 0.45


async def aggregate(
    s_news: float,
    s_tech: float,
    s_ts: float,
    atr: float,
) -> Dict[str, Any]:
    s_final = max(0.0, min(1.0, 0.25 * s_news + 0.40 * s_tech + 0.35 * s_ts))

    if s_final > _LONG_THRESHOLD:
        signal = "LONG"
    elif s_final < _SHORT_THRESHOLD:
        signal = "SHORT"
    else:
        signal = "NEUTRAL"

    tp = 3.0 * atr
    sl = 1.0 * atr

    result: Dict[str, Any] = {
        "signal": signal,
        "s_final": s_final,
        "s_news": s_news,
        "s_tech": s_tech,
        "s_ts": s_ts,
        "tp": tp,
        "sl": sl,
    }

    logger.info(
        "SGS: S_news=%.3f S_tech=%.3f S_ts=%.3f → S_final=%.3f signal=%s tp=%.5f sl=%.5f",
        s_news, s_tech, s_ts, s_final, signal, tp, sl,
    )
    return result
