"""
Keyboards package — inline and reply keyboard factories.
"""
from bot.keyboards.inline import (
    chess_keyboard,
    confirm_clear_keyboard,
    model_selection_keyboard,
    voice_mode_keyboard,
)
from bot.keyboards.reply import main_menu_keyboard

__all__: list[str] = [
    "chess_keyboard",
    "confirm_clear_keyboard",
    "main_menu_keyboard",
    "model_selection_keyboard",
    "voice_mode_keyboard",
]
