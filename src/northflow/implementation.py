"""Полный цикл задачи: preflight → снимок → lock → developer → проверки → review → commit → отчёт."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from .agent import AgentRun
from .checks import PreflightError, preflight, scan_files_for_forbidden
from .config import RuntimeConfig
from .locks import WriterLock
from .roles import ROLE_PROMPTS
from .snapshot import diff_snapshots, save_report, snapshot_tree
from .state import ProjectState, Task, write_roadmap
from .tools import ToolExecutor

DEFAULT_CHECK_COMMANDS = [
    ("lint", "ruff check ."),
    ("format", "ruff format --check ."),
    ("test", "pytest -q"),
]

MAX_FIX_CYCLES = 3


class TaskEngine:
    def __init__(self, state: ProjectState, cfg: RuntimeConfig, client):
        self.state = state
        self.cfg = cfg
        self.client = client
        self.root = state.root

    def run_task(self, task: Task, expected_branch: str = "main", run_checks: bool = True, on_event=None, request_approval=None) -> dict:
        errs = preflight(self.root, expected_branch=expected_branch, allow_dirty=True)
        if errs and expected_branch:
            raise PreflightError("PREFLIGHT FAIL:\n" + "\n".join(errs))

        lock = WriterLock(self.root)
        if not lock.acquire(task.id, task.stage_id or 0):
            raise PreflightError("Writer lock held: another writer is active.")

        before = snapshot_tree(self.root)
        try:
            role = self.cfg.roles["developer"]
            tools = ToolExecutor(
                self.root,
                memory=self.state.memory,
                allowed_paths=role.allowed_paths,
                role="developer",
            )
            sys_prompt = ROLE_PROMPTS["developer"]["system"].format(project=self.state.name)
            user = (
                f"Задача {task.id}: {task.title}\n\n{task.description}\n\n"
                f"Разрешённые пути: {role.allowed_paths}\n"
                f"Файлы по плану: {', '.join(task.files) or '(не заданы)'}\n"
                "Если задача противоречит архитектуре — сначала вызови critical_change и не пиши код."
            )
            run = AgentRun(self.client, role, self.root, sys_prompt, user, tools, on_event=on_event, request_approval=request_approval)
            result = asyncio.run(run.run())
            meta = {"requests": run.requests, "tokens": run.total_tokens}

            after = snapshot_tree(self.root)
            diff = diff_snapshots(before, after)

            checks = {}
            if run_checks:
                for name, cmd in DEFAULT_CHECK_COMMANDS:
                    checks[name] = self._run_check(name, cmd)

            forbidden = self._scan_changed_files(diff)

            return {
                "result": result,
                "meta": meta,
                "diff": diff,
                "checks": checks,
                "forbidden": forbidden,
                "critical_change": tools.critical_change,
            }
        finally:
            lock.release()

    def _scan_changed_files(self, diff: dict) -> dict[str, list[str]]:
        changed = list(diff.get("created", {}).keys()) + list(diff.get("changed", {}).keys())
        return scan_files_for_forbidden(self.root, changed)

    def _run_check(self, name: str, cmd: str) -> str:
        try:
            res = subprocess.run(
                cmd.split(), cwd=str(self.root), capture_output=True, text=True, timeout=120,
            )
            return f"exit={res.returncode}" + (("\n" + res.stdout[:3000]) if res.stdout else "") + (("\n" + res.stderr[:3000]) if res.stderr else "")
        except Exception as e:
            return f"check failed: {e}"

    def run_review(self, task: Task, on_event=None, request_approval=None) -> dict:
        role = self.cfg.roles["reviewer"]
        tools = ToolExecutor(
            self.root,
            memory=self.state.memory,
            allowed_paths=role.allowed_paths,
            role="reviewer",
        )
        sys_prompt = ROLE_PROMPTS["reviewer"]["system"].format(project=self.state.name)
        user = f"Проверь задачу {task.id}: {task.title}\n\n{task.description}"
        run = AgentRun(self.client, role, self.root, sys_prompt, user, tools, on_event=on_event, request_approval=request_approval)
        result = asyncio.run(run.run())
        return {"result": result, "requests": run.requests, "tokens": run.total_tokens}

    def commit_task(self, task: Task) -> str:
        try:
            subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True, capture_output=True, timeout=30)
            res = subprocess.run(
                ["git", "-C", str(self.root), "commit", "-m", f"task {task.id}: {task.title}"],
                capture_output=True, text=True, timeout=30,
            )
            if res.returncode == 0:
                return "commit ok"
            if "nothing to commit" in res.stderr:
                return "nothing to commit"
            return f"commit failed: {res.stderr[:500]}"
        except Exception as e:
            return f"commit error: {e}"

    def complete_task_cycle(self, task: Task, on_event=None, request_approval=None) -> dict:
        """Полный цикл: реализация → проверки → (исправления до 3) → review → commit → roadmap."""
        cycle = {"attempts": 0, "steps": []}
        for attempt in range(1, MAX_FIX_CYCLES + 1):
            cycle["attempts"] = attempt
            if on_event:
                on_event("phase", {"name": f"реализация (попытка {attempt})"})
            out = self.run_task(task, run_checks=True, on_event=on_event, request_approval=request_approval)
            cycle["steps"].append(out)

            if out.get("critical_change"):
                task.status = "blocked"
                self.state.answers[f"critical:{task.id}"] = out["critical_change"]["question"]
                self.state.save()
                cycle["status"] = "critical_change"
                return cycle

            if out.get("forbidden"):
                # Запрещённые паттерны — сразу блок, не тратим циклы на «исправь ещё раз».
                task.status = "blocked"
                self.state.save()
                cycle["status"] = "forbidden"
                return cycle

            if self._checks_passed(out.get("checks", {})):
                break
            # Не прошли проверки — ещё один цикл разработчика.
        else:
            task.status = "blocked"
            self.state.save()
            cycle["status"] = "checks_failed"
            return cycle

        if on_event:
            on_event("phase", {"name": "review"})
        review = self.run_review(task, on_event=on_event, request_approval=request_approval)
        cycle["review"] = review
        cycle["commit"] = self.commit_task(task)
        task.status = "done"
        task.completed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        write_roadmap(self.state)
        self.state.save()
        cycle["status"] = "done"
        return cycle

    @staticmethod
    def _checks_passed(checks: dict) -> bool:
        if not checks:
            return True
        for name, out in checks.items():
            if "exit=0" not in out:
                return False
        return True
