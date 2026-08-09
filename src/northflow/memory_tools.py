"""Интеграция памяти в инструменты агента: хранение/поиск + лог операций."""
from __future__ import annotations

from pathlib import Path

from .memory import MemoryDB


class MemoryTools:
    """Тонкая обёртка над MemoryDB для ToolExecutor.

    Хранит активную роль и автоматически пишет в memory_log, чтобы
    человек видел, кто и когда обращался к памяти.
    """

    def __init__(self, db: MemoryDB):
        self.db = db

    def set_role(self, role: str) -> None:
        self.db._last_role = role

    def store(self, content: str, kind: str = "fact", tags: list[str] | None = None,
              importance: float = 1.0, source_role: str = "") -> dict:
        mid = self.db.store_memory(
            content=content, kind=kind, tags=tags or [],
            importance=importance, source_role=source_role or self.db._last_role,
        )
        return {"memory_id": mid, "content": content[:300]}

    def recall(self, query: str, top_k: int = 5, expand_relations: int = 1) -> dict:
        res = self.db.recall(query, top_k=top_k, expand_relations=expand_relations)
        return {"results": res, "count": len(res)}

    def related(self, memory_id: int, limit: int = 5) -> dict:
        res = self.db.related(memory_id, limit=limit)
        return {"results": res, "count": len(res)}

    def list_log(self, limit: int = 50) -> list[dict]:
        return self.db.list_memory_log(limit=limit)

    def get_log(self, log_id: int) -> dict | None:
        return self.db.get_memory_log(log_id)


def open_memory(root: Path | str, embedder=None) -> MemoryDB:
    """Открывает/создаёт memory.db в корне проекта."""
    return MemoryDB(Path(root) / "memory.db", embedder=embedder)
