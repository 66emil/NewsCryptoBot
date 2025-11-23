import logging
from typing import Callable, Awaitable

from pyrogram import Client, filters
from pyrogram.types import Message

from trading_bot.config import Config

logger = logging.getLogger(__name__)


def create_telegram_client(config: Config) -> Client:
    """
    Создаёт экземпляр Pyrogram-клиента, залогиненного как юзер.
    """
    app = Client(
        name=config.TELEGRAM_SESSION_NAME,
        api_id=config.TELEGRAM_API_ID,
        api_hash=config.TELEGRAM_API_HASH,
        workdir=".",  # сессия будет сохранена в текущей директории
    )
    return app


def setup_news_handler(
    app: Client,
    config: Config,
    news_callback: Callable[[Message], Awaitable[None]],
) -> None:
    """
    Настраивает обработчик новых сообщений из заданных каналов.
    """

    @app.on_message(filters.chat(config.TELEGRAM_CHANNEL_IDS))
    async def _handler(client: Client, message: Message) -> None:  # type: ignore[unused-variable]
        if not message.text and not message.caption:
            return
        logger.info(
            "Новое сообщение из Telegram канала %s: id=%s",
            message.chat.id,
            message.id,
        )
        await news_callback(message)



