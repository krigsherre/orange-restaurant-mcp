"""Thin DeepSeek/OpenAI-compatible chat client with tool-calling support."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 60.0


class LLMError(RuntimeError):
    """The chat model could not be reached or returned something unusable."""


class ChatLLM:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        temperature: float = 0.2,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise LLMError(
                "DEEPSEEK_API_KEY is not set (checked the environment and .env); "
                "the chat interface needs it because the conversation itself is model-driven."
            )
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        try:
            self.timeout = float(timeout if timeout is not None else os.environ.get("DEEPSEEK_TIMEOUT") or DEFAULT_TIMEOUT)
        except ValueError as exc:
            raise LLMError(f"invalid DEEPSEEK_TIMEOUT: {exc}") from exc
        self.temperature = temperature

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"chat API HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LLMError(f"chat API unreachable: {exc}") from exc

        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"chat API returned an unexpected payload: {exc}") from exc
        if not isinstance(message, dict):
            raise LLMError("chat API returned a non-object message")
        message.setdefault("role", "assistant")
        message.setdefault("content", "")
        return message
