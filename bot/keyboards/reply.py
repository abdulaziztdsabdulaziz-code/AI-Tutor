"""
Reply keyboards factory for the AI-Tutor bot.

Provides the persistent bottom navigation panel for quick switching between
core modalities: Chat, Voice, Chess, Model Selection, Clear History, and Help.
"""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """
    Build the persistent main menu reply keyboard.

    Layout (3 columns x 2 rows):
    ┌─────────────┬─────────────┬─────────────┐
    │   💬 Чат    │   🎙 Голос  │  ♟ Шахматы  │
    ├─────────────┼─────────────┼─────────────┤
    │  🤖 Модель  │ 🗑 Очистить │   ℹ️ Помощь │
    └─────────────┴─────────────┴─────────────┘

    Returns
    -------
    ReplyKeyboardMarkup
        Persistent reply keyboard with resize_keyboard=True.
    """
    builder = ReplyKeyboardBuilder()
    buttons: list[str] = [
        "💬 Чат",
        "🎙 Голос",
        "♟ Шахматы",
        "🤖 Модель",
        "🗑 Очистить историю",
        "ℹ️ Помощь",
    ]
    for text in buttons:
        builder.add(KeyboardButton(text=text))
    builder.adjust(3, 3)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=False)
