"""Потоковые события для веб-интерфейса (SSE).

run_phase_step принимает on_event-колбэк; этот модуль запускает шаг
в фоновом потоке и отдаёт события в queue, из которой HTTP-хендлер
пишет SSE-строки клиенту.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from .approval import ApprovalManager, ApprovalWaiter
from .runner import run_phase_step


class StreamSession:
    """Запускает шаг конвейера и собирает события в потокобезопасную очередь."""

    def __init__(self, root: Path, config: str | None = None):
        self.root = Path(root)
        self.config = config
        self.q: queue.Queue[dict | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self.approvals = ApprovalManager()
        self._waiter = None

    def _on_event(self, kind: str, data: dict) -> None:
        self.q.put({"type": kind, "data": data})

    def start(self) -> None:
        self._waiter = ApprovalWaiter(self.approvals, self._on_event)
        def worker():
            try:
                result = run_phase_step(self.root, config_path=self.config, on_event=self._on_event, request_approval=self._waiter)
                self.q.put({"type": "done", "data": result})
            except Exception as e:
                self.q.put({"type": "done", "data": {"ok": False, "message": str(e)}})
            finally:
                self.q.put(None)

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def events(self):
        """Генератор: отдаёт события, включая финальный None."""
        while True:
            item = self.q.get()
            yield item
            if item is None:
                return


def sse_event(payload: dict) -> str:
    """Форматирует событие SSE."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
