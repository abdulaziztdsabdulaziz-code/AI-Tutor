"""
FastAPI application entry point — production webhook server for the AI-Tutor bot.

Architecture:
    Telegram Update -> POST /webhook -> FastAPI -> Secret Token Check -> aiogram Dispatcher -> Handlers

Security:
    Every webhook update is verified against the X-Telegram-Bot-Api-Secret-Token header
    using constant-time string comparison. Requests with missing or invalid tokens are
    immediately rejected with HTTP 403 Forbidden before reaching the dispatcher.

Lifespan:
    Startup:
        - Establishes SQLite connection pool with WAL mode and tables.
        - Instantiates service singletons (LLMRouter, DiarizationClient, ChessEngine).
        - Configures aiogram Bot, Dispatcher with MemoryStorage, Routers, and Middlewares.
        - Registers webhook with Telegram API using the configured secret token.
    Shutdown:
        - Deregisters Telegram webhook.
        - Closes Bot HTTP session, Deepgram HTTP client, and database connection pool.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from bot.config import settings
from bot.database import Database
from bot.handlers import chat, chess, models, start, voice
from bot.middlewares import (
    LoggingMiddleware,
    ServicesMiddleware,
    verify_telegram_secret_token,
)
from bot.services.chess_engine import ChessEngine
from bot.services.diarization_client import DiarizationClient
from bot.services.llm_client import LLMRouter

# ── Logging Configuration ─────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Application Lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    FastAPI lifespan manager orchestrating startup and graceful shutdown.
    """
    logger.info("Starting AI-Tutor & Multimodal Powerhouse (Antigravity Edition)...")

    # 1. Database Connection Pool Setup
    db = Database(
        db_path=settings.DATABASE_PATH,
        max_connections=settings.DATABASE_POOL_SIZE,
    )
    await db.connect()

    # 2. Service Singletons Initialization
    llm = LLMRouter(api_key=settings.OPENROUTER_API_KEY)
    diarization = DiarizationClient(api_key=settings.DEEPGRAM_API_KEY)
    chess_engine = ChessEngine(stockfish_path=settings.STOCKFISH_PATH)

    # 3. aiogram Bot Instance
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # 4. Dispatcher with MemoryStorage FSM
    dp = Dispatcher(storage=MemoryStorage())

    # 5. Router Registration (Specific routers before general text handler)
    dp.include_router(start.router)
    dp.include_router(models.router)
    dp.include_router(voice.router)
    dp.include_router(chess.router)
    dp.include_router(chat.router)  # Fallback catch-all conversational handler

    # 6. Outer Middlewares Registration
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(
        ServicesMiddleware(
            db=db,
            llm=llm,
            diarization=diarization,
            chess=chess_engine,
        )
    )

    # 7. Telegram Webhook Registration
    webhook_url = f"{settings.WEBHOOK_URL.rstrip('/')}{settings.WEBHOOK_PATH}"
    try:
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.WEBHOOK_SECRET,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True,
        )
        bot_user = await bot.get_me()
        logger.info(
            "Webhook registered successfully: @%s -> %s",
            bot_user.username,
            webhook_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not set Telegram webhook directly (%s). Running in standalone/webhook test mode.",
            exc,
        )

    # Attach instances to FastAPI state for endpoint accessibility
    app.state.bot = bot
    app.state.dp = dp
    app.state.db = db
    app.state.llm = llm
    app.state.diarization = diarization
    app.state.chess = chess_engine

    yield  # Server runs handling incoming webhook requests

    # ── Graceful Shutdown Sequence ─────────────────────────────────────────────
    logger.info("Executing graceful shutdown...")
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Webhook deletion notice: %s", exc)

    await bot.session.close()
    await diarization.aclose()
    await db.close()
    logger.info("AI-Tutor server cleanly terminated.")


# ── FastAPI App Instance ──────────────────────────────────────────────────────

app = FastAPI(
    title="AI-Tutor & Multimodal Powerhouse (Antigravity Edition)",
    description="High-performance async Telegram webhook server powered by aiogram 3.x and FastAPI.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,  # Disabled in production for security hardening
    redoc_url=None,
)


# ── Webhook Endpoint ──────────────────────────────────────────────────────────

@app.post(settings.WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    """
    Receive, authenticate, and dispatch Telegram webhook updates.

    Security:
        Rejects requests whose secret token header does not match settings.WEBHOOK_SECRET.
    """
    if not verify_telegram_secret_token(
        received_token=x_telegram_bot_api_secret_token,
        expected_token=settings.WEBHOOK_SECRET,
    ):
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(
            "Unauthorized webhook attempt from %s with invalid secret token.", client_ip
        )
        raise HTTPException(
            status_code=403,
            detail="Forbidden: invalid X-Telegram-Bot-Api-Secret-Token",
        )

    body = await request.json()
    update = Update.model_validate(body)

    bot: Bot = app.state.bot
    dp: Dispatcher = app.state.dp
    await dp.feed_update(bot=bot, update=update)

    return JSONResponse(content={"ok": True})


# ── Health Probe ──────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health_check() -> JSONResponse:
    """Service liveness probe returning system status."""
    try:
        bot: Bot = app.state.bot
        bot_info = await bot.get_me()
        bot_name = f"@{bot_info.username}"
    except Exception:
        bot_name = "ready (offline mode)"

    return JSONResponse(
        content={
            "status": "ok",
            "bot": bot_name,
            "webhook_path": settings.WEBHOOK_PATH,
            "models_available": 5,
        },
        status_code=200,
    )


# ── CLI Entry Point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "bot.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )
