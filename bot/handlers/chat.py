"""
Main conversational AI-Tutor chat handler.

Features:
- Routes incoming text to the user's active OpenRouter LLM model.
- Transparent multi-turn conversational context persistence (last 20 turns in SQLite).
- Telegram message chunking ensuring strict compliance with the 4096-character limit.
- Automatic fallback notification display when primary LLM is unavailable.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.database import Database
from bot.keyboards import main_menu_keyboard
from bot.services.llm_client import LLMRouter, MODEL_REGISTRY

logger = logging.getLogger(__name__)
router: Router = Router(name="chat")

_TUTOR_SYSTEM_PROMPT = (
    "Ты — ведущий университетский AI-наставник и преподаватель высшей квалификации. "
    "Твои ключевые компетенции: архитектура программного обеспечения, чистый код (PEP 8), "
    "алгоритмы, компьютерные науки, высшая математика и инженерное проектирование. "
    "Отвечай глубоко, академично, структурированно, с практическими примерами кода на Python "
    "и подробными объяснениями 'под капотом'."
)

_MAX_TELEGRAM_MESSAGE_LENGTH: int = 4096


class ChatStates(StatesGroup):
    """FSM state for interactive chat."""

    chatting: State = State()


def _split_text_safely(text: str, max_len: int = _MAX_TELEGRAM_MESSAGE_LENGTH) -> list[str]:
    """
    Split text into chunks of at most max_len characters without breaking words.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while len(text) > max_len:
        split_pos = text.rfind("\n\n", 0, max_len)
        if split_pos == -1 or split_pos < max_len // 2:
            split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1 or split_pos < max_len // 2:
            split_pos = text.rfind(" ", 0, max_len)
        if split_pos == -1:
            split_pos = max_len

        chunks.append(text[:split_pos].strip())
        text = text[split_pos:].lstrip()

    if text.strip():
        chunks.append(text.strip())
    return chunks


@router.message(F.text == "💬 Чат")
async def handle_chat_button(message: Message, state: FSMContext, db: Database) -> None:
    """Explicitly activate conversational chat mode."""
    user_id = message.from_user.id  # type: ignore[union-attr]
    await db.set_user_mode(user_id, "chat")
    await state.set_state(ChatStates.chatting)
    current_model = await db.get_user_model(user_id)
    cfg = MODEL_REGISTRY.get(current_model)
    model_name = f"{cfg.emoji} {cfg.display_name}" if cfg else current_model

    await message.answer(
        text=(
            "💬 <b>Режим университетского AI-Тьютора активен</b>\n\n"
            f"Текущая модель: <b>{model_name}</b>\n"
            "Контекст: сохраняются последние 20 сообщений диалога.\n\n"
            "Задайте любой вопрос по архитектуре, коду, математике или науке!"
        ),
        reply_markup=main_menu_keyboard(),
    )


@router.message(
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(
        {
            "💬 Чат",
            "🎙 Голос",
            "♟ Шахматы",
            "🤖 Модель",
            "🗑 Очистить историю",
            "ℹ️ Помощь",
        }
    ),
)
async def handle_user_query(
    message: Message,
    db: Database,
    llm: LLMRouter,
) -> None:
    """
    Handle natural language user query with active LLM model and context.
    """
    user = message.from_user
    if user is None:
        return

    user_id = user.id
    current_mode = await db.get_user_mode(user_id)
    if current_mode != "chat":
        # Do not intercept if user is in voice or chess mode
        return

    user_text = (message.text or "").strip()
    if not user_text:
        return

    bot = message.bot
    if bot:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    model_key = await db.get_user_model(user_id)
    history = await db.get_chat_history(user_id=user_id, limit=20)
    messages_payload = history + [{"role": "user", "content": user_text}]

    model_cfg = MODEL_REGISTRY.get(model_key)
    model_label = f"{model_cfg.emoji} {model_cfg.display_name}" if model_cfg else model_key

    try:
        reply_text = await llm.chat(
            model_key=model_key,
            messages=messages_payload,
            system_prompt=_TUTOR_SYSTEM_PROMPT,
            max_tokens=4096,
            temperature=0.7,
        )
    except RuntimeError as exc:
        logger.error("All models failed for user %d: %s", user_id, exc)
        await message.answer(
            "❌ <b>Ошибка связи с AI:</b> Все доступные модели в данный момент перегружены.\n"
            "Попробуйте переключить модель через /model или повторить запрос через пару минут."
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in chat handler for user %d", user_id)
        await message.answer(
            f"❌ <b>Непредвиденный сбой:</b> <code>{type(exc).__name__}: {exc}</code>"
        )
        return

    # Persist interaction turns to SQLite
    await db.add_message(user_id=user_id, role="user", content=user_text)
    await db.add_message(user_id=user_id, role="assistant", content=reply_text)

    # Deliver reply chunks
    chunks = _split_text_safely(reply_text)
    for idx, chunk in enumerate(chunks):
        footer = f"\n\n<i>🤖 {model_label}</i>" if idx == len(chunks) - 1 else ""
        await message.answer(text=chunk + footer)

    logger.info(
        "AI response delivered | user=%d | model=%s | length=%d chars",
        user_id,
        model_key,
        len(reply_text),
    )
