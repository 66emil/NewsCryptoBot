import asyncio
import logging
from typing import Any, Dict

from pyrogram.types import Message

from trading_bot.analysis.keyword_analyzer import analyze_news
from trading_bot.analysis.tech_indicators import calculate_indicators
from trading_bot.analysis.decision_engine import make_decision
from trading_bot.bybit import orders
from trading_bot.config import get_config
from trading_bot.storage.storage import save_news
from trading_bot.telegram.client import create_telegram_client, setup_news_handler
from trading_bot.exchange.router import route_symbol
from trading_bot.exchange.factory import get_exchange


logger = logging.getLogger(__name__)


async def monitor_positions() -> None:
    """
    Фоновая задача: проверка открытых позиций каждые 60 секунд.
    Если позиция открыта более 15 минут, она закрывается принудительно.
    """
    logger.info("Запущен мониторинг позиций (таймер 15 минут)")
    exchange = get_exchange()
    
    while True:
        try:
            # Ждем минуту перед следующей проверкой
            await asyncio.sleep(60)
            
            positions = await exchange.get_positions()
            if not positions:
                continue
                
            import time
            now_ts = int(time.time() * 1000)
            
            for pos in positions:
                # Время создания/обновления позиции в мс
                # Bybit: createdTime или updatedTime
                created_time = pos.get("createdTime") or pos.get("updatedTime")
                if not created_time:
                    continue
                    
                start_ts = int(created_time)
                duration_min = (now_ts - start_ts) / 1000 / 60
                
                symbol = pos.get("symbol")
                side = pos.get("side")
                
                if duration_min > 15:
                    logger.info(
                        "Позиция %s (%s) открыта %.1f минут (> 15 мин). Принудительное закрытие...",
                        symbol, side, duration_min
                    )
                    res = await exchange.close_position(symbol, side)
                    logger.info("Результат закрытия %s: %s", symbol, res)
                    
        except asyncio.CancelledError:
            logger.info("Мониторинг позиций остановлен")
            break
        except Exception as e:
            logger.error("Ошибка в мониторинге позиций: %s", e, exc_info=True)
            await asyncio.sleep(60)


async def process_news_message(message: Message) -> None:
    """
    Полный пайплайн обработки входящей новости из Telegram.
    """
    config = get_config()
    text = message.text or message.caption or ""

    # 1. Анализ новости
    news_data = await analyze_news(text)
    raw_ticker = news_data.get("ticker")

    # Если удалось вытащить тикер из новости – торгуем именно этим коином.
    # Простое правило: если нет суффикса, добавляем USDT (PEOPLE -> PEOPLEUSDT).
    if raw_ticker:
        rt = str(raw_ticker).upper()
        if rt.endswith("USDT") or rt.endswith("USDC"):
            symbol = rt
        else:
            symbol = f"{rt}USDT"
    else:
        symbol = config.DEFAULT_SYMBOL
        logger.info(
            "Тикер в новости не распознан или не подходит под формат, "
            "используем тикер по умолчанию %s",
            symbol,
        )

    news_data["raw_ticker"] = raw_ticker
    news_data["resolved_symbol"] = symbol

    # 2. Сохранение новости
    await save_news(
        {
            "text": text,
            "chat_id": message.chat.id,
            "message_id": message.id,
            **news_data,
        }
    )

    # 3. Выбор биржи через роутер
    exchange = await route_symbol(symbol)
    if exchange is None:
        logger.info(
            "Тикер %s не найден ни на одной поддерживаемой бирже, торговый сигнал пропускается",
            symbol,
        )
        return

    # 4. Технические индикаторы (на выбранной бирже)
    technical_data = await calculate_indicators(symbol, exchange=exchange)

    # 5. Комбинированное решение
    decision = await make_decision(news_data, technical_data)
    signal = decision["signal"]

    logger.info(
        "Сигнал по тикеру %s: %s (score=%.3f)",
        symbol,
        signal,
        decision["final_score"],
    )

    # 6. Торговое действие
    if signal == "HOLD":
        logger.info("Сигнал HOLD, ордера не отправляются")
        return

    indicators: Dict[str, Any] = technical_data.get("indicators", {}) or {}
    last_price_raw = indicators.get("last_price")
    if last_price_raw is None:
        logger.warning(
            "Не удалось получить last_price для %s из технических индикаторов (%s), сделка пропускается",
            symbol,
            indicators,
        )
        return
    last_price = float(last_price_raw)

    tp_dist = float(decision.get("tp", 0.0))
    sl_dist = float(decision.get("sl", 0.0))

    plan = await orders.build_order_plan(
        symbol=symbol,
        signal=signal,
        last_price=last_price,
        tp_dist=tp_dist,
        sl_dist=sl_dist,
        risk_pct=0.01,
        exchange=exchange,
    )

    if not plan.get("success"):
        logger.warning("План сделки не построен: %s", plan)
        return

    side = plan["side"]
    qty = float(plan["qty"])
    tp_price = float(plan["tp_price"])
    sl_price = float(plan["sl_price"])

    if signal == "LONG":
        order_resp = await orders.create_long(symbol=symbol, qty=qty, exchange=exchange)
    else:  # signal == "SHORT"
        order_resp = await orders.create_short(symbol=symbol, qty=qty, exchange=exchange)

    if order_resp.get("success") and tp_dist and sl_dist:
        await orders.set_tp_sl(
            symbol=symbol,
            position_side=side,
            tp=tp_price,
            sl=sl_price,
            exchange=exchange,
        )
    elif order_resp.get("success"):
        logger.warning(
            "Позиция по %s открыта, но TP/SL НЕ установлены! (tp_dist=%.4f, sl_dist=%.4f). "
            "Возможно, не удалось рассчитать ATR.",
            symbol,
            tp_dist,
            sl_dist,
        )


async def run_bot() -> None:
    """
    Точка входа: запуск Pyrogram-клиента и прослушивание указанных каналов.
    """
    config = get_config()

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = create_telegram_client(config)
    setup_news_handler(app, config, process_news_message)

    logger.info("Запуск Telegram клиента. Слушаем каналы: %s", config.TELEGRAM_CHANNEL_IDS)

    # Pyrogram сам управляет event loop внутри run()
    await app.start()
    
    # Запускаем мониторинг позиций в фоне
    monitor_task = asyncio.create_task(monitor_positions())
    
    try:
        # "Вечный" таск, чтобы клиент оставался запущенным
        await asyncio.Event().wait()
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        await app.stop()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()


