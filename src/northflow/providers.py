"""Провайдеры LLM: один OpenAI-совместимый клиент."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResponse:
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: dict | None = None


class LLMClient:
    """Минимальный async-клиент для /chat/completions (OpenAI-совместимый)."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.3,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        resp = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        tool_calls = None
        raw_calls = msg.get("tool_calls")
        if raw_calls:
            tool_calls = []
            for i, tc in enumerate(raw_calls):
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                try:
                    parsed = json.loads(args) if isinstance(args, str) else (args or {})
                except json.JSONDecodeError:
                    parsed = {}
                tool_calls.append(ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=fn.get("name", ""),
                    arguments=parsed if isinstance(parsed, dict) else {},
                ))
        return ChatResponse(
            content=msg.get("content"),
            tool_calls=tool_calls,
            usage=data.get("usage"),
        )

    async def close(self) -> None:
        try:
            await self.client.aclose()
        except Exception:
            pass
