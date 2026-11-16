"""
Model switching handlers.

Supports:
- /model command and 🤖 Модель reply keyboard button.
- Interactive inline keyboard callback queries with real-time feedback.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.database import Database
from bot.keyboards import model_selection_keyboard
from bot.services.llm_client import MODEL_REGISTRY

logger = logging.getLogger(__name__)
router: Router = Router(name="models")


@router.message(Command("model"))
@router.message(F.text == "🤖 Модель")
async def handle_model_menu(message: Message, db: Database) -> None:
    """Show the interactive model selection inline keyboard."""
    user_id = message.from_user.id  # type: ignore[union-attr]
    current_model = await db.get_user_model(user_id)
    cfg = MODEL_REGISTRY.get(current_model)
    current_label = f"{cfg.emoji} {cfg.display_name}" if cfg else current_model

    await message.answer(
        text=(
            "🤖 <b>Панель выбора AI-модели (OpenRouter Router)</b>\n\n"
            f"Текущая активная модель: <b>{current_label}</b>\n\n"
            "Выберите нейросеть для решения текущей задачи:\n"
            "• 🧠 <b>Claude 3.7 Sonnet</b> — архитектура, код-ревью, глубокий анализ\n"
            "• 🤖 <b>GPT-4o</b> — универсальный мультимодальный интеллект\n"
            "• ✨ <b>Gemini 2.5 Pro</b> — гигантский контекст и сложные рассуждения\n"
            "• 🔢 <b>DeepSeek R1 / V3</b> — математика, алгоритмы, chain-of-thought\n"
            "• ⚡ <b>Grok</b> — креативность и живой актуальный контекст\n\n"
            "<i>В случае сбоя основной модели автоматический fallback переключит запрос на резервную.</i>"
        ),
        reply_markup=model_selection_keyboard(current_model),
    )


@router.callback_query(F.data.startswith("model:"))
async def handle_model_selection(callback: CallbackQuery, db: Database) -> None:
    """Process model choice from inline buttons."""
    if callback.data is None:
        return

    action = callback.data.split(":", 1)[1]

    if action == "cancel":
        await callback.answer("Выбор модели отменён.")
        if callback.message:
            await callback.message.delete()
        return

    if action not in MODEL_REGISTRY:
        await callback.answer("⚠️ Неизвестная модель.", show_alert=True)
        return

    selected_cfg = MODEL_REGISTRY[action]
    user_id = callback.from_user.id
    await db.set_user_model(user_id, action)

    logger.info("User %d selected model: %s (%s)", user_id, action, selected_cfg.display_name)
    await callback.answer(f"✅ Переключено: {selected_cfg.display_name}")

    if callback.message:
        await callback.message.edit_text(
            text=(
                f"✅ <b>Активная модель успешно изменена!</b>\n\n"
                f"{selected_cfg.emoji} <b>{selected_cfg.display_name}</b>\n"
                f"📝 <i>{selected_cfg.description}</i>\n\n"
                "Все последующие вопросы в режиме чата будут направляться этой модели."
            )
        )
