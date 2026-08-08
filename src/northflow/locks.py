"""Writer-lock: один писатель на этап/проект + stale-таймаут."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

LOCK_FILENAME = ".northflow.lock"
STALE_SECONDS = 900  # 15 минут


class WriterLock:
    def __init__(self, root: Path, stale_seconds: int = STALE_SECONDS):
        self.root = root
        self.path = root / LOCK_FILENAME
        self.stale_seconds = stale_seconds

    def acquire(self, task_id: int, stage_id: int) -> bool:
        if self.path.exists():
            data = self._read()
            if data is None:
                # Кривой lock-файл: считаем его stale и забираем.
                self.path.unlink(missing_ok=True)
            elif not self._is_stale(data.get("created_at")):
                return False
            else:
                # Старый lock: освобождаем автоматически.
                self.path.unlink(missing_ok=True)
        self.path.write_text(json.dumps({
            "task_id": task_id,
            "stage_id": stage_id,
            "holder": "developer",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        return True

    def release(self) -> None:
        if self.path.exists():
            self.path.unlink(missing_ok=True)

    def held(self) -> bool:
        return self.path.exists()

    def _read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _is_stale(self, created_at: str | None) -> bool:
        if not created_at:
            return True
        try:
            created = datetime.fromisoformat(created_at)
        except ValueError:
            return True
        age = (datetime.now(timezone.utc) - created).total_seconds()
        return age > self.stale_seconds
