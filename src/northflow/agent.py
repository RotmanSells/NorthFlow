"""Агентский цикл: инструменты, лимиты запросов/времени, output-first."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from .config import RoleConfig
from .providers import LLMClient
from .tools import ToolExecutor


class AgentRun:
    """Выполнение одной роли до finish / лимита."""

    def __init__(
        self,
        client: LLMClient,
        role: RoleConfig,
        root: Path,
        system_prompt: str,
        user_message: str,
        tools: ToolExecutor | None = None,
    ):
        self.client = client
        self.role = role
        self.root = root
        self.system_prompt = system_prompt
        self.user_message = user_message
        self.tools = tools or ToolExecutor(root, role=role.name, allowed_paths=role.allowed_paths)
        self.requests = 0
        self.total_tokens = 0
        self.started_at = time.monotonic()
        self.log: list[dict] = []

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def _check_limits(self) -> str | None:
        if self.requests >= self.role.budget.max_requests:
            return f"PARTIAL: role request limit reached ({self.requests} requests) with result so far."
        if self.elapsed >= self.role.budget.max_seconds:
            return f"PARTIAL: role time limit reached ({int(self.elapsed)}s) with result so far."
        return None

    async def run(self) -> str:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.append({"role": "user", "content": self.user_message})
        tool_defs = self.tools.get_defs(self.role.tools)

        while True:
            limit_msg = self._check_limits()
            if limit_msg:
                self.log.append({"event": "limit", "message": limit_msg})
                return limit_msg

            try:
                resp = await self.client.chat(
                    messages, model=self.role.model or None,
                    tools=tool_defs or None, temperature=self.role.temperature,
                )
            except Exception as e:
                self.log.append({"event": "provider_error", "error": str(e)})
                return f"ERROR: provider call failed: {e}"

            self.requests += 1
            if resp.usage:
                self.total_tokens += resp.usage.get("total_tokens", 0)

            if not resp.tool_calls:
                text = resp.content or "(empty)"
                self.log.append({"event": "final_text", "text": text[:2000]})
                return text

            assistant_msg: dict = {
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in resp.tool_calls
                ],
            }
            messages.append(assistant_msg)

            for tc in resp.tool_calls:
                result = await self.tools.execute(tc.name, tc.arguments)
                if len(result) > 30000:
                    result = result[:20000] + "\n… (truncated)\n" + result[-8000:]
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": result,
                })
                self.log.append({"event": "tool", "tool": tc.name, "args": tc.arguments, "result": result[:500]})
                if tc.name == "finish":
                    payload = self.tools.finish_payload or {"result": result}
                    self.log.append({"event": "finish", "payload": payload})
                    return json.dumps(payload, ensure_ascii=False)

    async def close(self) -> None:
        await self.tools.close()
