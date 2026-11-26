"""Minimal MCP (JSON-RPC 2.0) streamable-HTTP client — no third-party deps."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class McpError(RuntimeError):
    pass


class McpHttpClient:
    """Talks to an MCP server over streamable HTTP (the transport Swiggy documents).

    Auth is session-scoped: pass the OAuth access token once, the server keeps the
    session. Set ``token`` from the environment; never hard-code it.
    """

    def __init__(self, endpoint: str, token: str | None = None, timeout: float = 30.0) -> None:
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 0

    # ------------------------------------------------------------------ transport
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise McpError(f"{method} failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise McpError(f"{method} failed: {exc.reason}") from exc

        message = self._decode(body)
        if "error" in message:
            raise McpError(f"{method} error: {message['error']}")
        return message.get("result")

    @staticmethod
    def _decode(body: str) -> dict[str, Any]:
        body = body.strip()
        if not body:
            return {}
        if body.startswith("{"):
            return json.loads(body)
        # SSE framing: take the last complete `data:` line.
        data_lines = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        if not data_lines:
            raise McpError("unparseable MCP response")
        return json.loads(data_lines[-1])

    # ------------------------------------------------------------------ API
    def initialize(self) -> dict[str, Any]:
        return self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "orange-ranker", "version": "0.1.0"},
            },
        )

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list", {})
        return (result or {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result is None:
            raise McpError(f"{name}: empty result")
        if result.get("isError"):
            raise McpError(f"{name}: {result.get('content')}")
        return result.get("structuredContent", result)


def extract_envelope(payload: Any) -> dict[str, Any]:
    """Providers wrap payloads as {success, data}; unwrap defensively."""
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload
