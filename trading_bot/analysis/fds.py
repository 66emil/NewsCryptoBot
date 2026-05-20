"""
Flat Detection System (FDS).
Detects no-trend / consolidation markets where trading is inadvisable.
All 4 criteria must hold simultaneously:
  C1. ATR% < 1.5%        — low absolute volatility
  C2. ADX  < 20          — no directional trend
  C3. ADF  p > 0.05      — price series has a unit root (random walk, no exploitable trend)
  C4. Range < 2.5·ATR    — tight price band over the lookback window
"""
import logging
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

_ATR_PCT_MAX = 1.5
_ADX_MAX = 20.0
_ADF_P_MIN = 0.05
_RANGE_MULT = 2.5


def detect_flat(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    adx: float,
    atr: float,
    window: int = 20,
) -> Dict[str, Any]:
    """
    Parameters
    ----------
    closes, highs, lows : full history arrays (at least `window` elements)
    adx  : latest ADX value (from TAM)
    atr  : latest ATR value (from TAM)
    window : lookback for ADF and Range criteria

    Returns
    -------
    {is_flat: bool, criteria: dict}
    """
    close_last = float(closes[-1]) if len(closes) > 0 else 1.0

    # C1 — ATR%
    atr_pct = (atr / close_last * 100.0) if close_last > 0 else 0.0
    c1 = atr_pct < _ATR_PCT_MAX

    # C2 — ADX
    c2 = adx < _ADX_MAX

    # C3 — ADF unit-root test on the price window
    adf_pvalue: Optional[float] = None
    c3 = False
    try:
        from statsmodels.tsa.stattools import adfuller  # noqa: PLC0415

        effective_window = min(window, len(closes))
        if effective_window >= 8:  # adfuller needs at least a few observations
            result = adfuller(closes[-effective_window:], autolag="AIC")
            adf_pvalue = float(result[1])
            # p > threshold → fail to reject H0 (unit root) → no exploitable trend
            c3 = adf_pvalue > _ADF_P_MIN
    except ImportError:
        logger.warning("FDS: statsmodels не установлен — критерий ADF пропущен (c3=False)")
    except Exception as exc:
        logger.warning("FDS: ошибка ADF теста: %s", exc)

    # C4 — price range over window vs ATR
    n_recent = min(window, len(highs), len(lows))
    price_range = float(np.max(highs[-n_recent:]) - np.min(lows[-n_recent:]))
    c4 = price_range < _RANGE_MULT * atr

    is_flat = c1 and c2 and c3 and c4

    criteria: Dict[str, Any] = {
        "atr_pct": round(atr_pct, 3),
        "c1_atr_low": c1,
        "adx": round(adx, 2),
        "c2_adx_low": c2,
        "adf_pvalue": round(adf_pvalue, 4) if adf_pvalue is not None else None,
        "c3_adf_unit_root": c3,
        "price_range": round(price_range, 6),
        "range_threshold": round(_RANGE_MULT * atr, 6),
        "c4_range_narrow": c4,
    }

    logger.info(
        "FDS: flat=%s | ATR%%=%.2f(%s) ADX=%.1f(%s) ADF_p=%s(%s) range=%.4f<%s",
        is_flat,
        atr_pct, "✓" if c1 else "✗",
        adx, "✓" if c2 else "✗",
        f"{adf_pvalue:.3f}" if adf_pvalue is not None else "n/a", "✓" if c3 else "✗",
        price_range, "✓" if c4 else "✗",
    )

    return {"is_flat": is_flat, "criteria": criteria}
