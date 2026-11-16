"""
Inline keyboards factory for the AI-Tutor bot.

Provides builders for:
- Model selection menu (/model)
- Voice mode action bar
- Chess input mode selector (FEN vs Moves)
- Chat history clearing confirmation
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.llm_client import MODEL_REGISTRY


def model_selection_keyboard(current_model: str) -> InlineKeyboardMarkup:
    """
    Build an inline keyboard presenting all available OpenRouter LLM models.

    The currently active model is highlighted with a checkmark (✅).

    Parameters
    ----------
    current_model : str
        The unique key of the user's currently active model.

    Returns
    -------
    InlineKeyboardMarkup
        Inline keyboard with one button per model plus a cancel button.
    """
    builder = InlineKeyboardBuilder()
    for cfg in MODEL_REGISTRY.values():
        active_mark = " ✅" if cfg.key == current_model else ""
        builder.button(
            text=f"{cfg.emoji} {cfg.display_name}{active_mark}",
            callback_data=f"model:{cfg.key}",
        )
    builder.button(text="❌ Отмена", callback_data="model:cancel")
    builder.adjust(1)
    return builder.as_markup()


def voice_mode_keyboard() -> InlineKeyboardMarkup:
    """
    Build inline navigation bar for voice transcription mode.

    Returns
    -------
    InlineKeyboardMarkup
        Keyboard containing 'Back to Menu' button.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад в меню", callback_data="voice:back")
    builder.adjust(1)
    return builder.as_markup()


def chess_keyboard() -> InlineKeyboardMarkup:
    """
    Build chess analyzer mode entry keyboard.

    Options:
    - FEN input mode
    - PGN / SAN move list mode
    - Return to main menu

    Returns
    -------
    InlineKeyboardMarkup
        Structured inline keyboard for chess mode.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Анализ по FEN", callback_data="chess:fen")
    builder.button(text="♟ Ввести ходы партии", callback_data="chess:moves")
    builder.button(text="🔙 Назад в меню", callback_data="chess:back")
    builder.adjust(2, 1)
    return builder.as_markup()


def confirm_clear_keyboard() -> InlineKeyboardMarkup:
    """
    Build two-button confirmation dialog for wiping conversational history.

    Returns
    -------
    InlineKeyboardMarkup
        Confirm and Cancel buttons.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, очистить", callback_data="clear:confirm")
    builder.button(text="❌ Отмена", callback_data="clear:cancel")
    builder.adjust(2)
    return builder.as_markup()
