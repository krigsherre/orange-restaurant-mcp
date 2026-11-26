"""Tool-calling loop: model -> tools -> model until a final answer."""

from __future__ import annotations

import json
from typing import Any

from .llm import ChatLLM
from .tools import TOOL_SCHEMAS, ChatTools


class ChatAgent:
    def __init__(
        self,
        llm: ChatLLM,
        tools: ChatTools,
        system_prompt: str,
        *,
        max_steps: int = 6,
        max_messages: int = 24,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.max_messages = max_messages
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    def say(self, text: str) -> str:
        self.messages.append({"role": "user", "content": text})
        for _ in range(self.max_steps):
            message = self.llm.complete(self.messages, tools=TOOL_SCHEMAS)
            self.messages.append(self._normalize(message))
            calls = message.get("tool_calls") or []
            if not calls:
                return (message.get("content") or "").strip() or "(no response)"
            for index, call in enumerate(calls):
                self.messages.append(self._run_tool(call, index))
            self._trim()
        return "I hit my tool-call limit for that request. Try narrowing it — for example a cuisine or a time window."

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _normalize(message: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content") or "",
        }
        if message.get("tool_calls"):
            clean["tool_calls"] = message["tool_calls"]
        return clean

    def _run_tool(self, call: dict[str, Any], index: int) -> dict[str, Any]:
        function = call.get("function") or {}
        name = function.get("name") or ""
        raw = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            result: dict[str, Any] = {"error": "tool arguments were not valid JSON"}
        else:
            result = self.tools.dispatch(name, arguments)
        return {
            "role": "tool",
            "tool_call_id": call.get("id") or f"call_{index}",
            "content": json.dumps(result, ensure_ascii=False, default=str),
        }

    def _trim(self) -> None:
        """Drop old turns without orphaning a tool result from its assistant call."""
        if len(self.messages) <= self.max_messages:
            return
        floor = len(self.messages) - self.max_messages
        start = next(
            (i for i in range(floor, len(self.messages)) if self.messages[i].get("role") == "user"),
            None,
        )
        if start is not None:
            self.messages = [self.messages[0]] + self.messages[start:]
