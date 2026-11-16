"""
Application services package:
- LLMRouter (OpenRouter multi-model integration with automated fallback)
- DiarizationClient & SpeakerRoleAnalyzer (Deepgram voice transcription & smart role mapping)
- ChessEngine (Stockfish UCI + Python-chess positional heuristic evaluator)
"""
from bot.services.chess_engine import ChessEngine, PositionEvaluation
from bot.services.diarization_client import (
    DiarizationClient,
    DiarizationResult,
    DiarizationSegment,
    SpeakerRoleAnalyzer,
)
from bot.services.llm_client import LLMRouter, MODEL_REGISTRY, ModelConfig

__all__: list[str] = [
    "ChessEngine",
    "DiarizationClient",
    "DiarizationResult",
    "DiarizationSegment",
    "LLMRouter",
    "MODEL_REGISTRY",
    "ModelConfig",
    "PositionEvaluation",
    "SpeakerRoleAnalyzer",
]
