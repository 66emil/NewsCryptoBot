"""
Technical Analysis Module (TAM).
Computes 10 indicators: EMA9/21/50, MACD, RSI, Bollinger, ATR, ADX, Stochastic, CCI.
S_tech ∈ [0, 1]; weights adapt between trending (ADX>25) and ranging (ADX<20) regimes.
"""
import logging
from typing import Any, Dict, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_MIN_CANDLES = 60

# --------------------------------------------------------------------------
# Adaptive weight tables (must each sum to 1.0)
# --------------------------------------------------------------------------

_TRENDING_WEIGHTS: Dict[str, float] = {
    "ema9_21":    0.25,
    "ema21_50":   0.20,
    "macd":       0.25,
    "rsi":        0.10,
    "bollinger":  0.05,
    "stochastic": 0.07,
    "cci":        0.08,
}

_RANGING_WEIGHTS: Dict[str, float] = {
    "ema9_21":    0.05,
    "ema21_50":   0.05,
    "macd":       0.10,
    "rsi":        0.20,
    "bollinger":  0.25,
    "stochastic": 0.20,
    "cci":        0.15,
}

# --------------------------------------------------------------------------
# Low-level indicator functions (module-level so TSM can import them)
# --------------------------------------------------------------------------

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty(len(values))
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1.0 - k)
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    out = np.full(n, 50.0)
    if n < period + 1:
        return out

    diffs = np.diff(closes)
    gains = np.maximum(diffs, 0.0)
    losses = np.maximum(-diffs, 0.0)

    avg_g = float(np.mean(gains[:period]))
    avg_l = float(np.mean(losses[:period]))

    def _val(ag: float, al: float) -> float:
        if al == 0.0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + ag / al)

    out[period] = _val(avg_g, avg_l)
    for i in range(period, n - 1):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        out[i + 1] = _val(avg_g, avg_l)

    return out


