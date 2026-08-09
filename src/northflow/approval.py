"""Разрешение команд от человека (синхронное ожидание через queue).

Используется SSE-потоком: когда агент хочет выполнить команду, мы
кладём событие approval_request в очередь, HTTP-хендлер пишет его
клиенту, а ответ приходит через POST /api/approve.
"""
from __future__ import annotations

import queue
import uuid
from pathlib import Path


class ApprovalManager:
    def __init__(self):
        self._pending: dict[str, queue.Queue[bool]] = {}

    def request(self, command: str) -> tuple[str, queue.Queue[bool]]:
        q: queue.Queue[bool] = queue.Queue(maxsize=1)
        token = uuid.uuid4().hex[:12]
        self._pending[token] = q
        return token, q

    def resolve(self, token: str, allowed: bool) -> bool:
        q = self._pending.pop(token, None)
        if q is None:
            return False
        q.put(allowed)
        return True


class ApprovalWaiter:
    """Колбэк, который вешает агента до ответа человека."""

    def __init__(self, manager: ApprovalManager, emit):
        self.manager = manager
        self.emit = emit

    async def __call__(self, command: str) -> bool:
        token, q = self.manager.request(command)
        self.emit("approval_request", {"token": token, "command": command})
        return await asyncio_queue_get(q)


async def asyncio_queue_get(q) -> bool:
    """Ждёт ответа в фоне, не блокируя event loop напрямую."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, q.get)
