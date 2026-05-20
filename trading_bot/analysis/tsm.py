"""
Time Series Module (TSM).
LSTM 2-layer (128 → 64) over a rolling window of N=60 candles.
10 features per timestep: open, high, low, close, volume, RSI, MACD, EMA9, EMA21, ATR.
All features are min-max normalized per window.
S_ts ∈ [0, 1] via Sigmoid output.

If no weights file is found, returns 0.5 (neutral) so the untrained model
does not distort SGS aggregation. Train the model on historical data and
point TSM_MODEL_PATH in .env to the resulting .pth file to activate it.
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

N_FEATURES = 10
_NEUTRAL = 0.5


class TimeSeriesModule:

    def __init__(self, model_path: str = "", window: int = 60, device: str = "cpu") -> None:
        self._model_path = model_path
        self._window = window
        self._device = device
        self._model = None
        self._model_ready = False  # True only when real weights are loaded
        self._attempted = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    async def _ensure_loaded(self) -> None:
        async with self._lock:
            if self._attempted:
                return
            self._attempted = True
            await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        try:
            import torch  # noqa: PLC0415
            from trading_bot.models.lstm_model import LSTMPriceModel  # noqa: PLC0415
        except ImportError as exc:
            logger.error("TSM: PyTorch не установлен (%s) — S_ts = 0.5", exc)
            return

        model = LSTMPriceModel(input_size=N_FEATURES, hidden1=128, hidden2=64)

        if self._model_path and os.path.exists(self._model_path):
            try:
                state = torch.load(self._model_path, map_location=self._device)
                model.load_state_dict(state)
                model.eval()
                self._model = model
                self._model_ready = True
                logger.info("TSM: LSTM веса загружены из %s", self._model_path)
                return
            except Exception as exc:
                logger.error("TSM: ошибка загрузки весов из %s: %s", self._model_path, exc)

        logger.warning(
            "TSM: файл весов не найден (%s). "
            "Установите TSM_MODEL_PATH в .env для активации LSTM. "
            "Пока S_ts = 0.5 (нейтральный скор).",
            self._model_path or "<не задан>",
        )

    # ------------------------------------------------------------------
    # Feature construction
    # ------------------------------------------------------------------

    def _build_features(self, klines: List[Dict[str, Any]]) -> Optional[np.ndarray]:
        if len(klines) < self._window:
            return None

        from trading_bot.analysis.tam import _ema, _rsi, _macd, _atr  # noqa: PLC0415

        recent = klines[-self._window:]
        opens = np.array([float(k["open"]) for k in recent])
        highs = np.array([float(k["high"]) for k in recent])
        lows = np.array([float(k["low"]) for k in recent])
        closes = np.array([float(k["close"]) for k in recent])
        volumes = np.array([float(k.get("volume", 0.0)) for k in recent])

        ema9 = _ema(closes, 9)
        ema21 = _ema(closes, 21)
        macd_line, _, _ = _macd(closes)
        rsi_arr = _rsi(closes, 14)
        atr_arr = _atr(highs, lows, closes, 14)

        matrix = np.column_stack([
            opens, highs, lows, closes, volumes,
            rsi_arr, macd_line, ema9, ema21, atr_arr,
        ])  # shape (window, 10)

        # Min-max normalize per feature to avoid scale dominance
        mins = matrix.min(axis=0)
        maxs = matrix.max(axis=0)
        ranges = maxs - mins
        ranges[ranges == 0.0] = 1.0
        return ((matrix - mins) / ranges).astype(np.float32)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _infer_sync(self, features: np.ndarray) -> float:
        if not self._model_ready or self._model is None:
            return _NEUTRAL
        try:
            import torch  # noqa: PLC0415

            x = torch.tensor(features, dtype=torch.float32).unsqueeze(0)
            with torch.no_grad():
                out = self._model(x)
            return float(out.squeeze().item())
        except Exception as exc:
            logger.error("TSM: ошибка инференса: %s", exc)
            return _NEUTRAL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def predict(self, klines: List[Dict[str, Any]]) -> float:
        """Returns S_ts ∈ [0, 1]. Returns 0.5 when model is not trained."""
        await self._ensure_loaded()

        features = self._build_features(klines)
        if features is None:
            logger.warning(
                "TSM: недостаточно свечей (нужно %d, есть %d) — S_ts = 0.5",
                self._window,
                len(klines),
            )
            return _NEUTRAL

        s_ts = await asyncio.to_thread(self._infer_sync, features)
        logger.info("TSM: S_ts=%.3f (model_ready=%s)", s_ts, self._model_ready)
        return s_ts
