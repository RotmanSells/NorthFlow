"""State-модель проекта: markdown + JSON, без внешней БД."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

STATE_FILENAME = ".northflow.json"
ROADMAP_FILENAME = "roadmap.md"

PIPELINE_PHASES = (
    "idea",
    "research",
    "questions",
    "architecture",
    "critique",
    "documentation",
    "roadmap",
    "stage",
    "tasks",
    "implementation",
    "review",
    "done",
)


@dataclass
class Task:
    id: int
    title: str
    description: str
    status: str = "todo"
    stage_id: int | None = None
    files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: str = ""
    completed_at: str = ""


@dataclass
class Stage:
    id: int
    title: str
    description: str
    status: str = "todo"  # todo | in_progress | done
    tasks: list[Task] = field(default_factory=list)


@dataclass
class ProjectState:
    root: Path
    name: str = ""
    phase: str = "idea"
    stages: list[Stage] = field(default_factory=list)
    current_stage: int | None = None
    next_task_id: int = 1
    answers: dict = field(default_factory=dict)
    memory: dict = field(default_factory=dict)
    pending_questions: list = field(default_factory=list)
    pending_next_phase: str = ""
    updated_at: str = ""

    @classmethod
    def load(cls, root: Path | str) -> "ProjectState":
        root = Path(root)
        state_file = root / STATE_FILENAME
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding="utf-8"))
            stages = []
            for s in data.get("stages", []):
                tasks = [Task(**t) for t in s.get("tasks", [])]
                stages.append(Stage(
                    id=s["id"], title=s["title"], description=s.get("description", ""),
                    status=s.get("status", "todo"), tasks=tasks,
                ))
            return cls(
                root=root,
                name=data.get("name", root.name),
                phase=data.get("phase", "idea"),
                stages=stages,
                current_stage=data.get("current_stage"),
                next_task_id=data.get("next_task_id", 1),
                answers=data.get("answers", {}),
                memory=data.get("memory", {}),
                pending_questions=data.get("pending_questions", []),
                pending_next_phase=data.get("pending_next_phase", ""),
                updated_at=data.get("updated_at", ""),
            )
        return cls(root=root, name=root.name)

    def save(self) -> None:
        from datetime import datetime, timezone
        self.updated_at = datetime.now(timezone.utc).isoformat()
        data = {
            "name": self.name,
            "phase": self.phase,
            "stages": [
                {
                    "id": s.id,
                    "title": s.title,
                    "description": s.description,
                    "status": s.status,
                    "tasks": [
                        {
                            "id": t.id,
                            "title": t.title,
                            "description": t.description,
                            "status": t.status,
                            "stage_id": t.stage_id,
                            "files": t.files,
                            "tests": t.tests,
                            "notes": t.notes,
                            "created_at": t.created_at,
                            "completed_at": t.completed_at,
                        }
                        for t in s.tasks
                    ],
                }
                for s in self.stages
            ],
            "current_stage": self.current_stage,
            "next_task_id": self.next_task_id,
            "answers": self.answers,
            "memory": self.memory,
            "pending_questions": self.pending_questions,
            "pending_next_phase": self.pending_next_phase,
            "updated_at": self.updated_at,
        }
        tmp = self.root / (STATE_FILENAME + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.root / STATE_FILENAME)

    def stage_by_id(self, sid: int) -> Stage | None:
        return next((s for s in self.stages if s.id == sid), None)

    def current_stage_obj(self) -> Stage | None:
        if self.current_stage is None:
            return None
        return self.stage_by_id(self.current_stage)

    def next_task(self) -> Task | None:
        for s in self.stages:
            if s.status == "in_progress":
                for t in s.tasks:
                    if t.status == "todo":
                        return t
        return None

    def phase_slug(self) -> str:
        return self.phase.replace(" ", "-").lower()


def parse_task_blocks(md: str, stage_id: int, start_id: int = 1) -> list[Task]:
    """Парсит задачи из markdown-файла этапа (блоки ## Task N)."""
    tasks: list[Task] = []
    blocks = re.split(r"(?m)^##\s+Task\s+(\d+)", md)
    # blocks: ["preamble", "1", "body1", "2", "body2", ...]
    nid = start_id
    for i in range(1, len(blocks), 2):
        num = int(blocks[i])
        body = blocks[i + 1] if i + 1 < len(blocks) else ""
        title_m = re.search(r"(?m)^###\s+Title[:\s]*(.+)$", body)
        title = title_m.group(1).strip() if title_m else f"Task {num}"
        desc_m = re.search(r"(?m)^###\s+Description[:\s]*(.*)$", body, re.DOTALL)
        files_m = re.findall(r"(?m)^-\s*`([^`]+)`", body)
        tasks.append(Task(
            id=nid,
            title=title,
            description=desc_m.group(1).strip() if desc_m else body.strip(),
            stage_id=stage_id,
            files=files_m,
        ))
        nid += 1
    return tasks


def write_roadmap(state: ProjectState) -> None:
    lines = ["# Roadmap", ""]
    for s in state.stages:
        marker = {"todo": "⬜", "in_progress": "🔄", "done": "✅"}.get(s.status, "⬜")
        lines.append(f"## {marker} Этап {s.id}: {s.title}")
        if s.description:
            lines.append(s.description)
        lines.append("")
        for t in s.tasks:
            tmarker = {"todo": "⬜", "in_progress": "🔄", "done": "✅", "blocked": "⛔"}.get(t.status, "⬜")
            lines.append(f"- {tmarker} **Задача {t.id}:** {t.title}")
            if t.notes:
                lines.append(f"  - {t.notes}")
        lines.append("")
    (state.root / ROADMAP_FILENAME).write_text("\n".join(lines), encoding="utf-8")
