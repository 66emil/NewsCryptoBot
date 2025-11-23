import asyncio
import json
import logging
from pathlib import Path
from typing import Any, List, Dict

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent / "db.json"
_lock = asyncio.Lock()


async def _ensure_db_exists() -> None:
    if not DB_PATH.exists():
        logger.debug("db.json не найден, создаю новый файл по пути %s", DB_PATH)

        def _init():
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            with DB_PATH.open("w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

        await asyncio.to_thread(_init)


async def save_news(news_obj: Dict[str, Any]) -> None:
    """
    Сохранить объект новости в db.json (асинхронно, с блокировкой).
    """
    await _ensure_db_exists()
    async with _lock:
        logger.info("Сохраняю новость в db.json")

        def _write():
            try:
                with DB_PATH.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                data = []
            # Храним только последние 10 записей
            data.append(news_obj)
            if len(data) > 10:
                data = data[-10:]
            with DB_PATH.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        await asyncio.to_thread(_write)


async def load_all_news() -> List[Dict[str, Any]]:
    """
    Загрузить все новости из db.json.
    """
    await _ensure_db_exists()
    async with _lock:
        logger.debug("Загружаю все новости из db.json")

        def _read() -> List[Dict[str, Any]]:
            try:
                with DB_PATH.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return []

        return await asyncio.to_thread(_read)


