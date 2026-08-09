"""Агентский цикл: инструменты, лимиты, детектор зацикливания/бесполезности."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from .config import RoleConfig
from .providers import LLMClient
from .tools import ToolExecutor


class AgentRun:
    """Выполнение одной роли до finish / лимита.

    Стоп-условия:
    - роль явно завершилась через finish;
    - модель вернула финальный текст;
    - исчерпан бюджет запросов/времени (PARTIAL);
    - сработал детектор бесполезности/зацикливания (STUCK).
    """

    LOOP_WINDOW = 6          # сколько последних сигнатур смотрим
    LOOP_THRESHOLD = 4       # столько одинаковых подряд — зацикливание
    IDLE_TOOL_LIMIT = 12     # столько вызовов подряд без полезного результата

    def __init__(
        self,
        client: LLMClient,
        role: RoleConfig,
        root: Path,
        system_prompt: str,
        user_message: str,
        tools: ToolExecutor | None = None,
        on_event=None,
        request_approval=None,
    ):
        self.client = client
        self.role = role
        self.root = root
        self.system_prompt = system_prompt
        self.user_message = user_message
        self.tools = tools or ToolExecutor(root, role=role.name, allowed_paths=role.allowed_paths)
        if self.tools is not None:
            self.tools.request_approval = request_approval
        self.on_event = on_event
        self.request_approval = request_approval
        self.requests = 0
        self.total_tokens = 0
        self.started_at = time.monotonic()
        self.log: list[dict] = []
        self._recent_signatures: list[str] = []
        self._useful_tool_count = 0
        self._useless_streak = 0
        if self.on_event:
            self._emit("start", {"role": role.name, "message": user_message[:500]})

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def _check_limits(self) -> str | None:
        if self.requests >= self.role.budget.max_requests:
            return f"PARTIAL: role request limit reached ({self.requests} requests) with result so far."
        if self.elapsed >= self.role.budget.max_seconds:
            return f"PARTIAL: role time limit reached ({int(self.elapsed)}s) with result so far."
        return None

    def _emit(self, kind: str, data: dict) -> None:
        if self.on_event:
            try:
                self.on_event(kind, data)
            except Exception:
                pass

    def _mark_tool_used(self, result: str) -> None:
        """Считаем инструмент полезным, если он что-то изменил/принёс данные, а не просто ошибся."""
        if result.startswith("Ошибка:") or result == "(нет вывода)" or result == "Роль завершена.":
            self._useless_streak += 1
        else:
            self._useless_streak = 0
            self._useful_tool_count += 1
        if self._useless_streak >= self.IDLE_TOOL_LIMIT:
            return

    def _detect_stuck(self, signature: str) -> bool:
        """Зацикливание: одни и те же вызовы подряд."""
        self._recent_signatures.append(signature)
        self._recent_signatures = self._recent_signatures[-self.LOOP_WINDOW:]
        if len(self._recent_signatures) >= self.LOOP_THRESHOLD:
            last = self._recent_signatures[-self.LOOP_THRESHOLD:]
            if len(set(last)) == 1:
                return True
        return False

    async def run(self) -> str:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.append({"role": "user", "content": self.user_message})
        tool_defs = self.tools.get_defs(self.role.tools)

        while True:
            limit_msg = self._check_limits()
            if limit_msg:
                self.log.append({"event": "limit", "message": limit_msg})
                self._emit("limit", {"message": limit_msg})
                return limit_msg
            if self._useless_streak >= self.IDLE_TOOL_LIMIT:
                msg = (
                    f"STUCK: роль не приносит результата ({self._useless_streak} "
                    "инструментов подряд без полезного эффекта)."
                )
                self.log.append({"event": "stuck", "message": msg})
                self._emit("stuck", {"message": msg})
                return msg

            self._emit("thinking", {"request": self.requests + 1})
            try:
                resp = await self.client.chat(
                    messages, model=self.role.model or None,
                    tools=tool_defs or None, temperature=self.role.temperature,
                )
            except Exception as e:
                self.log.append({"event": "provider_error", "error": str(e)})
                self._emit("error", {"message": str(e)})
                return f"ERROR: provider call failed: {e}"

            self.requests += 1
            if resp.usage:
                self.total_tokens += resp.usage.get("total_tokens", 0)

            if not resp.tool_calls:
                text = resp.content or "(empty)"
                self.log.append({"event": "final_text", "text": text[:2000]})
                self._emit("thinking", {"text": text[:2000]})
                self._emit("finish", {"text": text[:2000]})
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
                self._emit("tool", {"tool": tc.name, "args": tc.arguments})
                result = await self.tools.execute(tc.name, tc.arguments)
                if len(result) > 30000:
                    result = result[:20000] + "\n… (truncated)\n" + result[-8000:]
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": result,
                })
                is_error = result.startswith("Ошибка:") or result.startswith("Error:")
                self.log.append({"event": "tool", "tool": tc.name, "args": tc.arguments, "result": result[:500]})
                self._emit("tool_result", {"tool": tc.name, "result": result[:2000], "is_error": is_error})
                self._mark_tool_used(result)
                sig = json.dumps({"n": tc.name, "a": tc.arguments}, sort_keys=True, ensure_ascii=False)
                if self._detect_stuck(sig):
                    msg = "STUCK: роль повторяет одинаковые вызовы (зацикливание)."
                    self.log.append({"event": "stuck", "message": msg})
                    self._emit("stuck", {"message": msg})
                    return msg
                if tc.name == "finish":
                    payload = self.tools.finish_payload or {"result": result}
                    self.log.append({"event": "finish", "payload": payload})
                    self._emit("finish", {"payload": payload})
                    return json.dumps(payload, ensure_ascii=False)

    async def close(self) -> None:
        await self.tools.close()
