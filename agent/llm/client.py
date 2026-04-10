"""
LLM Client — unified interface for Anthropic and Ollama.

Both providers expose the same complete() method.
The rest of the system never imports anthropic or ollama directly.

Usage:
    client = LLMClient.from_env()
    response = client.complete(system_prompt, user_prompt)

Design note: If the LLM is unavailable, complete() raises LLMError.
The caller decides whether to abort or return a partial (deterministic-only) report.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


# ── Exceptions ────────────────────────────────────────────────────────────────

class LLMError(Exception):
    """Raised when the LLM call fails for any reason."""


# ── Base ─────────────────────────────────────────────────────────────────────

class BaseLLMClient(ABC):
    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """
        Send a prompt to the LLM and return the text response.
        Raises LLMError on failure.
        """


# ── Anthropic ─────────────────────────────────────────────────────────────────

class AnthropicClient(BaseLLMClient):
    """
    Anthropic Claude client.
    Requires ANTHROPIC_API_KEY in environment.
    """

    DEFAULT_MODEL = "claude-haiku-4-5-20251001"
    MAX_TOKENS = 2048

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
            self._model = model
        except ImportError as e:
            raise LLMError("anthropic package not installed. Run: pip install anthropic") from e

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            logger.debug("Calling Anthropic model: %s", self._model)
            message = self._client.messages.create(
                model=self._model,
                max_tokens=self.MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return message.content[0].text
        except Exception as e:
            raise LLMError(f"Anthropic API call failed: {e}") from e


# ── Ollama ────────────────────────────────────────────────────────────────────

class OllamaClient(BaseLLMClient):
    """
    Ollama local LLM client.
    Requires Ollama running locally or at OLLAMA_BASE_URL.
    Default model: llama3
    """

    DEFAULT_MODEL = "llama3"
    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL):
        self._base_url = base_url.rstrip("/")
        self._model = model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            import httpx
        except ImportError as e:
            raise LLMError("httpx package not installed. Run: pip install httpx") from e

        payload = {
            "model": self._model,
            "prompt": f"<system>\n{system_prompt}\n</system>\n\n{user_prompt}",
            "stream": False,
            "options": {"temperature": 0.2},
        }

        try:
            logger.debug("Calling Ollama model: %s at %s", self._model, self._base_url)
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            return response.json()["response"]
        except Exception as e:
            raise LLMError(f"Ollama call failed: {e}") from e


# ── Factory ───────────────────────────────────────────────────────────────────

class LLMClient:
    """
    Factory — reads LLM_PROVIDER from environment and returns
    the appropriate client.

    Usage:
        client = LLMClient.from_env()
    """

    @staticmethod
    def from_env() -> BaseLLMClient:
        provider = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()

        if provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise LLMError(
                    "ANTHROPIC_API_KEY is not set. "
                    "Add it to your .env file or set LLM_PROVIDER=ollama to use local model."
                )
            model = os.getenv("ANTHROPIC_MODEL", AnthropicClient.DEFAULT_MODEL)
            logger.info("LLM provider: Anthropic (%s)", model)
            return AnthropicClient(api_key=api_key, model=model)

        elif provider == "ollama":
            base_url = os.getenv("OLLAMA_BASE_URL", OllamaClient.DEFAULT_BASE_URL)
            model = os.getenv("OLLAMA_MODEL", OllamaClient.DEFAULT_MODEL)
            logger.info("LLM provider: Ollama (%s at %s)", model, base_url)
            return OllamaClient(base_url=base_url, model=model)

        else:
            raise LLMError(
                f"Unknown LLM_PROVIDER: '{provider}'. "
                f"Valid options: 'anthropic', 'ollama'"
            )