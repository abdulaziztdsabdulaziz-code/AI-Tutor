"""
Aiogram routers package — start, chat, voice, models, and chess.
"""
from bot.handlers import chat, chess, models, start, voice

__all__: list[str] = ["chat", "chess", "models", "start", "voice"]
