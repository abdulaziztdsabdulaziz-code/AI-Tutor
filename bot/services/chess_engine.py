"""
Chess engine service — position evaluation and strategic analysis via Stockfish & python-chess.

Architecture:
- High-level async interface for chess position parsing and analysis.
- MultiPV Stockfish UCI integration for top-3 winning lines and centipawn scoring.
- Graceful Built-in Fallback: If Stockfish executable is absent on the host environment,
  the service automatically switches to a Python-chess positional evaluator with
  material and piece-square table (PST) heuristics, guaranteeing zero crashes during
  live defense and demonstrations.
- Strategic move explanations: tactical annotations (check, capture, castle, promotion,
  piece development, center control).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass

import chess
import chess.engine

logger = logging.getLogger(__name__)

# ── Centipawn Advantage Thresholds ────────────────────────────────────────────

_CENTIPAWN_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (500, "⚔️ Решающее преимущество"),
    (200, "📈 Значительное преимущество"),
    (80, "➕ Небольшое преимущество"),
    (25, "⚖️ Примерное равенство"),
    (0, "⚖️ Равная позиция"),
)

# ── Piece-Square Tables (Midgame Heuristics for Fallback Engine) ───────────────

PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000,
}

PST_PAWN: list[int] = [
    0, 0, 0, 0, 0, 0, 0, 0,
    50, 50, 50, 50, 50, 50, 50, 50,
    10, 10, 20, 30, 30, 20, 10, 10,
    5, 5, 10, 25, 25, 10, 5, 5,
    0, 0, 0, 20, 20, 0, 0, 0,
    5, -5, -10, 0, 0, -10, -5, 5,
    5, 10, 10, -20, -20, 10, 10, 5,
    0, 0, 0, 0, 0, 0, 0, 0,
]

PST_KNIGHT: list[int] = [
    -50, -40, -30, -30, -30, -30, -40, -50,
    -40, -20, 0, 0, 0, 0, -20, -40,
    -30, 0, 10, 15, 15, 10, 0, -30,
    -30, 5, 15, 20, 20, 15, 5, -30,
    -30, 0, 15, 20, 20, 15, 0, -30,
    -30, 5, 10, 15, 15, 10, 5, -30,
    -40, -20, 0, 5, 5, 0, -20, -40,
    -50, -40, -30, -30, -30, -30, -40, -50,
]


@dataclass
class MoveAnalysis:
    """Evaluation metrics for an individual candidate continuation move."""

    move_uci: str
    move_san: str
    score_cp: int | None
    mate_in: int | None
    principal_variation: list[str]
    description: str


@dataclass
class PositionEvaluation:
    """Consolidated assessment of a chess board state with top candidate lines."""

    fen: str
    top_moves: list[MoveAnalysis]
    overall_assessment: str
    turn: str
    engine_name: str

    def format_for_telegram(self) -> str:
        """
        Format position analysis into rich HTML output for Telegram.
        """
        lines: list[str] = [
            "♟ <b>Шахматный анализ позиции</b>",
            f"⚙️ <i>Движок: {self.engine_name}</i>\n",
            f"🎯 <b>Очередь хода:</b> {self.turn}",
            f"📊 <b>Оценка:</b> {self.overall_assessment}\n",
            "<b>🏆 Топ-3 сильнейших продолжения:</b>",
        ]

        medals = ["🥇", "🥈", "🥉"]
        for idx, move in enumerate(self.top_moves):
            medal = medals[idx] if idx < 3 else f"{idx + 1}."
            if move.mate_in is not None:
                score_str = f"#{'+' if move.mate_in > 0 else ''}{move.mate_in}"
            elif move.score_cp is not None:
                cp = move.score_cp / 100.0
                sign = "+" if cp > 0 else ""
                score_str = f"{sign}{cp:.2f}"
            else:
                score_str = "0.00"

            pv_preview = " ".join(move.principal_variation[:5])
            lines.append(
                f"\n{medal} <b>{move.move_san}</b> (оценка: <code>{score_str}</code>)\n"
                f"   📌 <i>{move.description}</i>\n"
                f"   🔮 <b>Линия:</b> <code>{pv_preview}</code>"
            )

        lines.extend([
            "\n📋 <b>FEN позиции:</b>",
            f"<code>{self.fen}</code>",
        ])
        return "\n".join(lines)


class ChessEngine:
    """
    Chess engine service supporting Stockfish UCI with automated python-chess fallback.
    """

    def __init__(
        self,
        stockfish_path: str = "stockfish",
        depth: int = 18,
        num_lines: int = 3,
    ) -> None:
        self._stockfish_path = stockfish_path
        self._depth = depth
        self._num_lines = num_lines
        self._resolved_engine_path: str | None = self._resolve_stockfish_binary()

    def _resolve_stockfish_binary(self) -> str | None:
        """Locate valid Stockfish binary on filesystem or PATH."""
        candidates = [
            self._stockfish_path,
            shutil.which("stockfish"),
            shutil.which("stockfish.exe"),
            "/usr/bin/stockfish",
            "/usr/local/bin/stockfish",
            "/usr/games/stockfish",
            r"C:\stockfish\stockfish.exe",
            r"C:\Program Files\Stockfish\stockfish.exe",
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    # ── Public API ────────────────────────────────────────────────────────────

    async def analyze_position(self, fen: str) -> PositionEvaluation:
        """
        Analyze a FEN position and return structured top-3 moves and strategic verdict.
        Runs in background thread to keep event loop responsive.
        """
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise ValueError(f"Некорректная FEN-нотация: {exc}") from exc

        return await asyncio.to_thread(self._sync_analyze, board)

    async def parse_moves_to_fen(self, moves_input: str) -> str:
        """
        Convert SAN/PGN moves or bare FEN to validated FEN string.
        """
        stripped = moves_input.strip()
        if self._looks_like_fen(stripped):
            try:
                chess.Board(stripped)
                return stripped
            except ValueError as exc:
                raise ValueError(f"Некорректный FEN: {exc}") from exc

        return await asyncio.to_thread(self._sync_parse_moves, stripped)

    # ── Internal Synchronous Execution ────────────────────────────────────────

    def _sync_analyze(self, board: chess.Board) -> PositionEvaluation:
        """Execute analysis using Stockfish or fallback evaluator."""
        # Try Stockfish first if available
        if self._resolved_engine_path:
            try:
                return self._analyze_with_stockfish(board, self._resolved_engine_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Stockfish engine invocation failed (%s); reverting to built-in fallback.",
                    exc,
                )

        # Fallback to python-chess internal heuristic engine
        return self._analyze_with_heuristic_fallback(board)

    def _analyze_with_stockfish(
        self, board: chess.Board, engine_path: str
    ) -> PositionEvaluation:
        """Run Stockfish MultiPV analysis via python-chess UCI interface."""
        with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
            limit = chess.engine.Limit(depth=self._depth)
            info_list = engine.analyse(board, limit, multipv=self._num_lines)

        top_moves: list[MoveAnalysis] = []
        for info in info_list:
            pv: list[chess.Move] = info.get("pv", [])
            if not pv:
                continue

            move = pv[0]
            move_san = board.san(move)
            move_uci = move.uci()
            score: chess.engine.Score = info["score"].white()

            score_cp: int | None = None
            mate_in: int | None = None
            if score.is_mate():
                mate_in = score.mate()
            else:
                score_cp = score.score()

            # Construct SAN continuation
            pv_san: list[str] = []
            temp_board = board.copy()
            for pv_move in pv[:6]:
                try:
                    pv_san.append(temp_board.san(pv_move))
                    temp_board.push(pv_move)
                except Exception:
                    break

            description = self._generate_move_description(board, move)
            top_moves.append(
                MoveAnalysis(
                    move_uci=move_uci,
                    move_san=move_san,
                    score_cp=score_cp,
                    mate_in=mate_in,
                    principal_variation=pv_san,
                    description=description,
                )
            )

        turn_str = "Белые" if board.turn == chess.WHITE else "Чёрные"
        if top_moves:
            best = top_moves[0]
            if best.mate_in is not None:
                winner = "Белые" if best.mate_in > 0 else "Чёрные"
                overall = f"🏁 Форсированный мат в {abs(best.mate_in)} ход(а) ({winner})"
            else:
                overall = self._score_to_assessment(info_list[0]["score"].white())
        else:
            overall = "⚖️ Позиция не определена"

        return PositionEvaluation(
            fen=board.fen(),
            top_moves=top_moves,
            overall_assessment=overall,
            turn=turn_str,
            engine_name="Stockfish 16 (UCI Engine)",
        )

    def _analyze_with_heuristic_fallback(
        self, board: chess.Board
    ) -> PositionEvaluation:
        """
        Pure Python-chess heuristic evaluator.
        Evaluates legal moves with material, positional PST, checks, captures, and mobility.
        """
        legal_moves = list(board.legal_moves)
        turn_str = "Белые" if board.turn == chess.WHITE else "Чёрные"

        if not legal_moves:
            if board.is_checkmate():
                winner = "Чёрные" if board.turn == chess.WHITE else "Белые"
                overall = f"🏁 Шах и мат! {winner} победили."
            elif board.is_stalemate():
                overall = "⚖️ Пат — ничья."
            else:
                overall = "⚖️ Ничья."
            return PositionEvaluation(
                fen=board.fen(),
                top_moves=[],
                overall_assessment=overall,
                turn=turn_str,
                engine_name="Python-Chess Heuristic Engine",
            )

        scored_moves: list[tuple[int, chess.Move, list[str]]] = []

        for move in legal_moves:
            board.push(move)
            # 1-ply search with static evaluation
            val = self._evaluate_board_static(board)
            pv_san = [board.san(m) for m in list(board.legal_moves)[:3]]
            board.pop()

            # Higher is better for current player
            relative_score = val if board.turn == chess.WHITE else -val
            scored_moves.append((relative_score, move, pv_san))

        # Sort descending by score
        scored_moves.sort(key=lambda x: x[0], reverse=True)

        top_moves: list[MoveAnalysis] = []
        for rel_score, move, pv_san in scored_moves[: self._num_lines]:
            # Convert to White's perspective centipawns
            white_score = rel_score if board.turn == chess.WHITE else -rel_score
            san_move = board.san(move)
            desc = self._generate_move_description(board, move)

            top_moves.append(
                MoveAnalysis(
                    move_uci=move.uci(),
                    move_san=san_move,
                    score_cp=white_score,
                    mate_in=None,
                    principal_variation=[san_move] + pv_san,
                    description=desc,
                )
            )

        overall = self._centipawn_to_text(top_moves[0].score_cp or 0)

        return PositionEvaluation(
            fen=board.fen(),
            top_moves=top_moves,
            overall_assessment=overall,
            turn=turn_str,
            engine_name="Python-Chess Heuristic Engine (Fallback Mode)",
        )

    def _evaluate_board_static(self, board: chess.Board) -> int:
        """Calculate static centipawn evaluation from White's perspective."""
        if board.is_checkmate():
            return -20000 if board.turn == chess.WHITE else 20000
        if board.is_stalemate() or board.is_insufficient_material():
            return 0

        score = 0
        for sq in chess.SQUARES:
            piece = board.piece_at(sq)
            if not piece:
                continue

            base_val = PIECE_VALUES.get(piece.piece_type, 0)
            pst_val = 0
            if piece.piece_type == chess.PAWN:
                pst_val = PST_PAWN[sq if piece.color == chess.WHITE else chess.square_mirror(sq)]
            elif piece.piece_type == chess.KNIGHT:
                pst_val = PST_KNIGHT[sq if piece.color == chess.WHITE else chess.square_mirror(sq)]

            total = base_val + pst_val
            score += total if piece.color == chess.WHITE else -total

        # Mobility bonus
        score += 5 * len(list(board.legal_moves)) * (1 if board.turn == chess.WHITE else -1)
        return score

    def _generate_move_description(self, board: chess.Board, move: chess.Move) -> str:
        """Generate human-readable tactical and strategic annotation."""
        board_copy = board.copy()
        is_capture = board_copy.is_capture(move)
        is_castling = board_copy.is_castling(move)
        is_en_passant = board_copy.is_en_passant(move)
        is_promotion = move.promotion is not None

        board_copy.push(move)
        is_check = board_copy.is_check()
        is_mate = board_copy.is_checkmate()

        if is_mate:
            return "☠️ Форсированный мат сопернику"
        if is_check and is_capture:
            return "⚡ Шах с взятием вражеского материала"
        if is_check:
            return "✚ Острый шах неприятельскому королю"
        if is_castling:
            return "🏰 Рокировка: эвакуация короля и соединение ладей"
        if is_promotion:
            promoted_name = {
                chess.QUEEN: "Ферзя",
                chess.ROOK: "Ладью",
                chess.BISHOP: "Слона",
                chess.KNIGHT: "Коня",
            }.get(move.promotion, "фигуру")
            return f"👑 Проведение пешки в {promoted_name}"
        if is_en_passant:
            return "🌪️ Взятие на проходе (en passant)"
        if is_capture:
            captured = board.piece_at(move.to_square)
            piece_name = "материала"
            if captured:
                names = {
                    chess.PAWN: "пешки",
                    chess.KNIGHT: "коня",
                    chess.BISHOP: "слона",
                    chess.ROOK: "ладьи",
                    chess.QUEEN: "ферзя",
                }
                piece_name = names.get(captured.piece_type, "материала")
            return f"⚔️ Тактическое взятие {piece_name}"

        piece = board.piece_at(move.from_square)
        if piece:
            if piece.piece_type == chess.PAWN:
                if move.to_square in (chess.D4, chess.E4, chess.D5, chess.E5):
                    return "⬆️ Захват центра пешечным клином"
                return "⬆️ Продвижение пешечной структуры"
            if piece.piece_type == chess.KNIGHT:
                return "🚀 Развитие коня на активную позицию"
            if piece.piece_type == chess.BISHOP:
                return "🏹 Активизация слона по диагонали"
            if piece.piece_type == chess.ROOK:
                return "🔄 Перевод ладьи на открытую вертикаль"
            if piece.piece_type == chess.QUEEN:
                return "👸 Централизация сильнейшей фигуры"
            if piece.piece_type == chess.KING:
                return "🛡️ Профилактический ход королём"

        return "🎯 Стратегический позиционный манёвр"

    def _sync_parse_moves(self, moves_text: str) -> str:
        """Parse moves from PGN / SAN string and return final FEN."""
        cleaned = re.sub(r"\d+\.", " ", moves_text)
        tokens = [t.strip() for t in cleaned.split() if t.strip()]

        board = chess.Board()
        for token in tokens:
            if token in {"1-0", "0-1", "1/2-1/2", "*"}:
                continue
            try:
                board.push_san(token)
            except (chess.InvalidMoveError, chess.IllegalMoveError, ValueError) as exc:
                raise ValueError(f"Невозможно применить ход '{token}': {exc}") from exc

        return board.fen()

    def _score_to_assessment(self, score: chess.engine.Score) -> str:
        """Convert engine Score to localized description."""
        if score.is_mate():
            m = score.mate()
            if m is None:
                return "⚖️ Равная позиция"
            winner = "Белые" if m > 0 else "Чёрные"
            return f"🏁 Мат в {abs(m)} ход(а) — {winner}"
        cp = score.score() or 0
        return self._centipawn_to_text(cp)

    @staticmethod
    def _centipawn_to_text(cp: int) -> str:
        """Translate centipawn score to readable string."""
        for threshold, label in _CENTIPAWN_THRESHOLDS:
            if cp >= threshold:
                side = "Белые" if cp > 25 else ""
                return f"{label}{' — ' + side if side else ''}"
            if cp <= -threshold and threshold != 0:
                return f"{label} — Чёрные"
        return "⚖️ Равная позиция"

    @staticmethod
    def _looks_like_fen(text: str) -> bool:
        """Detect if input matches FEN structure."""
        parts = text.split()
        if len(parts) < 4:
            return False
        return "/" in parts[0] and bool(re.search(r"\d", parts[0]))
