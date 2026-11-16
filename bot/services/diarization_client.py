"""
Deepgram diarization service — async voice transcription with smart speaker role mapping.

Features:
- Direct in-memory audio streaming (io.BytesIO) to Deepgram API without touching disk.
- Nova-3 / Nova-2 support with diarize=True, punctuate=True, language="ru".
- Smart Role Mapping: classifies raw speaker tags (speaker_0, speaker_1) into formatted
  roles (e.g. 'Мужчина_1', 'Женщина_1', 'Преподаватель', 'Студент') using Russian morphological
  verb/adjective markers with optional LLM contextual refinement.
- Message chunking ensuring Telegram 4096-character limit is respected without mid-word splits.
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from bot.services.llm_client import LLMRouter

logger = logging.getLogger(__name__)


# ── Morphological heuristics for Russian gender detection ─────────────────────

_MASCULINE_VERB_PATTERNS = re.compile(
    r"\b(?:я|мы)?\s*(?:был|сказал|подумал|сделал|хотел|решил|увидел|понял|"
    r"спросил|ответил|написал|проверил|заметил|пошел|пришел|смог|взял|"
    r"готов|уверен|согласен|рад|должен|прав|сам)\b",
    re.IGNORECASE,
)

_FEMININE_VERB_PATTERNS = re.compile(
    r"\b(?:я|мы)?\s*(?:была|сказала|подумала|сделала|хотела|решила|увидела|поняла|"
    r"спросила|ответила|написала|проверила|заметила|пошла|пришла|смогла|взяла|"
    r"готова|уверена|согласна|рада|должна|права|сама)\b",
    re.IGNORECASE,
)


@dataclass
class DiarizationSegment:
    """Single utterance attributed to a specific speaker with timestamps."""

    speaker_id: int
    text: str
    start: float
    end: float


class SpeakerRoleAnalyzer:
    """
    Analyzes transcribed speaker utterances to deduce logical roles and genders.

    Combines Russian grammatical/morphological agreement heuristics with optional
    LLM conversational context analysis.
    """

    @staticmethod
    def infer_roles_heuristically(
        segments: list[DiarizationSegment],
    ) -> dict[int, str]:
        """
        Deduce roles using grammatical markers in speaker utterances.

        Returns mapping from speaker_id -> formatted role string (e.g. 'Мужчина_1', 'Женщина_1').
        """
        # Aggregate text per speaker
        speaker_texts: dict[int, list[str]] = {}
        for seg in segments:
            speaker_texts.setdefault(seg.speaker_id, []).append(seg.text)

        role_mapping: dict[int, str] = {}
        male_idx = 1
        female_idx = 1
        neutral_idx = 1

        for spk_id, texts in sorted(speaker_texts.items()):
            full_text = " ".join(texts)
            male_matches = len(_MASCULINE_VERB_PATTERNS.findall(full_text))
            female_matches = len(_FEMININE_VERB_PATTERNS.findall(full_text))

            if male_matches > female_matches:
                role_mapping[spk_id] = f"Мужчина_{male_idx}"
                male_idx += 1
            elif female_matches > male_matches:
                role_mapping[spk_id] = f"Женщина_{female_idx}"
                female_idx += 1
            else:
                role_mapping[spk_id] = f"Собеседник_{neutral_idx}"
                neutral_idx += 1

        return role_mapping

    @staticmethod
    async def infer_roles_with_llm(
        segments: list[DiarizationSegment],
        llm: LLMRouter | None = None,
    ) -> dict[int, str]:
        """
        Use LLM or fallback heuristics to infer speaker roles from dialogue context.
        """
        fallback_map = SpeakerRoleAnalyzer.infer_roles_heuristically(segments)
        if llm is None or not segments:
            return fallback_map

        # Prepare compact dialogue snippet for LLM
        sample_lines: list[str] = []
        for s in segments[:12]:
            sample_lines.append(f"Speaker_{s.speaker_id}: {s.text}")
        dialogue_sample = "\n".join(sample_lines)

        prompt = (
            "Определи пол и контекстную роль каждого спикера (например: Мужчина_1, Женщина_1, "
            "Преподаватель, Студент) по репликам диалога. Верни ТОЛЬКО строго валидный JSON "
            'объект в формате {"0": "Мужчина_1", "1": "Женщина_1"}. Диалог:\n'
            f"{dialogue_sample}"
        )

        try:
            response = await llm.chat(
                model_key="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.1,
            )
            # Extract JSON block
            json_match = re.search(r"\{.*?\}", response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                llm_map: dict[int, str] = {}
                for k, v in parsed.items():
                    llm_map[int(k)] = str(v)
                # Ensure all speakers present
                for spk_id, fallback_role in fallback_map.items():
                    if spk_id not in llm_map:
                        llm_map[spk_id] = fallback_role
                return llm_map
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLM role inference fallback to heuristics: %s", exc)

        return fallback_map


@dataclass
class DiarizationResult:
    """
    Parsed Deepgram transcription containing speaker segments and metadata.
    """

    segments: list[DiarizationSegment]
    language: str
    duration: float

    def format_transcript(
        self, role_mapping: dict[int, str] | None = None
    ) -> str:
        """
        Merge consecutive same-speaker utterances and format with smart roles.

        Format:
        🗣 Спикер 1 (Мужчина_1) [00:00–00:15]: [text]
        🗣 Спикер 2 (Женщина_1) [00:16–00:30]: [text]
        """
        if not self.segments:
            return "🔇 <i>Транскрипт пуст — речь не обнаружена.</i>"

        if role_mapping is None:
            role_mapping = SpeakerRoleAnalyzer.infer_roles_heuristically(self.segments)

        # Merge consecutive utterances by the same speaker
        merged: list[DiarizationSegment] = []
        for seg in self.segments:
            if merged and merged[-1].speaker_id == seg.speaker_id:
                merged[-1] = DiarizationSegment(
                    speaker_id=merged[-1].speaker_id,
                    text=f"{merged[-1].text.rstrip()} {seg.text.lstrip()}",
                    start=merged[-1].start,
                    end=seg.end,
                )
            else:
                merged.append(
                    DiarizationSegment(
                        speaker_id=seg.speaker_id,
                        text=seg.text,
                        start=seg.start,
                        end=seg.end,
                    )
                )

        lines: list[str] = []
        for seg in merged:
            spk_num = seg.speaker_id + 1
            role_label = role_mapping.get(seg.speaker_id, f"Спикер_{spk_num}")
            timestamp = f"[{self._fmt_time(seg.start)}–{self._fmt_time(seg.end)}]"
            lines.append(
                f"🗣 <b>Спикер {spk_num} ({role_label})</b> <i>{timestamp}</i>:\n{seg.text.strip()}"
            )

        header = (
            f"🎙 <b>Транскрипт с диаризацией и ролями</b>\n"
            f"🌐 <b>Язык:</b> {self.language.upper()} | ⏱ <b>Длительность:</b> {self._fmt_time(self.duration)}\n"
            + "─" * 34 + "\n\n"
        )
        return header + "\n\n".join(lines)

    def to_telegram_chunks(
        self,
        max_len: int = 4096,
        role_mapping: dict[int, str] | None = None,
    ) -> list[str]:
        """
        Split formatted transcript into Telegram-safe chunks (<= 4096 characters)
        without mid-word breaks.
        """
        text = self.format_transcript(role_mapping=role_mapping)
        if len(text) <= max_len:
            return [text]

        chunks: list[str] = []
        while len(text) > max_len:
            # Try splitting by double newline (paragraph boundary)
            split_pos = text.rfind("\n\n", 0, max_len)
            if split_pos == -1 or split_pos < max_len // 2:
                # Try single newline
                split_pos = text.rfind("\n", 0, max_len)
            if split_pos == -1 or split_pos < max_len // 2:
                # Try space
                split_pos = text.rfind(" ", 0, max_len)
            if split_pos == -1:
                split_pos = max_len

            chunks.append(text[:split_pos].strip())
            text = text[split_pos:].lstrip()

        if text.strip():
            chunks.append(text.strip())

        return chunks

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        """Format seconds into MM:SS string."""
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes:02d}:{secs:02d}"


class DiarizationClient:
    """
    Async Deepgram API client for audio transcription with speaker diarization.
    """

    BASE_URL: str = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._http: httpx.AsyncClient = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
        )

    async def transcribe(
        self,
        audio_data: io.BytesIO,
        language: str = "ru",
        model: str = "nova-3",
    ) -> DiarizationResult:
        """
        Transcribe in-memory audio bytes using Deepgram with speaker diarization.

        Parameters
        ----------
        audio_data : io.BytesIO
            In-memory audio stream.
        language : str
            BCP-47 language tag (default 'ru').
        model : str
            Deepgram speech model ('nova-3' with auto-fallback to 'nova-2').

        Returns
        -------
        DiarizationResult
            Parsed speech segments with speaker attribution and timestamps.
        """
        payload = audio_data.read() if hasattr(audio_data, "read") else bytes(audio_data)

        params: dict[str, str] = {
            "model": model,
            "language": language,
            "diarize": "true",
            "punctuate": "true",
            "utterances": "true",
            "smart_format": "true",
        }
        headers: dict[str, str] = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "audio/*",
        }

        logger.info(
            "Transcribing %.1f KB audio with Deepgram model=%s, lang=%s",
            len(payload) / 1024,
            model,
            language,
        )

        try:
            response = await self._http.post(
                self.BASE_URL,
                params=params,
                headers=headers,
                content=payload,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return self._parse_response(data)
        except httpx.HTTPStatusError as exc:
            # If nova-3 is unavailable on tier, fallback to nova-2
            if model == "nova-3" and exc.response.status_code in (400, 404):
                logger.warning("Deepgram nova-3 unavailable; falling back to nova-2.")
                return await self.transcribe(
                    audio_data=io.BytesIO(payload),
                    language=language,
                    model="nova-2",
                )
            raise

    def _parse_response(self, data: dict[str, Any]) -> DiarizationResult:
        """Parse Deepgram JSON output into structured DiarizationResult."""
        metadata: dict[str, Any] = data.get("metadata", {})
        duration: float = float(metadata.get("duration", 0.0))
        language: str = str(metadata.get("language", "ru") or "ru")

        results: dict[str, Any] = data.get("results", {})
        utterances: list[dict[str, Any]] | None = results.get("utterances")
        segments: list[DiarizationSegment] = []

        if utterances:
            for utt in utterances:
                speaker_id: int = int(utt.get("speaker", 0))
                text: str = str(utt.get("transcript", "")).strip()
                start: float = float(utt.get("start", 0.0))
                end: float = float(utt.get("end", 0.0))
                if text:
                    segments.append(
                        DiarizationSegment(
                            speaker_id=speaker_id,
                            text=text,
                            start=start,
                            end=end,
                        )
                    )
        else:
            # Word-level fallback parsing
            channels = results.get("channels", [])
            if channels:
                words = channels[0].get("alternatives", [{}])[0].get("words", [])
                curr_speaker: int | None = None
                curr_words: list[str] = []
                seg_start: float = 0.0
                seg_end: float = 0.0

                for word_obj in words:
                    spk = int(word_obj.get("speaker", 0))
                    w_text = str(word_obj.get("punctuated_word") or word_obj.get("word", ""))
                    w_start = float(word_obj.get("start", 0.0))
                    w_end = float(word_obj.get("end", 0.0))

                    if curr_speaker is None:
                        curr_speaker = spk
                        seg_start = w_start

                    if spk != curr_speaker:
                        if curr_words and curr_speaker is not None:
                            segments.append(
                                DiarizationSegment(
                                    speaker_id=curr_speaker,
                                    text=" ".join(curr_words),
                                    start=seg_start,
                                    end=seg_end,
                                )
                            )
                        curr_speaker = spk
                        curr_words = []
                        seg_start = w_start

                    curr_words.append(w_text)
                    seg_end = w_end

                if curr_words and curr_speaker is not None:
                    segments.append(
                        DiarizationSegment(
                            speaker_id=curr_speaker,
                            text=" ".join(curr_words),
                            start=seg_start,
                            end=seg_end,
                        )
                    )

        return DiarizationResult(segments=segments, language=language, duration=duration)

    async def aclose(self) -> None:
        """Close internal HTTP client session."""
        await self._http.aclose()
