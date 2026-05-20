import asyncio
import logging
import time
from typing import Any, Dict

import numpy as np
from pyrogram.types import Message

from trading_bot.analysis.keyword_analyzer import extract_ticker
from trading_bot.analysis.npm import NewsProcessingModule
from trading_bot.analysis.tam import TechnicalAnalysisModule
from trading_bot.analysis.tsm import TimeSeriesModule
from trading_bot.analysis.sgs import aggregate as sgs_aggregate
from trading_bot.analysis.fds import detect_flat
from trading_bot.bybit import orders
from trading_bot.config import get_config
from trading_bot.storage.storage import save_news
from trading_bot.telegram.client import create_telegram_client, setup_news_handler
from trading_bot.exchange.router import route_symbol
from trading_bot.exchange.factory import get_exchange

logger = logging.getLogger(__name__)

# Module singletons — initialised lazily on first message
_npm: NewsProcessingModule | None = None
_tam: TechnicalAnalysisModule | None = None
_tsm: TimeSeriesModule | None = None


def _get_npm() -> NewsProcessingModule:
    global _npm
    if _npm is None:
        cfg = get_config()
        _npm = NewsProcessingModule(
            model_name=cfg.NPM_MODEL_NAME,
            lambda_decay=cfg.NPM_LAMBDA_DECAY,
        )
    return _npm


def _get_tam() -> TechnicalAnalysisModule:
    global _tam
    if _tam is None:
        _tam = TechnicalAnalysisModule()
    return _tam


def _get_tsm() -> TimeSeriesModule:
    global _tsm
    if _tsm is None:
        cfg = get_config()
        _tsm = TimeSeriesModule(
            model_path=cfg.TSM_MODEL_PATH,
            window=cfg.TSM_WINDOW,
        )
    return _tsm


# ---------------------------------------------------------------------------
# Position monitor (unchanged logic)
# ---------------------------------------------------------------------------

async def monitor_positions() -> None:
    logger.info("Запущен мониторинг позиций (таймер 15 минут)")
    exchange = get_exchange()

    while True:
        try:
            await asyncio.sleep(60)

            positions = await exchange.get_positions()
            if not positions:
                continue

            now_ts = int(time.time() * 1000)
            for pos in positions:
                created_time = pos.get("createdTime") or pos.get("updatedTime")
                if not created_time:
                    continue

                duration_min = (now_ts - int(created_time)) / 1000 / 60
                symbol = pos.get("symbol")
                side = pos.get("side")

                if duration_min > 15:
                    logger.info(
                        "Позиция %s (%s) открыта %.1f мин > 15. Принудительное закрытие…",
                        symbol, side, duration_min,
                    )
                    res = await exchange.close_position(symbol, side)
                    logger.info("Результат закрытия %s: %s", symbol, res)

        except asyncio.CancelledError:
            logger.info("Мониторинг позиций остановлен")
            break
        except Exception as exc:
            logger.error("Ошибка в мониторинге позиций: %s", exc, exc_info=True)
            await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Main news processing pipeline
# ---------------------------------------------------------------------------

async def process_news_message(message: Message) -> None:
    cfg = get_config()
    text = message.text or message.caption or ""
    msg_ts = time.time()

    # ── 1. Ticker extraction ──────────────────────────────────────────────
    raw_ticker = await extract_ticker(text)
    if raw_ticker:
        rt = raw_ticker.upper()
        symbol = rt if rt.endswith(("USDT", "USDC")) else f"{rt}USDT"
    else:
        symbol = cfg.DEFAULT_SYMBOL
        logger.info("Тикер не распознан, используем символ по умолчанию %s", symbol)

    # ── 2. Persist news ───────────────────────────────────────────────────
    await save_news({
        "text": text,
        "chat_id": message.chat.id,
        "message_id": message.id,
        "ticker": raw_ticker,
        "symbol": symbol,
        "timestamp": msg_ts,
    })

    # ── 3. Exchange routing ───────────────────────────────────────────────
    exchange = await route_symbol(symbol)
    if exchange is None:
        logger.info("Тикер %s не найден ни на одной бирже — пропускаем", symbol)
        return

    # ── 4. Fetch candles ──────────────────────────────────────────────────
    klines = await exchange.get_klines(symbol, "1", limit=cfg.MAX_CANDLES)
    if not klines:
        logger.warning("Нет свечей для %s — пропускаем", symbol)
        return

    # ── 5. TAM — Technical Analysis Module ───────────────────────────────
    tam_result = await _get_tam().analyze(klines)
    s_tech: float = tam_result["s_tech"]
    indicators: Dict[str, Any] = tam_result.get("indicators") or {}
    atr = float(indicators.get("atr") or 0.0)
    adx = float(indicators.get("adx") or 0.0)
    last_price = float(indicators.get("last_price") or 0.0)

    if last_price <= 0:
        logger.warning("last_price недоступен для %s — пропускаем", symbol)
        return

    # ── 6. FDS — Flat Detection System ───────────────────────────────────
    if len(klines) >= 20:
        closes_arr = np.array([float(k["close"]) for k in klines])
        highs_arr = np.array([float(k["high"]) for k in klines])
        lows_arr = np.array([float(k["low"]) for k in klines])
        fds_result = detect_flat(closes_arr, highs_arr, lows_arr, adx, atr)
        if fds_result["is_flat"]:
            logger.info(
                "FDS: флэт по %s — торговый сигнал пропускается (%s)",
                symbol, fds_result["criteria"],
            )
            return

    # ── 7. NPM — News Processing Module (FinBERT) ─────────────────────────
    npm_result = await _get_npm().analyze(text)
    s_news: float = npm_result["s_news"]

    # ── 8. TSM — Time Series Module (LSTM) ───────────────────────────────
    s_ts: float = await _get_tsm().predict(klines)

    # ── 9. SGS — Signal Generation System ────────────────────────────────
    decision = await sgs_aggregate(s_news, s_tech, s_ts, atr)
    signal: str = decision["signal"]

    logger.info(
        "SGS [%s]: signal=%s S_news=%.3f S_tech=%.3f S_ts=%.3f S_final=%.3f",
        symbol, signal,
        decision["s_news"], decision["s_tech"], decision["s_ts"], decision["s_final"],
    )

    if signal == "NEUTRAL":
        logger.info("Сигнал NEUTRAL — ордера не отправляются")
        return

    # ── 10. Build order plan ──────────────────────────────────────────────
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

    # ── 11. Execute order + TP/SL ─────────────────────────────────────────
    if signal == "LONG":
        order_resp = await orders.create_long(symbol=symbol, qty=qty, exchange=exchange)
    else:
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
            "Позиция %s открыта, но TP/SL не установлены (ATR=%.4f)", symbol, atr,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_bot() -> None:
    cfg = get_config()

    logging.basicConfig(
        level=getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = create_telegram_client(cfg)
    setup_news_handler(app, cfg, process_news_message)

    logger.info("Запуск Telegram клиента. Слушаем каналы: %s", cfg.TELEGRAM_CHANNEL_IDS)

    await app.start()

    monitor_task = asyncio.create_task(monitor_positions())
    try:
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
