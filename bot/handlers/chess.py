"""
Chess position analysis handler.

Workflow:
1. /chess or ♟ Шахматы -> show entry keyboard (FEN vs Move list).
2. User chooses input method -> bot updates FSM state.
3. User supplies FEN string or SAN/PGN move sequence.
4. ChessEngine parses input and calculates top-3 moves with centipawn scoring and strategic descriptions.
5. Rich HTML evaluation is rendered and sent to the user.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.database import Database
from bot.keyboards import chess_keyboard, main_menu_keyboard
from bot.services.chess_engine import ChessEngine

logger = logging.getLogger(__name__)
router: Router = Router(name="chess")


class ChessStates(StatesGroup):
    """FSM states for the chess analysis workflow."""

    waiting_for_fen: State = State()
    waiting_for_moves: State = State()


# ── Entry Points ──────────────────────────────────────────────────────────────

@router.message(Command("chess"))
@router.message(F.text == "♟ Шахматы")
async def handle_chess_menu(
    message: Message, state: FSMContext, db: Database
) -> None:
    """Display chess mode entry options."""
    user_id: int = message.from_user.id  # type: ignore[union-attr]
    await db.set_user_mode(user_id, "chess")
    await state.clear()

    await message.answer(
        text=(
            "♟ <b>Шахматный Гроссмейстер & Анализатор позиций</b>\n\n"
            "Выберите способ задания позиции:\n\n"
            "• <b>📋 FEN-нотация</b> — вставьте строку FEN (любая позиция)\n"
            "  <i>Пример: rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1</i>\n\n"
            "• <b>♟ Список ходов</b> — введите ходы в формате SAN / PGN\n"
            "  <i>Пример: 1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 4.Ba4 Nf6</i>\n\n"
            "⚙️ <b>Движок:</b> Stockfish MultiPV=3 / Python-Chess Heuristics\n"
            "🎯 <b>Результат:</b> Топ-3 сильнейших хода, оценка в сантипешках и стратегические пояснения."
        ),
        reply_markup=chess_keyboard(),
    )
    logger.info("Chess mode entry shown to user_id=%d", user_id)


# ── Mode Selection Callbacks ──────────────────────────────────────────────────

@router.callback_query(F.data == "chess:fen")
async def handle_chess_fen_mode(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Prompt user for FEN notation."""
    await state.set_state(ChessStates.waiting_for_fen)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            text=(
                "📋 <b>Режим ввода FEN-нотации</b>\n\n"
                "Отправьте FEN-строку шахматной позиции сообщением:\n\n"
                "<i>Пример стандартной стартовой позиции:</i>\n"
                "<code>rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1</code>"
            )
        )


@router.callback_query(F.data == "chess:moves")
async def handle_chess_moves_mode(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Prompt user for move sequence."""
    await state.set_state(ChessStates.waiting_for_moves)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            text=(
                "♟ <b>Режим ввода ходов партии</b>\n\n"
                "Отправьте список ходов в формате PGN или SAN:\n\n"
                "<i>Примеры:</i>\n"
                "• <code>1.e4 e5 2.Nf3 Nc6 3.Bb5 a6</code>\n"
                "• <code>d4 d5 c4 c6 Nf3 Nf6</code>"
            )
        )


# ── Input Processors ──────────────────────────────────────────────────────────

@router.message(ChessStates.waiting_for_fen, F.text)
async def handle_fen_input(
    message: Message, state: FSMContext, chess: ChessEngine
) -> None:
    """Parse and evaluate FEN input."""
    raw_text = (message.text or "").strip()
    await _execute_analysis_pipeline(message, state, chess, raw_text, input_type="fen")


@router.message(ChessStates.waiting_for_moves, F.text)
async def handle_moves_input(
    message: Message, state: FSMContext, chess: ChessEngine
) -> None:
    """Parse and evaluate moves input."""
    raw_text = (message.text or "").strip()
    await _execute_analysis_pipeline(message, state, chess, raw_text, input_type="moves")


async def _execute_analysis_pipeline(
    message: Message,
    state: FSMContext,
    chess: ChessEngine,
    raw_input: str,
    input_type: str,
) -> None:
    """Validate, parse, evaluate position, and send output to user."""
    status_msg = await message.answer("⚙️ <b>Анализирую позицию на доске…</b>")

    try:
        fen = await chess.parse_moves_to_fen(raw_input)
    except ValueError as exc:
        hint = (
            "Убедитесь, что FEN содержит 6 полей через пробел."
            if input_type == "fen"
            else "Проверьте нотацию ходов (например: 1.e4 e5 2.Nf3 Nc6)."
        )
        await status_msg.edit_text(
            f"❌ <b>Ошибка формата данных:</b>\n<code>{exc}</code>\n\n💡 {hint}"
        )
        return

    try:
        evaluation = await chess.analyze_position(fen)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chess analysis error: %s", exc)
        await status_msg.edit_text(
            f"❌ <b>Ошибка при анализе позиции:</b> <code>{type(exc).__name__}: {exc}</code>"
        )
        return

    await status_msg.edit_text(evaluation.format_for_telegram())
    await state.clear()
    logger.info("Chess analysis delivered for fen=%s", fen[:30])


# ── Navigation ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "chess:back")
async def handle_chess_back(
    callback: CallbackQuery, state: FSMContext, db: Database
) -> None:
    """Exit chess mode and return to main menu."""
    user_id = callback.from_user.id
    await state.clear()
    await db.set_user_mode(user_id, "chat")
    await callback.answer("Возврат в главное меню.")
    if callback.message:
        await callback.message.edit_text("🏠 <b>Возврат в главное меню.</b>")
        await callback.message.answer(
            text="Выберите действие:",
            reply_markup=main_menu_keyboard(),
        )
    logger.info("Chess mode exited for user_id=%d", user_id)
