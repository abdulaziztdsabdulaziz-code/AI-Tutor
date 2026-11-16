"""
LLM Router service — OpenRouter multi-model client with automatic fallback.

Provides an async interface to top-tier AI models via the OpenRouter gateway.
On rate-limit (HTTP 429), timeout, network failure, or upstream 5xx errors,
the router transparently falls back to a secondary model and prepends a clear
notification to the user's response.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import AsyncIterator

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelConfig:
    """Configuration metadata for an LLM accessible via OpenRouter."""

    key: str
    model_id: str
    display_name: str
    emoji: str
    description: str
    fallback_key: str | None = None


# ── Model Registry ────────────────────────────────────────────────────────────

MODEL_REGISTRY: dict[str, ModelConfig] = {
    "claude-3-7-sonnet": ModelConfig(
        key="claude-3-7-sonnet",
        model_id="anthropic/claude-3.7-sonnet",
        display_name="Claude 3.7 Sonnet",
        emoji="🧠",
        description="Deep code analysis, architecture & tutoring logic",
        fallback_key="gpt-4o",
    ),
    "gpt-4o": ModelConfig(
        key="gpt-4o",
        model_id="openai/gpt-4o",
        display_name="GPT-4o",
        emoji="🤖",
        description="General multimodal intelligence",
        fallback_key="gemini-2-5-pro",
    ),
    "gemini-2-5-pro": ModelConfig(
        key="gemini-2-5-pro",
        model_id="google/gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        emoji="✨",
        description="Large context processing & reasoning",
        fallback_key="claude-3-7-sonnet",
    ),
    "deepseek-r1": ModelConfig(
        key="deepseek-r1",
        model_id="deepseek/deepseek-r1",
        display_name="DeepSeek R1 / V3",
        emoji="🔢",
        description="Advanced mathematics & step-by-step logic",
        fallback_key="gpt-4o",
    ),
    "grok": ModelConfig(
        key="grok",
        model_id="x-ai/grok-2-1212",
        display_name="Grok",
        emoji="⚡",
        description="Creativity & real-time context",
        fallback_key="gpt-4o",
    ),
}

DEFAULT_SYSTEM_PROMPT = (
    "Ты — умный AI-помощник и наставник университетского уровня. "
    "Отвечай чётко, структурированно, на языке пользователя (русский / английский)."
)


class LLMRouter:
    """
    Async Multi-Model Router communicating via OpenRouter API.
    Supports transparent automated model fallback.
    """

    def __init__(self, api_key: str) -> None:
        self._client: AsyncOpenAI = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=60.0,
            default_headers={
                "HTTP-Referer": "https://github.com/antigravity-ai-tutor",
                "X-Title": "AI-Tutor Antigravity Edition",
            },
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def chat(
        self,
        model_key: str,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """
        Execute chat completion with automatic fallback on failure.

        Parameters
        ----------
        model_key : str
            Identifier from MODEL_REGISTRY.
        messages : list[dict[str, str]]
            History sequence of role and content dicts.
        system_prompt : str | None
            System directive prepended to message payload.
        max_tokens : int
            Token generation ceiling.
        temperature : float
            Sampling randomness parameter.

        Returns
        -------
        str
            Model's response text (prefixed with fallback indicator if applicable).
        """
        cfg = self.get_model_config(model_key)
        full_messages = self._build_messages(messages, system_prompt)

        try:
            return await self._do_request(
                model_id=cfg.model_id,
                messages=full_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            logger.warning(
                "Primary model '%s' failed due to %s: %s; initiating fallback.",
                cfg.model_id,
                type(exc).__name__,
                exc,
            )
            return await self._fallback(cfg, full_messages, max_tokens, temperature)
        except APIStatusError as exc:
            if exc.status_code >= 429:
                logger.warning(
                    "Primary model '%s' HTTP status error %d; initiating fallback.",
                    cfg.model_id,
                    exc.status_code,
                )
                return await self._fallback(cfg, full_messages, max_tokens, temperature)
            raise
        except APIError as exc:
            logger.warning(
                "Primary model '%s' API error %s; initiating fallback.",
                cfg.model_id,
                exc,
            )
            return await self._fallback(cfg, full_messages, max_tokens, temperature)

    async def astream(
        self,
        model_key: str,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """
        Stream response deltas from the selected model.
        """
        cfg = self.get_model_config(model_key)
        full_messages = self._build_messages(messages, system_prompt)

        stream = await self._client.chat.completions.create(
            model=cfg.model_id,
            messages=full_messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ── Static Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def get_model_config(key: str) -> ModelConfig:
        """Lookup ModelConfig by registry key or raise descriptive KeyError."""
        if key not in MODEL_REGISTRY:
            raise KeyError(
                f"Unknown model key: '{key}'. Available keys: {list(MODEL_REGISTRY.keys())}"
            )
        return MODEL_REGISTRY[key]

    @staticmethod
    def all_models() -> list[ModelConfig]:
        """Return list of all registered model configurations."""
        return list(MODEL_REGISTRY.values())

    # ── Internal Helpers ──────────────────────────────────────────────────────

    async def _do_request(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Execute single completion call against OpenRouter API."""
        response = await self._client.chat.completions.create(
            model=model_id,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.choices[0].message.content
        return content if content is not None else ""

    async def _fallback(
        self,
        primary_cfg: ModelConfig,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Attempt secondary fallback model upon primary model failure."""
        if primary_cfg.fallback_key is None:
            raise RuntimeError(
                f"Primary model '{primary_cfg.key}' failed and no fallback is registered."
            )

        fallback_cfg = self.get_model_config(primary_cfg.fallback_key)
        logger.info(
            "Executing fallback from '%s' to '%s'.",
            primary_cfg.model_id,
            fallback_cfg.model_id,
        )

        try:
            result = await self._do_request(
                model_id=fallback_cfg.model_id,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Both primary model '{primary_cfg.key}' and fallback model "
                f"'{fallback_cfg.key}' failed: {exc}"
            ) from exc

        notification = (
            f"[⚠️ <i>Переключился на {fallback_cfg.emoji} <b>{fallback_cfg.display_name}</b> "
            f"из-за временной недоступности основной модели</i>]\n\n"
        )
        return notification + result

    @staticmethod
    def _build_messages(
        messages: list[dict[str, str]],
        system_prompt: str | None,
    ) -> list[dict[str, str]]:
        """Prepend optional system prompt to conversation message list."""
        if system_prompt:
            return [{"role": "system", "content": system_prompt}] + list(messages)
        return list(messages)
