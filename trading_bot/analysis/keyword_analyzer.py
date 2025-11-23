import asyncio
import logging
import re
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


positive_keywords: Dict[str, float] = {
    "продают": 0.5,
    "одобрение": 0.5,
    "рост": 0.4,
    "повышение": 0.3,
    "инвестиции": 0.4,
    "партнёрство": 0.3,
    "институциональный интерес": 0.5,
    "бычий": 0.4,
    "улучшение": 0.3,
    "рекорд": 0.5,
    "увеличение объёмов": 0.4,
    "запуск etf": 0.6,
    "сильные данные": 0.3,
    "прибыль": 0.3,
}

negative_keywords: Dict[str, float] = {
    "покупают": -0.5,
    "взлом": -0.7,
    "хак": -0.7,
    "обвал": -0.6,
    "падение": -0.4,
    "суд": -0.5,
    "запрет": -0.6,
    "арест": -0.4,
    "кража": -0.6,
    "фуд": -0.5,
    "риски": -0.3,
    "медвежий": -0.4,
    "проблемы": -0.3,
    "банкротство": -0.7,
}

neutral_keywords: Dict[str, float] = {
    "заявление": 0.0,
    "обновление": 0.0,
    "анонс": 0.0,
    "комментарий": 0.0,
}

# Все интересующие нас коины помечаются знаком #, поэтому берем именно их:
# #BTC, #AAVE, #PENDLE, #PEOPLE и т.п.
_TICKER_REGEX = re.compile(r"#([A-Z0-9]{2,15})", re.IGNORECASE)


async def extract_ticker(text: str) -> str | None:
    """
    Асинхронное извлечение тикера из текста по regex.
    Ожидаются тикеры в формате BTC, BTCUSDT и т.п.
    """
    return await asyncio.to_thread(_sync_extract_ticker, text)


def _sync_extract_ticker(text: str) -> str | None:
    # Находим все #Тикеры в тексте
    matches = _TICKER_REGEX.findall(text)
    if not matches:
        return None
    # Возвращаем первый найденный тикер в верхнем регистре
    return matches[0].upper()


async def analyze_news(text: str) -> Dict[str, Any]:
    """
    Анализ новости на русском языке:
    - извлечение тикера
    - подсчёт news_score на основе словарей ключевых слов
    - список сработавших ключевых слов
    """
    logger.debug("Начало анализа новости длиной %d символов", len(text))
    lowercase = text.lower()

    triggered: Dict[str, List[str]] = {
        "positive": [],
        "negative": [],
        "neutral": [],
    }
    score = 0.0

    # Поиск положительных ключевых слов
    for word, weight in positive_keywords.items():
        if word in lowercase:
            triggered["positive"].append(word)
            score += weight

    # Поиск отрицательных ключевых слов
    for word, weight in negative_keywords.items():
        if word in lowercase:
            triggered["negative"].append(word)
            score += weight

    # Поиск нейтральных ключевых слов
    for word, weight in neutral_keywords.items():
        if word in lowercase:
            triggered["neutral"].append(word)
            score += weight

    # Нормализация score в диапазон [-1, 1] через tanh-подобный кламп
    if score > 1.0:
        score = 1.0
    if score < -1.0:
        score = -1.0

    ticker = await extract_ticker(text)

    result = {
        "ticker": ticker,
        "news_score": score,
        "keywords_triggered": triggered,
    }
    logger.info("Результат анализа новости: %s", result)
    return result