def _macd(
    closes: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    ema_f = _ema(closes, fast)
    ema_s = _ema(closes, slow)
    macd_line = ema_f - ema_s
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger(
    closes: np.ndarray,
    period: int = 20,
    nstd: float = 2.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(closes)
    upper = np.zeros(n)
    mid = np.zeros(n)
    lower = np.zeros(n)
    for i in range(period - 1, n):
        w = closes[i - period + 1: i + 1]
        m = float(np.mean(w))
        s = float(np.std(w, ddof=0))
        mid[i] = m
        upper[i] = m + nstd * s
        lower[i] = m - nstd * s
    return upper, mid, lower


def _atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    # Wilder smoothing
    atr_arr = np.empty(n)
    atr_arr[0] = tr[0]
    for i in range(1, n):
        atr_arr[i] = atr_arr[i - 1] * (period - 1) / period + tr[i] / period
    return atr_arr


def _adx(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(closes)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)

    for i in range(1, n):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        plus_dm[i] = h_diff if (h_diff > l_diff and h_diff > 0) else 0.0
        minus_dm[i] = l_diff if (l_diff > h_diff and l_diff > 0) else 0.0
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    def _wilder(arr: np.ndarray, p: int) -> np.ndarray:
        out = np.zeros(n)
        if n <= p:
            return out
        out[p] = float(np.sum(arr[1: p + 1]))
        for i in range(p + 1, n):
            out[i] = out[i - 1] - out[i - 1] / p + arr[i]
        return out

    tr_s = _wilder(tr, period)
    plus_s = _wilder(plus_dm, period)
    minus_s = _wilder(minus_dm, period)

    plus_di = np.divide(100.0 * plus_s, tr_s, out=np.zeros_like(tr_s), where=tr_s > 0)
    minus_di = np.divide(100.0 * minus_s, tr_s, out=np.zeros_like(tr_s), where=tr_s > 0)
    di_sum = plus_di + minus_di
    dx = np.divide(
        100.0 * np.abs(plus_di - minus_di),
        di_sum,
        out=np.zeros_like(di_sum),
        where=di_sum > 0,
    )

    adx_arr = np.zeros(n)
    start = 2 * period
    if n > start:
        adx_arr[start] = float(np.mean(dx[period: start + 1]))
        for i in range(start + 1, n):
            adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx[i]) / period

    return adx_arr, plus_di, minus_di


def _stochastic(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    k_period: int = 14,
    d_period: int = 3,
) -> Tuple[np.ndarray, np.ndarray]:
    n = len(closes)
    k_arr = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        lo = float(np.min(lows[i - k_period + 1: i + 1]))
        hi = float(np.max(highs[i - k_period + 1: i + 1]))
        rng = hi - lo
        k_arr[i] = 100.0 * (closes[i] - lo) / rng if rng > 0 else 50.0

    d_arr = np.zeros(n)
    for i in range(d_period - 1, n):
        d_arr[i] = float(np.mean(k_arr[i - d_period + 1: i + 1]))

    return k_arr, d_arr


def _cci(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 20,
) -> np.ndarray:
    n = len(closes)
    tp = (highs + lows + closes) / 3.0
    cci_arr = np.zeros(n)
    for i in range(period - 1, n):
        w = tp[i - period + 1: i + 1]
        mean_tp = float(np.mean(w))
        mean_dev = float(np.mean(np.abs(w - mean_tp)))
        if mean_dev > 0:
            cci_arr[i] = (tp[i] - mean_tp) / (0.015 * mean_dev)
    return cci_arr


# --------------------------------------------------------------------------
# Scoring helpers: each returns a float in [-1, 1]
# --------------------------------------------------------------------------

def _tanh_score(x: float, scale: float = 1.0) -> float:
    import math
    return math.tanh(x * scale)


def _score_ema_cross(fast: float, slow: float, close: float) -> float:
    if close <= 0:
        return 0.0
    return _tanh_score((fast - slow) / close, 100.0)


def _score_macd(histogram: float, atr: float) -> float:
    return _tanh_score(histogram / (atr + 1e-10))


def _score_rsi(rsi_val: float) -> float:
    return float(np.clip((50.0 - rsi_val) / 50.0, -1.0, 1.0))


def _score_bollinger(close: float, lower: float, upper: float) -> float:
    rng = upper - lower
    if rng <= 0:
        return 0.0
    b_pct = (close - lower) / rng
    return float(np.clip(1.0 - 2.0 * b_pct, -1.0, 1.0))


def _score_stochastic(stoch_k: float) -> float:
    return float(np.clip((50.0 - stoch_k) / 50.0, -1.0, 1.0))


def _score_cci(cci_val: float) -> float:
    return float(np.clip(-cci_val / 200.0, -1.0, 1.0))


# --------------------------------------------------------------------------
# TechnicalAnalysisModule
# --------------------------------------------------------------------------

class TechnicalAnalysisModule:
    """
    Computes all 10 indicators and derives S_tech ∈ [0, 1].
    ADX determines market regime; weights blend linearly in transition zone.
    """

    async def analyze(self, klines: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(klines) < _MIN_CANDLES:
            logger.warning(
                "TAM: недостаточно свечей (%d < %d), S_tech = 0.5",
                len(klines),
                _MIN_CANDLES,
            )
            return {"s_tech": 0.5, "indicators": {}, "regime": "unknown", "scores": {}}

        opens = np.array([float(k["open"]) for k in klines])
        highs = np.array([float(k["high"]) for k in klines])
        lows = np.array([float(k["low"]) for k in klines])
        closes = np.array([float(k["close"]) for k in klines])

        # Compute all indicators
        ema9_arr = _ema(closes, 9)
        ema21_arr = _ema(closes, 21)
        ema50_arr = _ema(closes, 50)
        macd_line, signal_line, histogram = _macd(closes)
        rsi_arr = _rsi(closes, 14)
        bb_upper, bb_mid, bb_lower = _bollinger(closes, 20, 2.0)
        atr_arr = _atr(highs, lows, closes, 14)
        adx_arr, plus_di, minus_di = _adx(highs, lows, closes, 14)
        stoch_k_arr, stoch_d_arr = _stochastic(highs, lows, closes, 14, 3)
        cci_arr = _cci(highs, lows, closes, 20)

        # Latest values
        close = float(closes[-1])
        ema9_v = float(ema9_arr[-1])
        ema21_v = float(ema21_arr[-1])
        ema50_v = float(ema50_arr[-1])
        hist_v = float(histogram[-1])
        rsi_v = float(rsi_arr[-1])
        bb_up_v = float(bb_upper[-1])
        bb_lo_v = float(bb_lower[-1])
        atr_v = float(atr_arr[-1])
        adx_v = float(adx_arr[-1])
        stoch_k_v = float(stoch_k_arr[-1])
        cci_v = float(cci_arr[-1])

        # Per-indicator scores in [-1, 1]
        raw_scores: Dict[str, float] = {
            "ema9_21":    _score_ema_cross(ema9_v, ema21_v, close),
            "ema21_50":   _score_ema_cross(ema21_v, ema50_v, close),
            "macd":       _score_macd(hist_v, atr_v),
            "rsi":        _score_rsi(rsi_v),
            "bollinger":  _score_bollinger(close, bb_lo_v, bb_up_v),
            "stochastic": _score_stochastic(stoch_k_v),
            "cci":        _score_cci(cci_v),
        }

        # Regime detection and weight blending
        if adx_v > 25.0:
            weights = _TRENDING_WEIGHTS
            regime = "trending"
        elif adx_v < 20.0:
            weights = _RANGING_WEIGHTS
            regime = "ranging"
        else:
            t = (adx_v - 20.0) / 5.0  # 0→ranging, 1→trending
            weights = {
                k: t * _TRENDING_WEIGHTS[k] + (1.0 - t) * _RANGING_WEIGHTS[k]
                for k in _TRENDING_WEIGHTS
            }
            regime = "transition"

        weighted_score = sum(weights[k] * raw_scores[k] for k in weights)
        s_tech = float(np.clip((weighted_score + 1.0) / 2.0, 0.0, 1.0))

        indicators = {
            "ema9": ema9_v,
            "ema21": ema21_v,
            "ema50": ema50_v,
            "macd": float(macd_line[-1]),
            "macd_signal": float(signal_line[-1]),
            "macd_hist": hist_v,
            "rsi": rsi_v,
            "bb_upper": bb_up_v,
            "bb_lower": bb_lo_v,
            "atr": atr_v,
            "adx": adx_v,
            "plus_di": float(plus_di[-1]),
            "minus_di": float(minus_di[-1]),
            "stoch_k": stoch_k_v,
            "stoch_d": float(stoch_d_arr[-1]),
            "cci": cci_v,
            "last_price": close,
        }

        logger.info(
            "TAM: S_tech=%.3f regime=%s ADX=%.1f RSI=%.1f ATR=%.4f",
            s_tech, regime, adx_v, rsi_v, atr_v,
        )
        return {
            "s_tech": s_tech,
            "indicators": indicators,
            "regime": regime,
            "scores": raw_scores,
        }
