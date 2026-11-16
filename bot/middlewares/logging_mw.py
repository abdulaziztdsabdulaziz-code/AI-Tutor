"""
Logging middleware — structured request/response logging with timing.

Logs every incoming Telegram update before and after handler execution,
including elapsed time and any unhandled exceptions.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    """
    Logs all incoming Telegram updates with handler execution time.

    Output format::

        → message      | user=123456789 | text="Hello"
        ← message      | user=123456789 | 42.3ms
        ✗ message      | user=123456789 | 18.1ms | ValueError: ...
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        start: float = time.monotonic()

        update_type, user_id, preview = self._extract_meta(data)

        logger.info("→ %-18s | user=%-12s | %s", update_type, user_id, preview)

        try:
            result = await handler(event, data)
            elapsed_ms = (time.monotonic() - start) * 1_000
            logger.info(
                "← %-18s | user=%-12s | %.1fms",
                update_type,
                user_id,
                elapsed_ms,
            )
            return result
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1_000
            logger.error(
                "✗ %-18s | user=%-12s | %.1fms | %s: %s",
                update_type,
                user_id,
                elapsed_ms,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            raise

    @staticmethod
    def _extract_meta(data: dict[str, Any]) -> tuple[str, str, str]:
        """
        Extract update type, user ID, and a short text preview from handler data.

        Returns
        -------
        tuple[str, str, str]
            ``(update_type, user_id_str, preview_str)``
        """
        update: Update | None = data.get("update")
        if update is None:
            return "unknown", "?", ""

        if update.message:
            msg = update.message
            uid = str(msg.from_user.id) if msg.from_user else "?"
            if msg.text:
                preview = f'text="{msg.text[:60]}"'
            elif msg.voice:
                preview = f"voice duration={msg.voice.duration}s"
            elif msg.audio:
                preview = f"audio file_name={msg.audio.file_name}"
            else:
                preview = f"content_type={msg.content_type}"
            return "message", uid, preview

        if update.callback_query:
            cq = update.callback_query
            uid = str(cq.from_user.id)
            preview = f'data="{cq.data}"'
            return "callback_query", uid, preview

        if update.inline_query:
            iq = update.inline_query
            uid = str(iq.from_user.id)
            preview = f'query="{iq.query[:40]}"'
            return "inline_query", uid, preview

        if update.edited_message:
            em = update.edited_message
            uid = str(em.from_user.id) if em.from_user else "?"
            return "edited_message", uid, ""

        if update.channel_post:
            return "channel_post", "channel", ""

        if update.my_chat_member:
            mc = update.my_chat_member
            uid = str(mc.from_user.id)
            return "my_chat_member", uid, ""

        if update.chat_member:
            cm = update.chat_member
            uid = str(cm.from_user.id)
            return "chat_member", uid, ""

        return "other", "?", ""
