"""Снимок состояния файлов до/после задачи: хэши, строки, изменения."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _hash_file(path: Path) -> str | None:
    try:
        data = path.read_bytes()
        return hashlib.sha256(data).hexdigest()
    except OSError:
        return None


def _line_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def snapshot_tree(root: Path, paths: list[str] | None = None) -> dict:
    """Снимает отпечатки файлов. Если paths пусто — все файлы проекта (без .git, .venv)."""
    root = Path(root)
    files: list[Path] = []
    if paths:
        for rel in paths:
            p = Path(rel)
            if not p.is_absolute():
                p = root / p
            if p.exists():
                files.append(p)
    else:
        skip_dirs = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
        for p in root.rglob("*"):
            if p.is_file() and not any(part in skip_dirs for part in p.relative_to(root).parts):
                files.append(p)
    snapshot = {}
    for p in files:
        rel = p.relative_to(root).as_posix()
        h = _hash_file(p)
        if h is not None:
            snapshot[rel] = {"hash": h, "lines": _line_count(p)}
    return snapshot


def diff_snapshots(before: dict, after: dict) -> dict:
    """Сравнивает два снимка: созданные, изменённые, удалённые файлы + строки."""
    created = {}
    changed = {}
    deleted = {}
    for rel, info in after.items():
        if rel not in before:
            created[rel] = info["lines"]
        elif before[rel]["hash"] != info["hash"]:
            changed[rel] = {
                "lines_before": before[rel]["lines"],
                "lines_after": info["lines"],
                "delta": info["lines"] - before[rel]["lines"],
            }
    for rel, info in before.items():
        if rel not in after:
            deleted[rel] = info["lines"]
    return {"created": created, "changed": changed, "deleted": deleted}


def save_report(root: Path, task_id: int, diff: dict, extra: dict | None = None) -> Path:
    """Сохраняет отчёт о задаче в logs/."""
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"task-{task_id}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    data = {
        "task_id": task_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "diff": diff,
        "extra": extra or {},
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
