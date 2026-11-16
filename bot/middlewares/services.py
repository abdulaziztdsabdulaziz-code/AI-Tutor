"""
Services injection middleware for aiogram 3.x.

Injects shared application services (Database, LLMRouter, DiarizationClient, ChessEngine)
into every incoming Telegram event's data dictionary so handlers receive them
as typed keyword arguments.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.database import Database
from bot.services.chess_engine import ChessEngine
from bot.services.diarization_client import DiarizationClient
from bot.services.llm_client import LLMRouter

logger = logging.getLogger(__name__)


class ServicesMiddleware(BaseMiddleware):
    """
    Middleware injecting core service singletons into aiogram handler context.
    """

    def __init__(
        self,
        db: Database,
        llm: LLMRouter,
        diarization: DiarizationClient,
        chess: ChessEngine,
    ) -> None:
        super().__init__()
        self._db = db
        self._llm = llm
        self._diarization = diarization
        self._chess = chess

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Inject services into data dict prior to handler execution."""
        data["db"] = self._db
        data["llm"] = self._llm
        data["diarization"] = self._diarization
        data["chess"] = self._chess
        return await handler(event, data)
