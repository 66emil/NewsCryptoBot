"""
News Processing Module (NPM).
FinBERT sentiment analysis → S_news = clip(P+ − P− + 0.5, 0, 1).
Supports exponential time-decay aggregation across multiple news items.

Note: FinBERT (ProsusAI/finbert) is English-only. For Russian news set
NPM_MODEL_NAME=blanchefort/rubert-base-cased-sentiment in .env — it
exposes the same positive/negative/neutral labels via the same API.
"""
import asyncio
import logging
import math
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NEUTRAL_SCORE = 0.5


class NewsProcessingModule:
    """
    Lazy-loads the HuggingFace text-classification pipeline on first use.
    Thread-safe via asyncio.Lock.
    """

    def __init__(
        self,
        model_name: str = "ProsusAI/finbert",
        lambda_decay: float = 0.1,
        device: str = "cpu",
    ) -> None:
        self._model_name = model_name
        self._lambda_decay = lambda_decay
        self._device_id = -1  # -1 = CPU for HF pipeline
        self._pipeline = None
        self._attempted = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_loaded(self) -> None:
        async with self._lock:
            if self._attempted:
                return
            self._attempted = True
            logger.info("NPM: загрузка модели %s …", self._model_name)
            try:
                await asyncio.to_thread(self._load_sync)
            except Exception as exc:
                logger.error(
                    "NPM: не удалось загрузить %s: %s — будет использован нейтральный скор 0.5",
                    self._model_name,
                    exc,
                )

    def _load_sync(self) -> None:
        from transformers import pipeline as hf_pipeline  # noqa: PLC0415

        self._pipeline = hf_pipeline(
            "text-classification",
            model=self._model_name,
            top_k=None,
            device=self._device_id,
        )
        logger.info("NPM: модель %s успешно загружена", self._model_name)

    def _infer_sync(self, text: str) -> Dict[str, float]:
        if self._pipeline is None:
            return {"positive": 0.0, "negative": 0.0, "neutral": 1.0}
        # HuggingFace limits tokenizer to 512 tokens; trim at char level to be safe
        results = self._pipeline(text[:1024])
        # pipeline with top_k=None returns [[{label, score}, ...]]
        if results and isinstance(results[0], list):
            results = results[0]
        return {r["label"].lower(): float(r["score"]) for r in results}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(self, text: str) -> Dict[str, Any]:
        """
        Scores a single text.
        Returns {s_news, positive, negative, neutral}.
        S_news = clip(P+ − P− + 0.5, 0, 1)
        """
        await self._ensure_loaded()
        scores = await asyncio.to_thread(self._infer_sync, text)

        p_pos = scores.get("positive", 0.0)
        p_neg = scores.get("negative", 0.0)
        p_neu = scores.get("neutral", 0.0)

        s_news = max(0.0, min(1.0, p_pos - p_neg + 0.5))

        logger.info(
            "NPM: P+=%.3f P-=%.3f P0=%.3f → S_news=%.3f",
            p_pos, p_neg, p_neu, s_news,
        )
        return {
            "s_news": s_news,
            "positive": p_pos,
            "negative": p_neg,
            "neutral": p_neu,
        }

    def aggregate(
        self,
        items: List[Dict[str, Any]],
        now_ts: Optional[float] = None,
    ) -> float:
        """
        Exponential time-decay aggregation over a list of scored news items.
        Each item: {s_news: float, timestamp: float (unix seconds)}.
        weight_i = exp(−λ · Δt_hours)
        """
        if not items:
            return _NEUTRAL_SCORE
        now = now_ts if now_ts is not None else time.time()
        total_w = 0.0
        weighted = 0.0
        for item in items:
            delta_h = (now - item.get("timestamp", now)) / 3600.0
            w = math.exp(-self._lambda_decay * delta_h)
            weighted += item["s_news"] * w
            total_w += w
        return weighted / total_w if total_w > 0 else _NEUTRAL_SCORE
