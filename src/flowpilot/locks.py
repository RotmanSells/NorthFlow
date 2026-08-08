"""Writer-lock: один писатель на этап/проект."""
from __future__ import annotations

import json
from pathlib import Path

LOCK_FILENAME = ".flowpilot.lock"


class WriterLock:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / LOCK_FILENAME

    def acquire(self, task_id: int, stage_id: int) -> bool:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return False
            return False
        self.path.write_text(json.dumps({
            "task_id": task_id,
            "stage_id": stage_id,
            "holder": "developer",
        }, indent=2), encoding="utf-8")
        return True

    def release(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def held(self) -> bool:
        return self.path.exists()
