"""Планирование: парсинг этапов и задач из markdown, запись в state."""
from __future__ import annotations

import re
from pathlib import Path

from .state import ProjectState, Stage, Task


def parse_stages_file(md: str) -> list[dict]:
    """Парсит блоки '## Stage N: Title' → [{'id', 'title', 'description', 'tasks_md'}]."""
    stages = []
    blocks = re.split(r"(?m)^##\s+Stage\s+(\d+)[:\-]\s+(.+)$", md)
    # ["preamble", "1", "title1", "body1", "2", "title2", "body2", ...]
    for i in range(1, len(blocks), 3):
        try:
            sid = int(blocks[i])
        except ValueError:
            continue
        title = blocks[i + 1].strip()
        body = blocks[i + 2] if i + 2 < len(blocks) else ""
        # Задачи внутри этого блока
        tasks_md = body
        stages.append({"id": sid, "title": title, "body": tasks_md})
    return stages


def parse_tasks_from_stage(md: str, stage_id: int, start_id: int = 1) -> list[Task]:
    """Парсит задачи из тела этапа: '### Task N: Title' + описание + файлы."""
    tasks = []
    blocks = re.split(r"(?m)^###\s+Task\s+(\d+)[:\-]\s+(.+)$", md)
    nid = start_id
    for i in range(1, len(blocks), 3):
        try:
            num = int(blocks[i])
        except ValueError:
            continue
        title = blocks[i + 1].strip()
        body = blocks[i + 2] if i + 2 < len(blocks) else ""
        desc_m = re.search(r"(?m)^\*\*Description:\*\*\s*(.*)$", body, re.DOTALL)
        files = re.findall(r"(?m)^-\s*`([^`]+)`", body)
        tasks.append(Task(
            id=nid,
            title=title,
            description=desc_m.group(1).strip() if desc_m else body.strip(),
            stage_id=stage_id,
            files=files,
        ))
        nid += 1
    return tasks


def import_plan(state: ProjectState, stages_file: Path) -> int:
    """Импортирует этапы из stages.md: обновляет stage-задачи, возвращает число этапов."""
    md = stages_file.read_text(encoding="utf-8")
    stages = parse_stages_file(md)
    for idx, s in enumerate(stages, 1):
        existing = state.stage_by_id(idx)
        if existing is None:
            existing = Stage(id=idx, title=s["title"], description=s["body"][:500])
            state.stages.append(existing)
        else:
            existing.title = s["title"]
            existing.description = s["body"][:500]
        # Сохранить в папку этапа
        stage_dir = state.root / "stages" / f"{idx:02d}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "README.md").write_text(
            f"# Этап {idx}: {s['title']}\n\n{s['body'].strip()}\n", encoding="utf-8"
        )
    return len(stages)


def import_tasks_for_stage(state: ProjectState, stage_id: int, tasks_md: str) -> int:
    """Импортирует задачи для конкретного этапа (только ближайшие 3-5 детально)."""
    stage = state.stage_by_id(stage_id)
    if stage is None:
        return 0
    tasks = parse_tasks_from_stage(tasks_md, stage_id, start_id=state.next_task_id)
    stage.tasks = tasks
    state.next_task_id = max(state.next_task_id, max((t.id for t in tasks), default=0) + 1)
    state.current_stage = stage_id
    return len(tasks)
