"""
Voice transcription handler with Deepgram speaker diarization and Smart Role Mapping.

Workflow:
1. User activates voice mode (/voice command or 🎙 Голос keyboard button).
2. Bot sets FSM state -> waiting_for_audio.
3. User sends a voice note (.ogg Opus) or audio file (.mp3, .wav, etc.).
4. Bot downloads the audio directly into memory (io.BytesIO) to avoid disk I/O.
5. Deepgram Nova-3 / Nova-2 transcribes with diarize=True, punctuate=True, language="ru".
6. Smart Role Mapping post-processes speaker tags into roles:
   - 🗣 Спикер 1 (Мужчина_1): [text]
   - 🗣 Спикер 2 (Женщина_1): [text]
7. Result is partitioned into clean chunks (<= 4096 characters) without mid-word breaks.
"""
from __future__ import annotations

import io
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.database import Database
from bot.keyboards import main_menu_keyboard, voice_mode_keyboard
from bot.services.diarization_client import (
    DiarizationClient,
    DiarizationResult,
    SpeakerRoleAnalyzer,
)
from bot.services.llm_client import LLMRouter

logger = logging.getLogger(__name__)
router: Router = Router(name="voice")


class VoiceStates(StatesGroup):
    """FSM states for the voice transcription workflow."""

    waiting_for_audio: State = State()


@router.message(Command("voice"))
@router.message(F.text == "🎙 Голос")
async def handle_voice_mode(
    message: Message, state: FSMContext, db: Database
) -> None:
    """Activate voice transcription mode and prompt user to upload audio."""
    user_id: int = message.from_user.id  # type: ignore[union-attr]
    await db.set_user_mode(user_id, "voice")
    await state.set_state(VoiceStates.waiting_for_audio)

    await message.answer(
        text=(
            "🎙 <b>Режим транскрипции с диаризацией и ролями</b>\n\n"
            "Отправь голосовое сообщение или аудиофайл (OGG, MP3, WAV, FLAC, M4A).\n\n"
            "⚙️ <b>Возможности:</b>\n"
            "• Движок: Deepgram Nova-3 / Nova-2\n"
            "• Диаризация спикеров (разделение по голосам)\n"
            "• <b>Smart Role Mapping:</b> контекстное распознавание ролей\n"
            "  <i>(🗣 Спикер 1 (Мужчина_1), 🗣 Спикер 2 (Женщина_1))</i>\n"
            "• Автоматическая пунктуация и временные метки\n\n"
            "<i>Все аудиоданные обрабатываются в оперативной памяти (in-memory) "
            "без сохранения на диск.</i>"
        ),
        reply_markup=voice_mode_keyboard(),
    )
    logger.info("Voice transcription mode activated for user_id=%d", user_id)


@router.message(
    VoiceStates.waiting_for_audio,
    F.voice | F.audio | F.document,
)
async def handle_audio_message(
    message: Message,
    state: FSMContext,
    db: Database,
    diarization: DiarizationClient,
    llm: LLMRouter,
) -> None:
    """
    Download audio directly into memory buffer, transcribe via Deepgram, apply
    Smart Role Mapping, and deliver chunks to user.
    """
    status_msg = await message.answer("⏳ <b>Загрузка аудио в память…</b>")

    # Determine file_id and description
    if message.voice:
        file_id: str = message.voice.file_id
        content_label = f"голосовое сообщение ({message.voice.duration} сек)"
    elif message.audio:
        file_id = message.audio.file_id
        content_label = f"аудиофайл ({message.audio.file_name or 'audio'})"
    elif message.document and (message.document.mime_type or "").startswith("audio/"):
        file_id = message.document.file_id
        content_label = f"аудио-документ ({message.document.file_name or 'document'})"
    else:
        await status_msg.edit_text("❌ Пожалуйста, отправьте голосовое сообщение или аудиофайл.")
        return

    bot = message.bot
    if bot is None:
        await status_msg.edit_text("❌ Ошибка инициализации бота.")
        return

    # In-memory buffer download (no disk I/O)
    audio_buffer = io.BytesIO()
    try:
        tg_file = await bot.get_file(file_id)
        if tg_file.file_path is None:
            raise ValueError("Telegram did not return a valid file path.")
        await bot.download_file(tg_file.file_path, destination=audio_buffer)
        audio_buffer.seek(0)
    except Exception as exc:
        logger.error("Failed to download audio for user %d: %s", message.from_user.id, exc)  # type: ignore[union-attr]
        await status_msg.edit_text(
            f"❌ <b>Ошибка при загрузке аудио:</b> <code>{exc}</code>"
        )
        return

    await status_msg.edit_text(
        f"🔍 <b>Транскрибирую {content_label}…</b>\n"
        "<i>Deepgram распознает речь и диаризует спикеров…</i>"
    )

    try:
        result: DiarizationResult = await diarization.transcribe(
            audio_data=audio_buffer,
            language="ru",
            model="nova-3",
        )
    except Exception as exc:
        logger.error("Deepgram transcription failed for user %d: %s", message.from_user.id, exc)  # type: ignore[union-attr]
        await status_msg.edit_text(
            f"❌ <b>Сбой транскрипции Deepgram:</b>\n<code>{exc}</code>\n\n"
            "Пожалуйста, проверьте аудиозапись и повторите попытку."
        )
        return

    if not result.segments:
        await status_msg.edit_text("🔇 <b>Речь не обнаружена</b> в переданном аудио.")
        return

    await status_msg.edit_text("🧠 <b>Smart Role Mapping:</b> определяю роли спикеров…")

    # Smart Role Mapping: analyze context / grammatical cues or ask LLM
    role_mapping = await SpeakerRoleAnalyzer.infer_roles_with_llm(
        segments=result.segments, llm=llm
    )

    chunks = result.to_telegram_chunks(max_len=4096, role_mapping=role_mapping)

    # Edit status message to show the first chunk
    await status_msg.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await message.answer(chunk)

    logger.info(
        "Voice transcription completed | user=%d | segments=%d | chunks=%d | duration=%.1fs",
        message.from_user.id,  # type: ignore[union-attr]
        len(result.segments),
        len(chunks),
        result.duration,
    )


@router.callback_query(F.data == "voice:back")
async def handle_voice_back(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    """Exit voice mode, clear FSM state, and return to main menu."""
    user_id: int = callback.from_user.id
    await state.clear()
    await db.set_user_mode(user_id, "chat")
    await callback.answer("Возврат в главное меню.")
    if callback.message:
        await callback.message.edit_text("🏠 <b>Возврат в главное меню.</b>")
        await callback.message.answer(
            text="Выбери действие из меню ниже:",
            reply_markup=main_menu_keyboard(),
        )
    logger.info("Voice mode deactivated for user_id=%d", user_id)
