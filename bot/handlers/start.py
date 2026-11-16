"""
Start, help, and user state initialization handlers.

Handles:
- /start command with user onboarding and main menu initialization.
- /help command and ℹ️ Помощь button for university presentation and user guide.
- /clear and 🗑 Очистить историю dialog with 2-step confirmation.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database import Database
from bot.keyboards import confirm_clear_keyboard, main_menu_keyboard

logger = logging.getLogger(__name__)
router: Router = Router(name="start")

_WELCOME_TEXT = (
    "👋 <b>Добро пожаловать в AI-Tutor & Multimodal Powerhouse!</b>\n"
    "<i>(Antigravity Edition — Production Grade)</i>\n\n"
    "🚀 <b>Ключевые возможности системы:</b>\n"
    "1. 💬 <b>Multi-Model AI Tutor:</b> Маршрутизатор 5 моделей через OpenRouter "
    "(Claude 3.7 Sonnet, GPT-4o, Gemini 2.5 Pro, DeepSeek R1/V3, Grok) с авто-fallback.\n"
    "2. 🎙 <b>Voice Diarization & Role Mapping:</b> Потоковая транскрипция аудио Deepgram "
    "Nova-3 с определением спикеров и ролей (Мужчина/Женщина/Преподаватель).\n"
    "3. ♟ <b>Chess Master & Stockfish:</b> Расчёт топ-3 ходов, оценка в сантипешках и "
    "стратегический анализ позиций по FEN или списку ходов.\n"
    "4. 🔒 <b>Security & Architecture:</b> Строгие Webhooks с валидацией X-Telegram токена, "
    "Connection Pooling SQLite и полный PEP 8.\n\n"
    "⚡ <b>Быстрые команды:</b>\n"
    "/model — выбрать активную нейросеть\n"
    "/voice — режим голосовой транскрипции\n"
    "/chess — шахматный гроссмейстер\n"
    "/clear — очистить историю диалога\n"
    "/help  — руководство пользователя и стек\n\n"
    "<i>Используйте кнопки постоянного меню ниже для навигации ↓</i>"
)

_HELP_GUIDE = (
    "📖 <b>Техническая документация и руководство — AI-Tutor</b>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🧠 <b>1. Multi-Model Router (OpenRouter)</b>\n"
    "• <b>Claude 3.7 Sonnet:</b> Глубокий код-ревью, системная архитектура.\n"
    "• <b>GPT-4o:</b> Универсальный контекст и общие задачи.\n"
    "• <b>Gemini 2.5 Pro:</b> Анализ массивных фрагментов данных.\n"
    "• <b>DeepSeek R1 / V3:</b> Математические доказательства и пошаговые алгоритмы.\n"
    "• <b>Grok:</b> Быстрый креативный контекст.\n"
    "• <i>Отказоустойчивость:</i> при сетевых ошибках или 429 Rate Limit запрос "
    "прозрачно направляется на вторичную модель.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🎙 <b>2. Голосовой процессинг & Диаризация</b>\n"
    "• Аудио передаётся в памяти (io.BytesIO) в Deepgram Nova-3.\n"
    "• Smart Role Mapping определяет роли: <code>🗣 Спикер 1 (Мужчина_1): [текст]</code>.\n"
    "• Автоматическая разбивка длинных стенограмм на блоки до 4096 символов.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "♟ <b>3. Шахматный анализатор</b>\n"
    "• Парсинг нотации FEN или ходов в формате SAN / PGN.\n"
    "• Расчёт топ-3 ходов (MultiPV=3) движком Stockfish 16 / Python-Chess Heuristics.\n"
    "• Тактические аннотации (взятия, рокировки, вскрытые шахи, мат).\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🛡 <b>4. Архитектура</b>\n"
    "• FastAPI + aiogram 3.x в режиме Webhook.\n"
    "• Валидация заголовка <code>X-Telegram-Bot-Api-Secret-Token</code>.\n"
    "• Пул асинхронных соединений SQLite (aiosqlite + WAL mode)."
)


@router.message(CommandStart())
async def handle_start(message: Message, db: Database) -> None:
    """Register user in database and display welcoming navigation panel."""
    user = message.from_user
    if user is None:
        return

    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )
    logger.info("User registered/started bot: %d (@%s)", user.id, user.username)

    await message.answer(
        text=_WELCOME_TEXT,
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def handle_help(message: Message) -> None:
    """Send comprehensive technical manual for university presentation."""
    await message.answer(text=_HELP_GUIDE)


@router.message(F.text == "🗑 Очистить историю")
@router.message(Command("clear"))
async def handle_clear_request(message: Message) -> None:
    """Prompt user with two-step confirmation before deleting chat history."""
    await message.answer(
        text=(
            "⚠️ <b>Подтверждение очистки истории</b>\n\n"
            "Вы действительно хотите стереть всю сохранённую историю переписки?\n"
            "AI-Тьютор забудет предыдущий контекст обсуждения."
        ),
        reply_markup=confirm_clear_keyboard(),
    )


@router.callback_query(F.data == "clear:confirm")
async def handle_clear_confirm(callback: CallbackQuery, db: Database) -> None:
    """Execute deletion of user chat records in SQLite."""
    user_id = callback.from_user.id
    await db.clear_history(user_id)
    await callback.answer("История диалога очищена!")
    if callback.message:
        await callback.message.edit_text("✅ <b>История переписки успешно удалена.</b>")
    logger.info("Cleared history for user_id=%d", user_id)


@router.callback_query(F.data == "clear:cancel")
async def handle_clear_cancel(callback: CallbackQuery) -> None:
    """Cancel history clearing dialog."""
    await callback.answer("Действие отменено.")
    if callback.message:
        await callback.message.edit_text("❌ <b>Очистка отменена.</b> Контекст сохранён.")
